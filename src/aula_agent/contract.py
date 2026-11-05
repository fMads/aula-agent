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
from dataclasses import dataclass
from typing import Any

from . import store
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


def parse(raw: str | dict[str, Any], *, children: Iterable[str], pending_keys: Iterable[str]) -> EventsDoc:
    """Validate an events document (JSON text or already decoded).

    ``children`` are the known child ids, ``pending_keys`` the inbox items the
    agent was given - all of them must be in ``processed``. Raises
    :class:`EventError` listing every problem.
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

    missing = sorted(set(pending_keys) - set(raw_processed))
    if missing:
        problems.append(
            "processed mangler disse items fra indbakken: " + ", ".join(missing)
            + " (læs dem i data/inbox.json – den kan være hentet igen siden)"
        )

    if problems:
        raise EventError("Events-filen har fejl:\n  " + "\n  ".join(problems))
    return EventsDoc(processed=tuple(raw_processed), events=tuple(events))


# ----------------------------------------------------------------------------- inbox out


def write_inbox(ledger: Ledger, cfg: dict[str, Any], aula_calendar: list[dict[str, Any]], warnings: list[str]) -> None:
    """Write ``data/inbox.json`` for the agent (shape documented in AGENTS.md)."""
    from zoneinfo import ZoneInfo

    today = dt.date.today().isoformat()
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
