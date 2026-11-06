"""The agent contract: ``inbox.json`` out to the agent, the events document back in.

The agent (Claude Code, Codex, ...) reads the inbox and writes an events
document; AGENTS.md describes both to the agent. This module is the code side of
that contract: it writes the inbox and turns an events document into validated
:class:`Event` objects - or rejects it with every problem listed at once, so the
agent can fix the file in one go (``apply`` exits with code 5).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from . import archive, store
from .archive import Category, Classification, Todo
from .ledger import Ledger
from .runs import Outcome, Problem


class EventError(Problem):
    outcome = Outcome.BAD_EVENTS
    label = "Events-filen har fejl"


Point = dt.date | dt.datetime


@dataclass(frozen=True)
class Event:
    """One event from the events document.

    All-day events have ``date`` start/end with ``end`` the last day (inclusive).
    Timed events have naive ``datetime`` start/end in Danish time, ``end``
    exclusive (default one hour after start).

    ``child_ids`` are all the children the event is for: a child that is left
    out of a correction loses it. A cancellation only needs ``key`` and removes
    the event for every child (``child_ids`` there is an optional hint).
    """

    key: str
    child_ids: tuple[int, ...]
    cancelled: bool = False
    title: str = ""
    start: Point | None = None
    end: Point | None = None
    location: str = ""
    description: str = ""
    attachments: tuple[str, ...] = ()  # paths relative to data/
    source: str | None = None  # item key the event came from

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, dt.datetime)

    @staticmethod
    def _text(point: Point | None) -> str:
        if isinstance(point, dt.datetime):
            return point.strftime("%Y-%m-%dT%H:%M")
        return point.isoformat() if point else ""

    @property
    def start_text(self) -> str:
        return self._text(self.start)

    @property
    def end_text(self) -> str:
        return self._text(self.end)


@dataclass(frozen=True)
class EventsDoc:
    processed: tuple[str, ...]
    events: tuple[Event, ...]
    classifications: dict[str, Classification] = field(default_factory=dict)  # item key -> its classification
    new_topics: dict[str, str] = field(default_factory=dict)  # name -> description


# ----------------------------------------------------------------------------- events in


def _point(value: Any, field: str) -> Point:
    if not isinstance(value, str):
        raise ValueError(f"{field}: ugyldig dato/tid {value!r}")
    try:
        if "T" in value:
            return dt.datetime.fromisoformat(value).replace(tzinfo=None)
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field}: ugyldig dato/tid {value!r}") from exc


def _times(raw: dict[str, Any]) -> tuple[Point, Point]:
    if not raw.get("start"):
        raise ValueError("start mangler")
    start = _point(raw["start"], "start")
    end = _point(raw["end"], "end") if raw.get("end") else None
    if isinstance(start, dt.datetime):
        if end is None:
            end = start + dt.timedelta(hours=1)
        elif not isinstance(end, dt.datetime):
            raise ValueError("end skal have klokkeslæt, når start har det")
        if end <= start:
            raise ValueError("end ligger før start")
    else:
        if end is None:
            end = start
        elif isinstance(end, dt.datetime):
            raise ValueError("end må ikke have klokkeslæt, når start er en hel dag")
        if end < start:
            raise ValueError("end ligger før start")
    return start, end


def _attachment(rel: Any) -> str:
    if not isinstance(rel, str) or not rel:
        raise ValueError(f"ugyldig vedhæftning {rel!r}")
    full = (store.DATA_DIR / rel).resolve()
    if not full.is_relative_to(store.DATA_DIR.resolve()):
        raise ValueError(f"vedhæftningen skal ligge i data/: {rel}")
    if not full.is_file():
        raise ValueError(f"filen findes ikke: {rel}")
    return rel


def _event(raw: Any, children: set[str], problems: list[str], where: str) -> Event | None:
    if not isinstance(raw, dict):
        problems.append(f"{where}: skal være et objekt")
        return None
    before = len(problems)
    key = raw.get("key")
    if not key or not isinstance(key, str):
        problems.append(f"{where}: key mangler")
    child_ids: list[int] = []
    raw_children = raw.get("child_ids")
    if raw.get("cancelled") and raw_children in (None, []):
        pass  # a cancellation is for every child that has the event
    elif not raw_children or not isinstance(raw_children, list):
        problems.append(f"{where}: child_ids mangler")
    else:
        for cid in raw_children:
            if str(cid) not in children:
                problems.append(f"{where}: ukendt barn {cid}")
            else:
                child_ids.append(int(cid))
    if raw.get("source") is not None and not isinstance(raw["source"], str):
        problems.append(f"{where}: source skal være tekst")
    if raw.get("cancelled"):
        if len(problems) > before:
            return None
        return Event(key=key, child_ids=tuple(child_ids), cancelled=True, title=raw.get("title") or "", source=raw.get("source"))

    title = (raw.get("title") or "").strip() if isinstance(raw.get("title"), str) else ""
    if not title:
        problems.append(f"{where}: title mangler")
    for name in ("location", "description"):
        if raw.get(name) is not None and not isinstance(raw[name], str):
            problems.append(f"{where}: {name} skal være tekst")
    start = end = None
    try:
        start, end = _times(raw)
    except ValueError as exc:
        problems.append(f"{where}: {exc}")
    attachments = []
    for rel in raw.get("attachments") or []:
        try:
            attachments.append(_attachment(rel))
        except ValueError as exc:
            problems.append(f"{where}: {exc}")
    if len(problems) > before:
        return None
    return Event(
        key=key,
        child_ids=tuple(child_ids),
        title=title,
        start=start,
        end=end,
        location=(raw.get("location") or "").strip(),
        description=(raw.get("description") or "").strip(),
        attachments=tuple(attachments),
        source=raw.get("source"),
    )


SUMMARY_MAX = 120
TODO_MAX = 120
MAX_TOPICS = 3


def _span(raw: Any) -> tuple[dt.date, dt.date]:
    if not isinstance(raw, dict) or not raw.get("start"):
        raise ValueError("span skal have start")
    try:
        start = dt.date.fromisoformat(raw["start"])
        end = dt.date.fromisoformat(raw["end"]) if raw.get("end") else start
    except (TypeError, ValueError) as exc:
        raise ValueError(f"span skal være datoer (YYYY-MM-DD): {raw!r}") from exc
    if end < start:
        raise ValueError("span slutter før den starter")
    return start, end


def _todo(raw: Any, *, is_reply: bool, problems: list[str], where: str) -> Todo | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        problems.append(f"{where}: todo skal være et objekt {{text, due}}")
        return None
    before = len(problems)
    if is_reply:
        problems.append(f"{where}: et svar har ingen todo")
    text = raw.get("text").strip() if isinstance(raw.get("text"), str) else ""
    if not text:
        problems.append(f"{where}: todo: text mangler")
    elif len(text) > TODO_MAX:
        problems.append(f"{where}: todo: text er {len(text)} tegn (højst {TODO_MAX})")
    due = None
    if raw.get("due") is not None:
        try:
            due = dt.date.fromisoformat(raw["due"])
        except (TypeError, ValueError):
            problems.append(f"{where}: todo: due skal være en dato (YYYY-MM-DD), ikke {raw['due']!r}")
    return Todo(text, due) if len(problems) == before else None


def _classification(
    raw: dict[str, Any], *, topics: set[str], children: set[str], sensitive: bool, problems: list[str], where: str
) -> Classification | None:
    before = len(problems)
    category = raw.get("category")
    if category not in tuple(Category):
        problems.append(f"{where}: category skal være en af {', '.join(Category)} (ikke {category!r})")
    is_reply = category == Category.SVAR
    span_start = span_end = None
    if raw.get("span") is not None:
        try:
            span_start, span_end = _span(raw["span"])
        except ValueError as exc:
            problems.append(f"{where}: {exc}")
    raw_topics = raw.get("topics") or []
    if not isinstance(raw_topics, list) or not all(isinstance(t, str) and t.strip() for t in raw_topics):
        problems.append(f"{where}: topics skal være en liste af navne")
        raw_topics = []
    item_topics = tuple(dict.fromkeys(t.strip() for t in raw_topics))
    if not (0 if is_reply else 1) <= len(item_topics) <= MAX_TOPICS:
        problems.append(f"{where}: skal have 1–{MAX_TOPICS} topics" + (" (svar må have 0)" if not is_reply else ""))
    for name in item_topics:
        if name not in topics:
            problems.append(f"{where}: ukendt topic {name!r} (genbrug et fra inbox, eller tilføj det i new_topics)")
    summary = raw.get("summary")
    if summary is not None and not isinstance(summary, str):
        problems.append(f"{where}: summary skal være tekst")
        summary = None
    summary = (summary or "").strip() or None
    if sensitive and summary:
        problems.append(f"{where}: følsom tråd – intet resumé")
    elif summary is None and not is_reply and not sensitive:
        problems.append(f"{where}: summary mangler")
    elif summary and len(summary) > SUMMARY_MAX:
        problems.append(f"{where}: summary er {len(summary)} tegn (højst {SUMMARY_MAX})")
    todo = _todo(raw.get("todo"), is_reply=is_reply, problems=problems, where=where)
    child_ids: list[int] = []
    raw_children = raw.get("child_ids") or []
    if not isinstance(raw_children, list):
        problems.append(f"{where}: child_ids skal være en liste")
        raw_children = []
    for cid in raw_children:
        if str(cid) not in children:
            problems.append(f"{where}: ukendt barn {cid}")
        else:
            child_ids.append(int(cid))
    if len(problems) > before:
        return None
    return Classification(
        Category(category), span_start, span_end, item_topics, summary, tuple(dict.fromkeys(child_ids)), todo=todo
    )


def _new_topics(raw: Any, problems: list[str]) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append("new_topics skal være et objekt {navn: beskrivelse}")
        return {}
    out: dict[str, str] = {}
    for name, description in raw.items():
        if not name.strip():
            problems.append("new_topics: et topic mangler navn")
        elif not isinstance(description, str) or not description.strip():
            problems.append(f"new_topics: {name!r} mangler en beskrivelse")
        else:
            out[name.strip()] = description.strip()
    return out


def _classifications(
    raw: Any,
    *,
    topics: set[str],
    children: set[str],
    pending: set[str],
    to_classify: set[str],
    sensitive: set[str],
    problems: list[str],
) -> dict[str, Classification]:
    if raw is None:
        return {}
    if not isinstance(raw, list):
        problems.append("classifications skal være en liste")
        return {}
    out: dict[str, Classification] = {}
    for i, entry in enumerate(raw):
        item = entry.get("item") if isinstance(entry, dict) else None
        where = f"classifications[{i}] ({item or '?'})"
        if not isinstance(entry, dict):
            problems.append(f"{where}: skal være et objekt")
            continue
        if not isinstance(item, str) or not item:
            problems.append(f"{where}: item mangler")
            continue
        if item in out:
            problems.append(f"{where}: item er klassificeret to gange")
        if item not in pending and item not in to_classify:
            problems.append(f"{where}: {item} er hverken i items eller to_classify i indbakken")
        c = _classification(
            entry, topics=topics, children=children, sensitive=item in sensitive, problems=problems, where=where
        )
        if c:
            out[item] = c
    return out


def parse(
    raw: str | dict[str, Any],
    *,
    children: Iterable[str],
    pending_keys: Iterable[str],
    topics: Iterable[str] = (),
    to_classify: Iterable[str] = (),
    sensitive_keys: Iterable[str] = (),
) -> EventsDoc:
    """Validate an events document (JSON text or already decoded).

    ``children`` are the known child ids, ``pending_keys`` the inbox items the
    agent was given - all of them must be in ``processed``, and every processed
    one needs a classification. ``to_classify`` are the unclassified items it
    was given: a classification for them is optional, and they never give
    events. ``topics`` are the topics that exist already, ``sensitive_keys``
    the items from sensitive threads (no summary). Raises :class:`EventError`
    listing every problem.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EventError(f"Events-filen er ikke gyldig JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise EventError("Events-filen skal være et JSON-objekt med \"processed\" og \"events\".")

    problems: list[str] = []
    known = {str(c) for c in children}
    raw_events = raw.get("events", [])
    raw_processed = raw.get("processed", [])
    if not isinstance(raw_events, list):
        problems.append("events skal være en liste")
        raw_events = []
    if not isinstance(raw_processed, list) or not all(isinstance(k, str) for k in raw_processed):
        problems.append("processed skal være en liste af item-keys")
        raw_processed = []

    events: list[Event] = []
    keys: set[str] = set()
    for i, raw_ev in enumerate(raw_events):
        label = (raw_ev.get("key") or raw_ev.get("title") or "?") if isinstance(raw_ev, dict) else "?"
        where = f"events[{i}] ({label})"
        if isinstance(raw_ev, dict) and isinstance(raw_ev.get("key"), str):
            if raw_ev["key"] in keys:
                problems.append(f"{where}: key bruges to gange")
            keys.add(raw_ev["key"])
        ev = _event(raw_ev, known, problems, where)
        if ev:
            events.append(ev)

    pending, queued = set(pending_keys), set(to_classify)
    for i, ev in enumerate(events):
        if ev.source in queued:
            problems.append(f"events[{i}] ({ev.key}): {ev.source} er fra to_classify og må ikke give events")
    missing = sorted(pending - set(raw_processed))
    if missing:
        problems.append(
            "processed mangler disse items fra indbakken: " + ", ".join(missing)
            + " (læs dem i data/inbox.json – den kan være hentet igen siden)"
        )

    new_topics = _new_topics(raw.get("new_topics"), problems)
    raw_classifications = raw.get("classifications")
    classifications = _classifications(
        raw_classifications,
        topics={*topics, *new_topics},
        children=known,
        pending=pending,
        to_classify=queued,
        sensitive=set(sensitive_keys),
        problems=problems,
    )
    listed = raw_classifications if isinstance(raw_classifications, list) else []
    given = {c.get("item") for c in listed if isinstance(c, dict)}
    for key in dict.fromkeys(raw_processed):
        if key in pending and key not in given:  # one given with errors is reported already
            problems.append(f"{key}: classification mangler (hvert behandlet item skal have en)")

    if problems:
        raise EventError("Events-filen har fejl:\n  " + "\n  ".join(problems))
    return EventsDoc(
        processed=tuple(raw_processed), events=tuple(events), classifications=classifications, new_topics=new_topics
    )


def _queued() -> dict[str, int | None]:
    """The inbox's ``to_classify``, as item key -> the version the agent got."""
    file = store.path("INBOX_FILE")
    if not file.exists():
        return {}
    items = json.loads(file.read_text(encoding="utf-8")).get("to_classify") or []
    return {i["key"]: i.get("archive_version") for i in items}


def accept(raw: str | dict[str, Any], cfg: dict[str, Any], ledger: Ledger, *, dry_run: bool = False, echo=print) -> EventsDoc:
    """Validate an events document against the inbox and the archive, and save its
    classifications and new topics in the archive.

    This happens before anything goes to Google, so a missing Google login does
    not lose them. A classification is saved for the item version the agent got:
    the pending item's, or the one in the inbox's ``to_classify``.
    With ``dry_run`` they are only shown. Raises :class:`EventError` (nothing saved).
    """
    pending, queued = ledger.pending_keys(), _queued()
    with archive.open() as arch:
        sensitive = [key for key in [*pending, *queued] if (item := arch.item(key)) and item.sensitive]
        doc = parse(
            raw, children=cfg["children"], pending_keys=pending, to_classify=queued,
            topics=[t.name for t in arch.topics()], sensitive_keys=sensitive,
        )
        if dry_run:
            for name in doc.new_topics:
                echo(f"[prøvekørsel] Nyt topic: {name}")
            for key, c in doc.classifications.items():
                todo = c.todo and f"todo: {c.todo.text}" + (f" ({c.todo.due.isoformat()})" if c.todo.due else "")
                echo(f"[prøvekørsel] Classification: {key} {' '.join(filter(None, (c.category, c.span_text(), todo)))}")
            return doc
        for name, description in doc.new_topics.items():
            arch.create_topic(name, description)
        for key, c in doc.classifications.items():
            version = queued[key] if key in queued else (ledger.pending_item(key) or {}).get("archive_version")
            if not arch.classify(key, c, version=version):
                echo(f"ADVARSEL: {key} findes ikke i arkivet; dens classification er ikke gemt.")
    return doc


# ----------------------------------------------------------------------------- inbox out


def write_inbox(
    ledger: Ledger,
    cfg: dict[str, Any],
    aula_calendar: list[dict[str, Any]],
    warnings: list[str],
    *,
    to_classify: list[dict[str, Any]] = (),
) -> None:
    """Write ``data/inbox.json`` for the agent (shape documented in AGENTS.md).

    ``to_classify`` are the unclassified items, in the shape of ``items``; ``apply``
    reads them back from here, as the ones the agent was given.
    """
    from zoneinfo import ZoneInfo

    today = dt.date.today().isoformat()
    with archive.open() as arch:
        topics = [{"name": t.name, "description": t.description, "items": t.items} for t in arch.topics()]
    all_ids = [int(cid) for cid in cfg["children"]]
    store.write_json(
        store.path("INBOX_FILE"),
        {
            "generated_at": dt.datetime.now(ZoneInfo(store.TIMEZONE)).isoformat(timespec="seconds"),
            "today": today,
            "children": [
                {"id": int(cid), **{k: c.get(k) for k in ("name", "institution")}} for cid, c in cfg["children"].items()
            ],
            "items": ledger.pending(),
            "to_classify": list(to_classify),
            "topics": topics,
            "existing_events": [
                {"key": u.key, "child_ids": u.child_ids, "title": u.title, "start": u.start, "end": u.end, "source": u.source}
                for u in ledger.upcoming(today)
            ],
            "removed_by_user": [
                {**r, "child_ids": all_ids if r["child_ids"] is None else r["child_ids"]}
                for r in ledger.removed_upcoming(today)
            ],
            "aula_calendar": aula_calendar,
            "warnings": warnings,
        },
    )
