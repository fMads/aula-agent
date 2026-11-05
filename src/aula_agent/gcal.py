"""Put events into Google Calendar: one calendar per child, attachments via Drive.

``sync`` first makes a plan (create / update / delete / skip per event and
child) from the events document and the ledger, then carries it out through a
:class:`Google` adapter. Tests use an in-memory adapter with the same methods.
"""

from __future__ import annotations

import datetime as dt
import mimetypes
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from . import contract, credentials, store
from .contract import Event, EventError, EventsDoc  # noqa: F401  (EventError re-exported)
from .credentials import GoogleLoginRequired  # noqa: F401  (re-exported)
from .ledger import Ledger, SyncedEntry

KEY_PROP = "aulaAgentKey"  # private extended property holding the event key


# ----------------------------------------------------------------------------- adapter


class Google:
    """Google Calendar + Drive, reduced to what aula-agent needs."""

    def __init__(self, calendar_service, drive_service):
        self._cal = calendar_service
        self._drive = drive_service

    @classmethod
    def connect(cls) -> Google:
        """Raises :class:`GoogleLoginRequired` when there is no usable Google access."""
        return cls(*credentials.google_services())

    def create_calendar(self, summary: str) -> str:
        return self._cal.calendars().insert(body={"summary": summary, "timeZone": store.TIMEZONE}).execute()["id"]

    def find_event(self, calendar_id: str, key: str) -> str | None:
        found = self._cal.events().list(calendarId=calendar_id, privateExtendedProperty=f"{KEY_PROP}={key}").execute()
        items = found.get("items") or []
        return items[0]["id"] if items else None

    def insert_event(self, calendar_id: str, body: dict[str, Any]) -> tuple[str, str | None]:
        saved = self._cal.events().insert(calendarId=calendar_id, body=body, supportsAttachments=True).execute()
        return saved["id"], saved.get("htmlLink")

    def patch_event(self, calendar_id: str, event_id: str, body: dict[str, Any]) -> tuple[str, str | None]:
        saved = self._cal.events().patch(
            calendarId=calendar_id, eventId=event_id, body=body, supportsAttachments=True
        ).execute()
        return saved["id"], saved.get("htmlLink")

    def delete_event(self, calendar_id: str, event_id: str) -> bool:
        """False when the event was already gone."""
        from googleapiclient.errors import HttpError

        try:
            self._cal.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        except HttpError as exc:
            if exc.resp.status in (404, 410):
                return False
            raise
        return True

    def create_folder(self, name: str) -> str:
        body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        return self._drive.files().create(body=body, fields="id").execute()["id"]

    def upload_file(self, path: Path, name: str, folder_id: str) -> dict[str, Any]:
        """Upload a file; returns a Calendar attachment (fileId, fileUrl, title, mimeType)."""
        from googleapiclient.http import MediaFileUpload

        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        meta = self._drive.files().create(
            body={"name": name, "parents": [folder_id]},
            media_body=MediaFileUpload(str(path), mimetype=mime),
            fields="id,name,mimeType,webViewLink",
        ).execute()
        return {"fileId": meta["id"], "fileUrl": meta["webViewLink"], "title": meta["name"], "mimeType": meta["mimeType"]}

    def replace_file(self, file_id: str, path: Path) -> bool:
        """Replace the content of a file this tool uploaded earlier (name and folder stay).

        False when the file is gone from Drive or in the trash (itself or its folder).
        """
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            meta = self._drive.files().update(
                fileId=file_id, media_body=MediaFileUpload(str(path), mimetype=mime), fields="id,trashed"
            ).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                return False
            raise
        return not meta.get("trashed")


# ----------------------------------------------------------------------------- event body


def _source_footer(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    if item.get("type") == "post":
        what = f"opslag »{item.get('title') or ''}«"
        who, when = item.get("author"), item.get("published")
    else:
        what = f"besked »{item.get('subject') or ''}«"
        who, when = item.get("sender"), item.get("sent")
    parts = [f"Fra Aula: {what}"]
    if who:
        parts.append(f"af {who}")
    if when:
        parts.append(f"({when[:10]})")
    return " ".join(parts)


def build_event_body(ev: Event, source_item: dict[str, Any] | None = None, attachments: list[dict] | None = None) -> dict[str, Any]:
    """The full Google Calendar body for an event.

    Every field aula-agent owns is always present (also when empty), so an
    update clears what a correction removed.
    """
    if ev.all_day:
        start = {"date": ev.start.isoformat()}
        end = {"date": (ev.end + dt.timedelta(days=1)).isoformat()}  # Google's end date is exclusive
    else:
        start = {"dateTime": ev.start.isoformat(), "timeZone": store.TIMEZONE}
        end = {"dateTime": ev.end.isoformat(), "timeZone": store.TIMEZONE}
    description = "\n\n".join(p for p in (ev.description, _source_footer(source_item), "Oprettet automatisk af aula-agent") if p)
    return {
        "summary": ev.title,
        "start": start,
        "end": end,
        "location": ev.location,
        "description": description,
        "attachments": attachments or [],
        "extendedProperties": {"private": {KEY_PROP: ev.key}},
    }


# ----------------------------------------------------------------------------- plan


@dataclass(frozen=True)
class Action:
    kind: Literal["create", "update", "delete", "skip"]
    event: Event
    child_id: str | None = None
    calendar_id: str | None = None  # None: the child's calendar does not exist yet
    event_id: str | None = None  # known from the ledger
    reason: str = ""

    def label(self, cfg: dict[str, Any]) -> str:
        what = "aflyst" if self.event.cancelled else self.event.start_text
        text = f"{what} {self.event.title or self.event.key}"
        if self.child_id:
            text += f" → {(cfg['children'].get(self.child_id) or {}).get('name') or self.child_id}"
        return f"{text} ({self.reason})" if self.reason else text


def plan(doc: EventsDoc, ledger: Ledger, cfg: dict[str, Any]) -> list[Action]:
    """What ``sync`` would do, decided from local knowledge only (no Google calls).

    An event lists every child it is for, so a child whose calendar has it but
    who is no longer listed loses it. A cancellation removes it for everyone.
    """
    actions: list[Action] = []
    for ev in doc.events:
        synced = {str(e.child_id): e for e in ledger.entries(ev.key)}
        if ev.cancelled:
            targets = list(dict.fromkeys([*synced, *map(str, ev.child_ids)]))
            if not targets:
                actions.append(Action("skip", ev, reason="ikke i kalenderen"))
            for cid in targets:
                known = synced.get(cid)
                calendar_id = known.calendar_id if known else cfg["children"][cid].get("calendar_id")
                if not calendar_id or (not known and ledger.is_removed(ev.key, cid)):
                    actions.append(Action("skip", ev, cid, reason="ikke i kalenderen"))
                else:
                    actions.append(Action("delete", ev, cid, calendar_id, known.event_id if known else None))
            continue
        listed = list(dict.fromkeys(map(str, ev.child_ids)))
        for cid in listed:
            if ledger.is_removed(ev.key, cid):
                actions.append(Action("skip", ev, cid, reason="fjernet af dig"))
                continue
            calendar_id = cfg["children"][cid].get("calendar_id")
            known = ledger.synced(ev.key, calendar_id) if calendar_id else None
            event_id = known.event_id if known else None
            actions.append(Action("update" if known else "create", ev, cid, calendar_id, event_id))
        for cid, known in synced.items():
            if cid not in listed:
                actions.append(Action("delete", ev, cid, known.calendar_id, known.event_id, reason="ikke længere med"))
    return actions


# ----------------------------------------------------------------------------- execute


def ensure_calendar(google: Google, cfg: dict[str, Any], child_id: str) -> str:
    child = cfg["children"][child_id]
    if child.get("calendar_id"):
        return child["calendar_id"]
    first_name = (child.get("name") or "").split() or [child_id]
    summary = cfg["calendar_name_template"].format(name=first_name[0])
    child["calendar_id"] = google.create_calendar(summary)
    child["calendar_name"] = summary
    store.save_config(cfg)
    return child["calendar_id"]


def _upload(google: Google, ledger: Ledger, cfg: dict[str, Any], rel_path: str) -> dict[str, Any]:
    if cached := ledger.upload(rel_path):
        return cached
    if not ledger.drive_folder_id:
        ledger.drive_folder_id = google.create_folder(cfg["drive_folder_name"])
        ledger.save()
    path = store.DATA_DIR / rel_path
    info = google.upload_file(path, path.name.split("-", 1)[-1], ledger.drive_folder_id)  # drop the Aula id prefix
    ledger.record_upload(rel_path, info)
    ledger.save()
    return info


def _source_item(ev: Event, ledger: Ledger) -> dict[str, Any]:
    item = ledger.pending_item(ev.source) or ledger.source_item_for(ev.key) or {}
    return {k: v for k, v in item.items() if k not in ("body", "attachments")}


def execute(actions: list[Action], doc: EventsDoc, google: Google, ledger: Ledger, cfg: dict[str, Any]) -> dict[str, list[str]]:
    """Carry out a plan. The ledger is saved after every change, so a crash
    half-way loses nothing that already happened in Google."""
    result: dict[str, list[str]] = {"created": [], "updated": [], "deleted": [], "processed": [], "skipped": []}
    for a in actions:
        ev, label = a.event, a.label(cfg)
        if a.kind == "skip":
            result["skipped"].append(label)
            continue
        calendar_id = a.calendar_id or ensure_calendar(google, cfg, a.child_id)
        event_id = a.event_id or google.find_event(calendar_id, ev.key)  # local state may have been lost

        if a.kind == "delete":
            if event_id and google.delete_event(calendar_id, event_id):
                result["deleted"].append(label)
            ledger.forget(ev.key, calendar_id)
            ledger.save()
            continue

        source_item = _source_item(ev, ledger)
        attachments = [_upload(google, ledger, cfg, rel) for rel in ev.attachments]
        body = build_event_body(ev, source_item, attachments)
        before = ledger.synced(ev.key, calendar_id)
        if event_id and before and ("T" in before.start) == ev.all_day:
            # All-day <-> timed: Google cannot patch one into the other; replace it.
            google.delete_event(calendar_id, event_id)
            event_id, link = google.insert_event(calendar_id, body)
            result["updated"].append(label)
        elif event_id:
            event_id, link = google.patch_event(calendar_id, event_id, body)
            result["updated"].append(label)
        else:
            event_id, link = google.insert_event(calendar_id, body)
            result["created"].append(label)
        ledger.record(
            SyncedEntry(
                key=ev.key, child_id=int(a.child_id), calendar_id=calendar_id, event_id=event_id,
                title=ev.title, start=ev.start_text, end=ev.end_text, source=ev.source,
                source_item=source_item, link=link,
            )
        )
        ledger.save()

    result["processed"] = ledger.mark_processed(list(doc.processed))
    ledger.save()
    return result


def sync(
    raw_doc: str | dict[str, Any],
    *,
    dry_run: bool = False,
    google: Google | Callable[[], Google] | None = None,
    echo=print,
) -> dict[str, list[str]]:
    """Validate an events document and put it into Google Calendar.

    Raises :class:`EventError` (nothing changed) or :class:`GoogleLoginRequired`.
    With ``dry_run`` the plan is printed and nothing is changed.
    """
    cfg, ledger = store.load_config(), Ledger.load()
    doc = contract.accept(raw_doc, cfg, ledger, dry_run=dry_run, echo=echo)
    actions = plan(doc, ledger, cfg)
    if dry_run:
        names = {"create": "Opret", "update": "Opdatér", "delete": "Slet", "skip": "Spring over"}
        for a in actions:
            echo(f"[prøvekørsel] {names[a.kind]}: {a.label(cfg)}")
        echo(f"[prøvekørsel] Markeres som behandlet: {len(doc.processed)}")
        return {"created": [], "updated": [], "deleted": [], "processed": [], "skipped": []}
    google = resolve(google)
    return execute(actions, doc, google, ledger, cfg)


def remove_event(key: str, child_id: int | None = None, *, google: Google | Callable[[], Google] | None = None) -> int:
    """Delete an event from one child's calendar (``None``: every child's) and
    never put it back there.

    Returns how many calendar entries were removed. Raises KeyError when there
    is no such entry.
    """
    ledger = Ledger.load()
    entries = [e for e in ledger.entries(key) if child_id is None or e.child_id == int(child_id)]
    if not entries:
        raise KeyError(key)
    google = resolve(google)
    for e in entries:
        google.delete_event(e.calendar_id, e.event_id)
        ledger.forget(e.key, e.calendar_id)
        ledger.mark_removed(key, e.child_id, title=e.title, start=e.start, end=e.end)
        ledger.save()
    return len(entries)


def resolve(google: Google | Callable[[], Google] | None) -> Google:
    """The Google adapter to use: the one given (or made by the given callable), else a connection."""
    if google is None:
        return Google.connect()
    return google() if callable(google) and not isinstance(google, Google) else google
