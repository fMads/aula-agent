"""Fetch new posts and messages (with attachments) from Aula into the local inbox.

Never starts a MitID login on its own: if the saved session cannot be reused or
refreshed, :class:`LoginRequired` is raised and the user has to run ``aula login``
in a terminal. That protects the MitID account from being locked by repeated
unattended login attempts.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aula import FileTokenStorage
from aula.api_client import AulaApiClient
from aula.auth_flow import authenticate_and_create_client
from aula.config import DEFAULT_TOKEN_FILE
from aula.config import load_config as load_aula_config
from aula.models.attachment import Attachment
from aula.models.message import Message
from aula.utils.html import html_to_markdown

from . import store

TZ = ZoneInfo(store.TIMEZONE)
FIRST_RUN_DAYS = 30
MAX_MEDIA_PER_ITEM = 3  # more images than this is a photo gallery, not an invitation
MAX_BODY_CHARS = 20_000
CALENDAR_DAYS = 112  # how far ahead to read Aula's own calendar
CALENDAR_CHUNK_DAYS = 28  # Aula answers 403 for ranges much longer than a month


class LoginRequired(RuntimeError):
    pass


async def open_client() -> AulaApiClient:
    username = load_aula_config().get("mitid_username")
    if not username or not Path(DEFAULT_TOKEN_FILE).exists():
        raise LoginRequired("Ingen gemt Aula-session.")

    def refuse_login() -> None:
        raise LoginRequired("Den gemte Aula-session er udløbet.")

    return await authenticate_and_create_client(
        username, FileTokenStorage(DEFAULT_TOKEN_FILE), on_login_required=refuse_login
    )


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=TZ)


def _iso(value: dt.datetime | None) -> str | None:
    value = _aware(value)
    return value.astimezone(TZ).isoformat(timespec="minutes") if value else None


def _safe(name: str) -> str:
    name = re.sub(r"[^\w.\- æøåÆØÅ]+", "_", name).strip(" ._")
    return name[:120] or "fil"


def _version(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:16]


async def _download_attachments(
    client: AulaApiClient, item_key: str, attachments: list[Attachment], warnings: list[str]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    media_count = sum(1 for a in attachments if a.media is not None)
    folder = store.ATTACHMENTS_DIR / _safe(item_key.replace(":", "_"))
    for att in attachments:
        entry: dict[str, Any] = {"name": att.name}
        if att.document is not None:
            entry["kind"] = "secure_document"
            entry["note"] = "Sikkert dokument – kan kun ses i Aula"
        elif att.link is not None:
            entry["kind"] = "link"
            entry["url"] = att.link.url
        elif att.file is not None and att.file.url:
            entry["kind"] = "image" if att.media is not None else "file"
            if att.media is not None and media_count > MAX_MEDIA_PER_ITEM:
                entry["note"] = "Ikke hentet (billedgalleri)"
            else:
                path = folder / f"{att.id or att.file.id}-{_safe(att.name)}"
                if not path.exists():
                    try:
                        data = await client.download_file(att.file.url)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                    except Exception as exc:  # one bad file must not stop the run
                        warnings.append(f"Kunne ikke hente {att.name!r} fra {item_key}: {exc}")
                        entry["note"] = "Download fejlede"
                if path.exists():
                    entry["path"] = str(path.relative_to(store.DATA_DIR))
        else:
            continue
        out.append(entry)
    return out


def _child_hints(raw: dict[str, Any]) -> list[str]:
    """Names of children a message thread says it is about, when Aula tells us."""
    names: list[str] = []
    for key in ("regardingChildren", "regardingChildrenNames"):
        for entry in raw.get(key) or []:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict):
                name = entry.get("displayName") or entry.get("fullName") or entry.get("name")
                if name:
                    names.append(name)
    return names


async def fetch(*, first_run_days: int = FIRST_RUN_DAYS) -> dict[str, Any]:
    """Fetch new items into ``state['pending']`` and write the inbox file.

    Returns a summary dict (counts and warnings).
    """
    cfg = store.load_config()
    st = store.load_state()
    now = dt.datetime.now(TZ)
    first_run = st["last_fetch"] is None
    cutoff = now - dt.timedelta(days=first_run_days)
    warnings: list[str] = []
    new_keys: list[str] = []

    async with await open_client() as client:
        profile = await client.get_profile()

        # Children: keep calendar ids already assigned, add new children.
        children = []
        for child in profile.children:
            entry = cfg["children"].setdefault(str(child.id), {})
            entry["name"] = child.name
            entry["institution"] = child.institution_name
            children.append({"id": child.id, "name": child.name, "institution": child.institution_name})
        store.save_config(cfg)
        only_child = [children[0]["id"]] if len(children) == 1 else []

        def add_pending(key: str, version: str, item: dict[str, Any]) -> bool:
            if st["seen"].get(key) == version:
                return False
            st["seen"][key] = version
            existing = st["pending"].get(key)
            if existing:  # unprocessed and changed again: keep latest content
                item["child_ids"] = sorted(set(existing["child_ids"]) | set(item["child_ids"]))
            st["pending"][key] = item
            new_keys.append(key)
            return True

        # --- Posts. Aula only returns posts when asked with the guardian's own
        # institution profile(s) as well as the children's, so a post cannot be
        # attributed to a child from the request; with several children, Claude
        # decides from the groups/class the post is shared with.
        profile_ids = list(dict.fromkeys([*profile.institution_profile_ids, *(c.id for c in profile.children)]))
        posts = await client.get_posts(profile_ids, page=1, limit=30) if profile_ids else []

        for post in posts:
            key = f"post:{post.id}"
            published = _aware(post.publish_at or post.timestamp)
            latest = _aware(post.edited_at) or published
            version = _version(post.edited_at, post.content_html, len(post.attachments))
            if latest and latest < cutoff:  # old news, also on later runs
                st["seen"].setdefault(key, version)
                continue
            if st["seen"].get(key) == version:
                continue
            add_pending(
                key,
                version,
                {
                    "key": key,
                    "type": "post",
                    "child_ids": only_child,
                    "title": post.title,
                    "author": post.owner.full_name if post.owner else None,
                    "published": _iso(published),
                    "edited": _iso(post.edited_at),
                    "groups": [g.get("name") for g in post.shared_with_groups if isinstance(g, dict)],
                    "body": post.content_markdown[:MAX_BODY_CHARS],
                    "attachments": await _download_attachments(client, key, post.attachments, warnings),
                },
            )

        # --- Messages: only threads with activity since the last run.
        since = cutoff if first_run else dt.datetime.fromisoformat(st["last_fetch"]) - dt.timedelta(days=1)
        threads = await client.get_all_message_threads_result(since.date())
        if not threads.complete:
            warnings.append(f"Beskedlisten blev ikke læst helt ({threads.termination_reason}).")
        for thread in threads.items:
            thread_id = str(thread.get("id"))
            last = thread.get("lastMessageDate") or thread.get("lastUpdatedDate") or ""
            if st["threads"].get(thread_id) == last:
                continue
            msgs = await client.get_all_messages_for_thread_result(thread_id)
            if not msgs.complete:
                warnings.append(f"Tråd {thread_id} blev ikke læst helt ({msgs.termination_reason}).")
            hints = _child_hints(thread)
            hinted_ids = [c["id"] for c in children if c["name"] in hints]
            for raw in msgs.items:
                if raw.get("messageType") not in ("Message", "MessageEdited"):
                    continue
                msg = Message.from_dict(raw)
                sent = _aware(msg.send_datetime)
                key = f"message:{thread_id}:{msg.id}"
                version = _version(msg.content_html, len(msg.attachments))
                if sent and sent < cutoff:
                    st["seen"].setdefault(key, version)
                    continue
                if st["seen"].get(key) == version:
                    continue
                add_pending(
                    key,
                    version,
                    {
                        "key": key,
                        "type": "message",
                        "thread_id": thread_id,
                        "child_ids": hinted_ids or only_child,
                        "child_hints": hints,
                        "subject": thread.get("subject"),
                        "sender": msg.sender_name,
                        "sent": _iso(sent),
                        "body": html_to_markdown(msg.content_html)[:MAX_BODY_CHARS],
                        "attachments": await _download_attachments(client, key, msg.attachments, warnings),
                    },
                )
            st["threads"][thread_id] = last

        # --- What Aula's own calendar already has (not lessons), to avoid duplicates.
        aula_calendar = []
        if profile.children:
            try:
                events = []
                for offset in range(0, CALENDAR_DAYS, CALENDAR_CHUNK_DAYS):  # Aula rejects long ranges
                    start = now + dt.timedelta(days=offset)
                    end = now + dt.timedelta(days=min(offset + CALENDAR_CHUNK_DAYS, CALENDAR_DAYS) - 1)
                    events += await client.get_calendar_events([c.id for c in profile.children], start, end)
                seen_ids: set = set()
                for ev in events:
                    if ev.id in seen_ids:  # multi-day events show up in several chunks
                        continue
                    seen_ids.add(ev.id)
                    raw = ev._raw or {}
                    if raw.get("type") == "lesson" or raw.get("lesson"):
                        continue
                    aula_calendar.append(
                        {"title": ev.title, "start": _iso(ev.start_datetime), "end": _iso(ev.end_datetime), "child_id": ev.belongs_to}
                    )
            except Exception as exc:
                warnings.append(f"Kunne ikke læse Aula-kalenderen: {exc}")

    st["last_fetch"] = now.isoformat(timespec="seconds")
    store.save_state(st)
    write_inbox(st, cfg, aula_calendar, warnings)
    return {"new": new_keys, "pending": len(st["pending"]), "warnings": warnings, "first_run": first_run}


def write_inbox(st: dict[str, Any], cfg: dict[str, Any], aula_calendar: list[dict], warnings: list[str]) -> None:
    today = dt.date.today().isoformat()
    upcoming = [
        {k: v for k, v in ev.items() if k in ("key", "child_id", "title", "start", "end", "source")}
        for ev in st["events"].values()
        if (ev.get("end") or ev.get("start") or "") >= today
    ]
    store.write_json(
        store.INBOX_FILE,
        {
            "generated_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
            "today": today,
            "children": [{"id": int(cid), **{k: c.get(k) for k in ("name", "institution")}} for cid, c in cfg["children"].items()],
            "items": sorted(st["pending"].values(), key=lambda i: i.get("published") or i.get("sent") or ""),
            "existing_events": sorted(upcoming, key=lambda e: e.get("start") or ""),
            "removed_by_user": [
                {"key": k, "title": v.get("title"), "start": v.get("start")} for k, v in st["ignored"].items()
                if (v.get("start") or "") >= today
            ],
            "aula_calendar": aula_calendar,
            "warnings": warnings,
        },
    )
