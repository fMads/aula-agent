"""Google Calendar + Drive: one calendar per child, attachments via Drive.

Scopes are kept narrow:
- ``calendar.app.created``: only calendars this tool creates itself.
- ``drive.file``: only files this tool uploads itself.
"""

from __future__ import annotations

import datetime as dt
import json
import mimetypes
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import store

SCOPES = [
    "https://www.googleapis.com/auth/calendar.app.created",
    "https://www.googleapis.com/auth/drive.file",
]
KEY_PROP = "aulaAgentKey"
REDIRECT_URI = "http://localhost:8765/"


class GoogleLoginRequired(RuntimeError):
    pass


class EventError(ValueError):
    pass


# --------------------------------------------------------------------------- auth


def credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not store.GOOGLE_TOKEN.exists():
        raise GoogleLoginRequired("Ingen Google-adgang endnu.")
    creds = Credentials.from_authorized_user_file(str(store.GOOGLE_TOKEN), SCOPES)
    if creds.valid:
        return creds
    if creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            raise GoogleLoginRequired(f"Google-adgangen kunne ikke fornyes: {exc}") from exc
        store.GOOGLE_TOKEN.write_text(creds.to_json())
        return creds
    raise GoogleLoginRequired("Google-adgangen er udløbet.")


def interactive_login(prompt=input, echo=print) -> None:
    """OAuth for a desktop client, by pasting the redirect URL back.

    Works on a headless server too: the browser can be on any machine, and the
    "page cannot be reached" after approving is expected - its URL holds the code.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not store.GOOGLE_CLIENT_SECRET.exists():
        raise FileNotFoundError(f"Læg OAuth-nøglen fra Google Cloud her: {store.GOOGLE_CLIENT_SECRET}")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(store.GOOGLE_CLIENT_SECRET), SCOPES, redirect_uri=REDIRECT_URI
    )
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    echo("1. Åbn denne adresse i en browser og godkend adgangen:\n")
    echo(url + "\n")
    echo("2. Bagefter viser browseren en fejlside (localhost kan ikke nås) – det er forventet.")
    pasted = prompt("3. Kopiér hele adressen fra browserens adresselinje og indsæt den her: ").strip()
    code = parse_qs(urlparse(pasted).query).get("code", [pasted])[0]
    flow.fetch_token(code=code)
    store.write_json(store.GOOGLE_TOKEN, json.loads(flow.credentials.to_json()))
    store.GOOGLE_TOKEN.chmod(0o600)


def web_flow_start(redirect_uri: str) -> tuple[str, str, str]:
    """Start OAuth for the web UI. Returns (authorization_url, state, code_verifier)."""
    from google_auth_oauthlib.flow import Flow

    if not store.GOOGLE_CLIENT_SECRET.exists():
        raise FileNotFoundError(f"Læg OAuth-nøglen fra Google Cloud her: {store.GOOGLE_CLIENT_SECRET}")
    flow = Flow.from_client_secrets_file(str(store.GOOGLE_CLIENT_SECRET), SCOPES, redirect_uri=redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    return url, state, flow.code_verifier


def web_flow_finish(redirect_uri: str, state: str, code_verifier: str, code: str) -> None:
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        str(store.GOOGLE_CLIENT_SECRET), SCOPES, redirect_uri=redirect_uri, state=state, code_verifier=code_verifier
    )
    flow.fetch_token(code=code)
    store.write_json(store.GOOGLE_TOKEN, json.loads(flow.credentials.to_json()))
    store.GOOGLE_TOKEN.chmod(0o600)


def services():
    from googleapiclient.discovery import build

    creds = credentials()
    return (
        build("calendar", "v3", credentials=creds, cache_discovery=False),
        build("drive", "v3", credentials=creds, cache_discovery=False),
    )


# --------------------------------------------------------------------------- event body


def _parse_point(value: str, field: str) -> dt.date | dt.datetime:
    try:
        if "T" in value:
            return dt.datetime.fromisoformat(value)
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise EventError(f"{field}: ugyldig dato/tid {value!r}") from exc


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


def build_event_body(ev: dict[str, Any], source_item: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn one event from the events file into a Google Calendar event body.

    - ``start`` ``YYYY-MM-DD`` gives an all-day event; ``end`` is then the *last*
      day (inclusive) and defaults to ``start``.
    - ``start`` ``YYYY-MM-DDTHH:MM`` gives a timed event in Europe/Copenhagen;
      ``end`` defaults to one hour later.
    """
    title = (ev.get("title") or "").strip()
    if not title:
        raise EventError("title mangler")
    if not ev.get("start"):
        raise EventError("start mangler")
    start = _parse_point(ev["start"], "start")
    end = _parse_point(ev["end"], "end") if ev.get("end") else None

    body: dict[str, Any] = {"summary": title}
    if isinstance(start, dt.datetime):
        if end is None:
            end = start + dt.timedelta(hours=1)
        elif not isinstance(end, dt.datetime):
            raise EventError("end skal have klokkeslæt, når start har det")
        if end <= start:
            raise EventError("end ligger før start")
        body["start"] = {"dateTime": start.replace(tzinfo=None).isoformat(), "timeZone": store.TIMEZONE}
        body["end"] = {"dateTime": end.replace(tzinfo=None).isoformat(), "timeZone": store.TIMEZONE}
    else:
        if end is None:
            end = start
        elif isinstance(end, dt.datetime):
            raise EventError("end må ikke have klokkeslæt, når start er en hel dag")
        if end < start:
            raise EventError("end ligger før start")
        body["start"] = {"date": start.isoformat()}
        body["end"] = {"date": (end + dt.timedelta(days=1)).isoformat()}

    if ev.get("location"):
        body["location"] = ev["location"]
    description = (ev.get("description") or "").strip()
    footer = _source_footer(source_item)
    body["description"] = "\n\n".join(p for p in (description, footer, "Oprettet automatisk af aula-agent") if p)
    body["extendedProperties"] = {"private": {KEY_PROP: ev["key"]}}
    return body


def validate(doc: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    """Return a list of problems with an events document (empty when fine)."""
    errors: list[str] = []
    known = set(cfg["children"])
    seen: set[str] = set()
    for i, ev in enumerate(doc.get("events", [])):
        where = f"events[{i}] ({ev.get('key') or ev.get('title') or '?'})"
        if not ev.get("key"):
            errors.append(f"{where}: key mangler")
        elif ev["key"] in seen:
            errors.append(f"{where}: key bruges to gange")
        seen.add(ev.get("key"))
        child_ids = ev.get("child_ids") or []
        if not child_ids:
            errors.append(f"{where}: child_ids mangler")
        for cid in child_ids:
            if str(cid) not in known:
                errors.append(f"{where}: ukendt barn {cid}")
        if ev.get("cancelled"):
            continue
        try:
            build_event_body(ev)
        except EventError as exc:
            errors.append(f"{where}: {exc}")
        for rel in ev.get("attachments") or []:
            if not (store.DATA_DIR / rel).is_file():
                errors.append(f"{where}: filen findes ikke: {rel}")
    return errors


# --------------------------------------------------------------------------- sync


def ensure_calendar(cal_service, cfg: dict[str, Any], child_id: str) -> str:
    child = cfg["children"][child_id]
    if child.get("calendar_id"):
        return child["calendar_id"]
    summary = cfg["calendar_name_template"].format(name=child.get("name", child_id).split()[0])
    created = cal_service.calendars().insert(body={"summary": summary, "timeZone": store.TIMEZONE}).execute()
    child["calendar_id"] = created["id"]
    child["calendar_name"] = summary
    store.save_config(cfg)
    return created["id"]


def _drive_folder(drive, cfg: dict[str, Any], st: dict[str, Any]) -> str:
    if st.get("drive_folder_id"):
        return st["drive_folder_id"]
    folder = drive.files().create(
        body={"name": cfg["drive_folder_name"], "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    st["drive_folder_id"] = folder["id"]
    store.save_state(st)
    return folder["id"]


def upload(drive, cfg: dict[str, Any], st: dict[str, Any], rel_path: str) -> dict[str, Any]:
    if rel_path in st["uploads"]:
        return st["uploads"][rel_path]
    from googleapiclient.http import MediaFileUpload

    path = store.DATA_DIR / rel_path
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    name = path.name.split("-", 1)[-1]  # drop the Aula id prefix
    meta = drive.files().create(
        body={"name": name, "parents": [_drive_folder(drive, cfg, st)]},
        media_body=MediaFileUpload(str(path), mimetype=mime),
        fields="id,name,mimeType,webViewLink",
    ).execute()
    info = {"fileId": meta["id"], "fileUrl": meta["webViewLink"], "title": meta["name"], "mimeType": meta["mimeType"]}
    st["uploads"][rel_path] = info
    store.save_state(st)
    return info


def _find_existing(cal, calendar_id: str, key: str) -> str | None:
    found = cal.events().list(calendarId=calendar_id, privateExtendedProperty=f"{KEY_PROP}={key}").execute()
    items = found.get("items") or []
    return items[0]["id"] if items else None


def sync(doc: dict[str, Any], *, dry_run: bool = False, svc=None, echo=print) -> dict[str, Any]:
    cfg = store.load_config()
    st = store.load_state()
    errors = validate(doc, cfg)
    if errors:
        raise EventError("Events-filen har fejl:\n  " + "\n  ".join(errors))

    result = {"created": [], "updated": [], "deleted": [], "processed": [], "skipped": []}
    if not dry_run:
        cal, drive = svc or services()

    for ev in doc.get("events", []):
        if ev["key"] in st["ignored"]:
            result["skipped"].append(f"{ev.get('title', ev['key'])} (fjernet af dig)")
            continue
        source_item = st["pending"].get(ev.get("source")) or next(
            (e.get("source_item") for e in st["events"].values() if e["key"] == ev["key"]), None
        )
        for cid in map(str, ev["child_ids"]):
            label = f"{ev['start'] if not ev.get('cancelled') else 'aflyst'} {ev.get('title', ev['key'])} → {cfg['children'][cid].get('name')}"
            if dry_run:
                echo(f"[prøvekørsel] {label}")
                continue
            calendar_id = ensure_calendar(cal, cfg, cid)
            skey = f"{ev['key']}|{calendar_id}"
            event_id = (st["events"].get(skey) or {}).get("event_id") or _find_existing(cal, calendar_id, ev["key"])

            if ev.get("cancelled"):
                if event_id:
                    cal.events().delete(calendarId=calendar_id, eventId=event_id).execute()
                    result["deleted"].append(label)
                st["events"].pop(skey, None)
                store.save_state(st)
                continue

            body = build_event_body(ev, source_item)
            attachments = [upload(drive, cfg, st, rel) for rel in ev.get("attachments") or []]
            if attachments:
                body["attachments"] = attachments
            if event_id:
                saved = cal.events().patch(
                    calendarId=calendar_id, eventId=event_id, body=body, supportsAttachments=True
                ).execute()
                result["updated"].append(label)
            else:
                saved = cal.events().insert(calendarId=calendar_id, body=body, supportsAttachments=True).execute()
                result["created"].append(label)
            st["events"][skey] = {
                "key": ev["key"],
                "child_id": int(cid),
                "calendar_id": calendar_id,
                "event_id": saved["id"],
                "title": ev["title"],
                "start": ev["start"],
                "end": ev.get("end") or ev["start"],
                "source": ev.get("source"),
                "source_item": {k: v for k, v in (source_item or {}).items() if k not in ("body", "attachments")},
                "link": saved.get("htmlLink"),
            }
            store.save_state(st)

    if not dry_run:
        for key in doc.get("processed", []):
            if st["pending"].pop(key, None) is not None:
                result["processed"].append(key)
        store.save_state(st)
    return result


def remove_event(key: str, *, svc=None) -> int:
    """Delete an event (on every child's calendar) and never recreate this key.

    Returns how many calendar entries were deleted.
    """
    st = store.load_state()
    entries = {skey: e for skey, e in st["events"].items() if e["key"] == key}
    if not entries:
        raise KeyError(key)
    cal, _ = svc or services()
    from googleapiclient.errors import HttpError

    for skey, e in entries.items():
        try:
            cal.events().delete(calendarId=e["calendar_id"], eventId=e["event_id"]).execute()
        except HttpError as exc:
            if exc.resp.status not in (404, 410):  # already gone is fine
                raise
        del st["events"][skey]
    first = next(iter(entries.values()))
    st["ignored"][key] = {
        "title": first["title"],
        "start": first["start"],
        "removed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    store.save_state(st)
    return len(entries)
