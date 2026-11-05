"""The ledger: everything aula-agent remembers between runs (``data/state.json``).

Callers talk in domain terms (an item was seen, an event was synced to a child's
calendar, the user removed an event); the on-disk shape stays in here:

- ``seen``: item key -> version that was last handed to the agent
- ``threads``: Aula thread id -> ``lastMessageDate`` when it was last read completely
- ``thread_retries``: Aula thread id -> thread to read again next run, and how often it was tried
- ``pending``: item key -> inbox item the agent has not processed yet
- ``events``: ``"<event key>|<calendar id>"`` -> one synced entry: the event in one
  calendar, with the event's children that belong to it (``child_ids``). Entries from
  before joint calendars have ``child_id`` (one child) and are read as ``child_ids``.
- ``history``: event key -> the event's history: every item that created, updated
  or cancelled it (``lines``: ``item``, ``action``, ``at``), plus its last known
  title, dates and children. It stays when the event is cancelled.
- ``ignored``: event key -> event the user removed from some children's calendars
  (``child_ids``; missing = every child, as before removal was per child)
- ``uploads`` / ``drive_folder_id``: attachments already in Drive
- ``runs``: the last 50 command runs, newest last
- ``last_fetch``: when Aula was last read

Load it, change it, ``save()`` it - under :func:`store.lock` when other
processes might write at the same time.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from . import store

KEEP_RUNS = 50


def _today() -> str:
    return dt.date.today().isoformat()


def _not_over(start: str | None, end: str | None, today: str) -> bool:
    """Whether an event with these ISO dates/times is today or later (by its last day)."""
    return (end or start or "")[:10] >= today


def _legacy_outcome(run: dict[str, Any]) -> str:
    """Outcome for runs logged before outcomes existed."""
    if run.get("ok"):
        return "ok"
    summary = run.get("summary") or ""
    for prefix, outcome in (("Aula-login mangler", "aula_login"), ("Google-login mangler", "google_login")):
        if summary.startswith(prefix):
            return outcome
    return "error"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class SyncedEntry:
    """One event in one calendar, for the event's children who belong to that calendar."""

    key: str
    child_ids: list[int]
    calendar_id: str
    event_id: str
    title: str
    start: str
    end: str
    source: str | None = None
    source_item: dict[str, Any] = field(default_factory=dict)
    link: str | None = None


Action = Literal["created", "updated", "cancelled"]


@dataclass
class HistoryLine:
    """One item that changed an event: ``created``, ``updated`` or ``cancelled`` it."""

    item: str | None
    action: Action
    at: str | None  # None: synced before there was a history


@dataclass
class EventHistory:
    """An event with every item that created, updated or cancelled it, oldest first."""

    key: str
    title: str
    start: str
    end: str
    cancelled: bool
    child_ids: list[int]
    lines: list[HistoryLine]


@dataclass
class UpcomingEvent:
    """An event that is not over yet, with every child whose calendar has it."""

    key: str
    title: str
    start: str
    end: str
    child_ids: list[int]
    source: str | None
    source_item: dict[str, Any]
    link: str | None


class Ledger:
    def __init__(self, data: dict[str, Any] | None = None):
        st = data if data is not None else {}
        for name, default in (
            ("seen", {}), ("threads", {}), ("thread_retries", {}), ("pending", {}), ("events", {}), ("uploads", {}),
            ("ignored", {}), ("history", {}), ("runs", []), ("drive_folder_id", None), ("last_fetch", None),
        ):
            st.setdefault(name, default)
        for raw in st["events"].values():  # from before joint calendars: one child per entry
            if "child_ids" not in raw:
                raw["child_ids"] = [int(raw.pop("child_id"))]
        seeded = set()
        for raw in st["events"].values():  # from before event history: created by its source
            if raw["key"] not in st["history"]:
                seeded.add(raw["key"])
                st["history"][raw["key"]] = {
                    "title": raw["title"], "start": raw["start"], "end": raw["end"], "child_ids": [], "cancelled": False,
                    "lines": [{"item": raw.get("source"), "action": "created", "at": None}],
                }
        self._st = st
        for key in seeded:
            st["history"][key]["child_ids"] = self._children(key)

    @classmethod
    def load(cls) -> Ledger:
        return cls(store.read_json(store.path("STATE_FILE"), {}))

    def save(self) -> None:
        store.write_json(store.path("STATE_FILE"), self._st)

    # ------------------------------------------------------------------ intake

    @property
    def last_fetch(self) -> str | None:
        return self._st["last_fetch"]

    @last_fetch.setter
    def last_fetch(self, value: str) -> None:
        self._st["last_fetch"] = value

    def seen_version(self, item_key: str) -> str | None:
        return self._st["seen"].get(item_key)

    def mark_seen(self, item_key: str, version: str) -> None:
        self._st["seen"][item_key] = version

    def remember_old(self, item_key: str, version: str) -> None:
        """An item too old to matter: remember it, unless it was seen before."""
        self._st["seen"].setdefault(item_key, version)

    def thread_read_at(self, thread_id: str) -> str | None:
        return self._st["threads"].get(thread_id)

    def mark_thread_read(self, thread_id: str, last_message: str) -> None:
        self._st["threads"][thread_id] = last_message

    def threads_to_retry(self) -> dict[str, dict[str, Any]]:
        """Threads to read again (incomplete, or an attachment failed): id -> Aula thread."""
        return {tid: r["thread"] for tid, r in self._st["thread_retries"].items()}

    def retry_thread(self, thread_id: str, thread: dict[str, Any]) -> int:
        """Read this thread again next run; returns how many times it has been tried."""
        entry = self._st["thread_retries"].setdefault(thread_id, {"thread": thread, "attempts": 0})
        entry["thread"] = thread
        entry["attempts"] += 1
        return entry["attempts"]

    def stop_retrying_thread(self, thread_id: str) -> None:
        self._st["thread_retries"].pop(thread_id, None)

    def add_pending(self, item: dict[str, Any]) -> None:
        """Hand an item to the agent. If an older version still waits, the new one
        replaces it but keeps the children already attributed to it."""
        key = item["key"]
        waiting = self._st["pending"].get(key)
        if waiting:
            item = {**item, "child_ids": sorted(set(waiting.get("child_ids") or []) | set(item.get("child_ids") or []))}
        self._st["pending"][key] = item

    def pending(self) -> list[dict[str, Any]]:
        """Items waiting for the agent, oldest first."""
        return sorted(self._st["pending"].values(), key=lambda i: i.get("published") or i.get("sent") or "")

    def pending_item(self, item_key: str | None) -> dict[str, Any] | None:
        return self._st["pending"].get(item_key) if item_key else None

    def pending_keys(self) -> set[str]:
        return set(self._st["pending"])

    def mark_processed(self, item_keys: list[str]) -> list[str]:
        """Remove processed items from the waiting list; returns those that were waiting."""
        return [k for k in item_keys if self._st["pending"].pop(k, None) is not None]

    # ------------------------------------------------------------------ synced events

    @staticmethod
    def _slot(key: str, calendar_id: str) -> str:
        return f"{key}|{calendar_id}"

    @staticmethod
    def _entry(raw: dict[str, Any]) -> SyncedEntry:
        return SyncedEntry(**{k: v for k, v in raw.items() if k in SyncedEntry.__dataclass_fields__})

    def _entries(self) -> list[SyncedEntry]:
        return [self._entry(e) for e in self._st["events"].values()]

    def synced(self, key: str, calendar_id: str) -> SyncedEntry | None:
        raw = self._st["events"].get(self._slot(key, calendar_id))
        return self._entry(raw) if raw else None

    def entries(self, key: str) -> list[SyncedEntry]:
        """Every synced entry for one event key (one per calendar)."""
        return [e for e in self._entries() if e.key == key]

    def record(self, entry: SyncedEntry) -> None:
        raw = asdict(entry)
        raw["child_ids"] = sorted(int(c) for c in entry.child_ids)
        self._st["events"][self._slot(entry.key, entry.calendar_id)] = raw

    def forget(self, key: str, calendar_id: str) -> None:
        self._st["events"].pop(self._slot(key, calendar_id), None)

    def synced_count(self) -> int:
        return len(self._st["events"])

    def source_item_for(self, key: str) -> dict[str, Any] | None:
        """The Aula item an already synced event came from (for its description)."""
        return next((e.source_item for e in self.entries(key) if e.source_item), None)

    def upcoming(self, today: str | None = None) -> list[UpcomingEvent]:
        """Synced events that are not over, one per key, soonest first."""
        today = today or _today()
        by_key: dict[str, UpcomingEvent] = {}
        for e in self._entries():
            if not _not_over(e.start, e.end, today):
                continue
            if e.key in by_key:
                by_key[e.key].child_ids = sorted({*by_key[e.key].child_ids, *e.child_ids})
            else:
                by_key[e.key] = UpcomingEvent(
                    e.key, e.title, e.start, e.end, list(e.child_ids), e.source, e.source_item, e.link
                )
        return sorted(by_key.values(), key=lambda u: u.start)

    # ------------------------------------------------------------------ event history

    def add_history(self, key: str, action: Action, item: str | None, *, title: str = "", start: str = "", end: str = "") -> None:
        """An item created, updated or cancelled an event. A cancellation keeps the
        title, dates and children the event had; otherwise the children are those
        in its synced entries. The same item doing the same again adds no line."""
        h = self._st["history"].setdefault(key, {"title": "", "start": "", "end": "", "child_ids": [], "lines": []})
        h["cancelled"] = action == "cancelled"
        if action != "cancelled":
            h.update(title=title, start=start, end=end)
        if children := self._children(key):
            h["child_ids"] = children
        last = h["lines"][-1] if h["lines"] else {}
        if (last.get("item"), last.get("action")) != (item, action):  # not when the same document is applied again
            h["lines"].append({"item": item, "action": action, "at": _now()})

    def has_event(self, key: str) -> bool:
        """Whether the event was created and not cancelled (it may be in no calendar now)."""
        h = self._st["history"].get(key)
        return bool(self.entries(key)) or (h is not None and not h.get("cancelled"))

    def _children(self, key: str) -> list[int]:
        return sorted({c for e in self.entries(key) for c in e.child_ids})

    def _history(self, key: str, raw: dict[str, Any]) -> EventHistory:
        return EventHistory(
            key, raw["title"], raw["start"], raw["end"], raw.get("cancelled", False),
            self._children(key) or list(raw["child_ids"]), [HistoryLine(**line) for line in raw["lines"]],
        )

    def events_by_item(self, item_key: str) -> list[EventHistory]:
        """The events this item created, updated or cancelled, each with its whole history."""
        return sorted(
            (self._history(k, h) for k, h in self._st["history"].items() if any(l["item"] == item_key for l in h["lines"])),
            key=lambda h: h.start,
        )

    # ------------------------------------------------------------------ removed by the user

    def is_removed(self, key: str, child_id: int | str) -> bool:
        """Whether the user removed this event from this child's calendar."""
        removed = self._st["ignored"].get(key)
        if removed is None:
            return False
        child_ids = removed.get("child_ids")
        return child_ids is None or int(child_id) in child_ids

    def mark_removed(self, key: str, child_id: int | str, *, title: str, start: str, end: str) -> None:
        removed = self._st["ignored"].setdefault(key, {"child_ids": []})
        if removed.get("child_ids") is not None and int(child_id) not in removed["child_ids"]:
            removed["child_ids"] = sorted([*removed["child_ids"], int(child_id)])
        removed.update(title=title, start=start, end=end, removed_at=_now())

    def removed_upcoming(self, today: str | None = None) -> list[dict[str, Any]]:
        """Removed events that would still be in the future (the agent must not put
        them back in these children's calendars). ``child_ids`` None means every child."""
        today = today or _today()
        return [
            {
                "key": k, "child_ids": v.get("child_ids"),
                "title": v.get("title"), "start": v.get("start"), "end": v.get("end") or v.get("start"),
            }
            for k, v in self._st["ignored"].items()
            if _not_over(v.get("start"), v.get("end"), today)
        ]

    # ------------------------------------------------------------------ Drive uploads

    def upload(self, rel_path: str) -> dict[str, Any] | None:
        return self._st["uploads"].get(rel_path)

    def record_upload(self, rel_path: str, info: dict[str, Any]) -> None:
        self._st["uploads"][rel_path] = info

    @property
    def drive_folder_id(self) -> str | None:
        return self._st["drive_folder_id"]

    @drive_folder_id.setter
    def drive_folder_id(self, value: str) -> None:
        self._st["drive_folder_id"] = value

    # ------------------------------------------------------------------ runs

    def log_run(self, command: str, outcome: str, summary: str) -> None:
        self._st["runs"].append(
            {"at": _now(), "command": command, "ok": outcome == "ok", "outcome": outcome, "summary": summary}
        )
        self._st["runs"] = self._st["runs"][-KEEP_RUNS:]

    def runs(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Newest first. Every run has ``at``, ``command``, ``ok``, ``outcome``, ``summary``."""
        out = [{"outcome": _legacy_outcome(r), **r} for r in reversed(self._st["runs"])]
        return out[:limit] if limit else out

    def last_run(self, *commands: str) -> dict[str, Any] | None:
        return next((r for r in self.runs() if r["command"] in commands), None)
