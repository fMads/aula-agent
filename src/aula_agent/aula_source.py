"""Fetch new posts and messages (with attachments) from Aula into the archive and the inbox.

``fetch`` reads what is new each night; ``backfill`` reads everything Aula still
has into the archive only.

Never starts a MitID login on its own: if the saved session cannot be reused or
refreshed, :class:`LoginRequired` is raised and the user has to log in (web UI
or ``aula login`` in a terminal). That protects the MitID account from being
locked by repeated unattended login attempts.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from aula import FileTokenStorage
from aula.api_client import AulaApiClient
from aula.auth_flow import authenticate_and_create_client
from aula.models.attachment import Attachment
from aula.models.message import Message
from aula.models.pagination import PaginationResult
from aula.models.post import Post
from aula.utils.html import html_to_markdown

from . import archive, backup, contract, credentials, store
from .ledger import Ledger
from .runs import Outcome, Problem

TZ = ZoneInfo(store.TIMEZONE)
FIRST_RUN_DAYS = 30
BACKFILL_SINCE = dt.date(2000, 1, 1)  # long before anything Aula still has
MAX_MEDIA_PER_ITEM = 3  # more images than this is a photo gallery, not an invitation
MAX_BODY_CHARS = 20_000
POSTS_PER_PAGE = 30
MAX_POST_PAGES = 100  # a safety net; Aula only keeps posts for some months
MAX_THREAD_ATTEMPTS = 3  # read an incomplete thread again this many nights, then give up
CALENDAR_DAYS = 112  # how far ahead to read Aula's own calendar
CALENDAR_CHUNK_DAYS = 28  # Aula answers 403 for ranges much longer than a month
DOWNLOAD_FAILED = "Download fejlede"


class LoginRequired(Problem):
    outcome = Outcome.AULA_LOGIN
    label = "Aula-login mangler"


class BadRescueCopy(Problem):
    label = "Redningskopien kan ikke læses"


async def open_client() -> AulaApiClient:
    if not credentials.aula_session_saved():
        raise LoginRequired("Ingen gemt Aula-session.")

    def refuse_login() -> None:
        raise LoginRequired("Den gemte Aula-session er udløbet.")

    return await authenticate_and_create_client(
        credentials.aula_username(), FileTokenStorage(credentials.aula_token_file()), on_login_required=refuse_login
    )


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=TZ)


def _iso(value: dt.datetime | None) -> str | None:
    value = _aware(value)
    return value.astimezone(TZ).isoformat(timespec="minutes") if value else None


def _version(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:16]


# ----------------------------------------------------------------------------- intake


class Intake:
    """Decides which Aula items go to the agent.

    An item is handed over when it is new or changed since it was last handed
    over, and not older than ``cutoff``. If an attachment could not be
    downloaded, the item is handed over anyway and tried again on the next run;
    it is handed over a second time only once the download works.
    """

    def __init__(self, ledger: Ledger, cutoff: dt.datetime):
        self._ledger = ledger
        self._cutoff = cutoff
        self.new_keys: list[str] = []

    async def offer(
        self, key: str, version: str, when: dt.datetime | None, build: Callable[[], Awaitable[dict[str, Any]]]
    ) -> bool:
        """``when`` is the item's latest change; ``build`` makes the inbox item
        (downloading attachments) and is only called when the item may go to the agent.

        Returns False while an attachment of the item still could not be downloaded.
        """
        if when and when < self._cutoff:  # old news, also on later runs
            self._ledger.remember_old(key, version)
            return True
        before = self._ledger.seen_version(key)
        if before == version:
            return True
        item = await build()
        complete = all(a.get("note") != DOWNLOAD_FAILED for a in item.get("attachments") or [])
        partial = f"{version}~partial"
        if not complete and before == partial:  # still failing: the agent already has it
            return False
        self._ledger.mark_seen(key, version if complete else partial)
        self._ledger.add_pending(item)
        self.new_keys.append(key)
        return complete


# ----------------------------------------------------------------------------- sources

SECURE_DOCUMENT_NOTE = "Sikkert dokument – kan kun ses i Aula"


Download = Callable[[Attachment], Awaitable[bytes]]


def _from_aula(client: AulaApiClient) -> Download:
    async def download(att: Attachment) -> bytes:
        return await client.download_file(att.file.url)

    return download


async def _archive_files(
    download: Download, arch: archive.Archive, item_key: str, attachments: list[Attachment], warnings: list[str]
) -> list[archive.File]:
    """Every file and image of an item, in the archive (downloaded unless it already is)."""

    async def guarded(att: Attachment) -> bytes | None:
        try:
            return await download(att)
        except Exception as exc:  # one bad file must not stop the run
            warnings.append(f"Kunne ikke hente {att.name!r} fra {item_key}: {exc}")
            return None

    return await arch.save_files(item_key, attachments, guarded)


def _inbox_attachments(files: list[archive.File]) -> list[dict[str, Any]]:
    """What the agent is pointed at: not the images of a photo gallery."""
    gallery = sum(1 for f in files if f.kind == "image") > MAX_MEDIA_PER_ITEM
    out: list[dict[str, Any]] = []
    for f in files:
        entry: dict[str, Any] = {"name": f.name, "kind": f.kind}
        if f.kind == "secure_document":
            entry["note"] = SECURE_DOCUMENT_NOTE
        elif f.kind == "link":
            entry["url"] = f.detail
        elif f.kind == "image" and gallery:
            entry["note"] = "Ikke vedlagt (billedgalleri)"
        elif f.path:
            entry["path"] = f.path
        else:
            entry["note"] = DOWNLOAD_FAILED
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


def _children(profile) -> list[dict[str, Any]]:
    return [{"id": c.id, "name": c.name, "institution": c.institution_name} for c in profile.children]


def _only_child(children: list[dict[str, Any]]) -> list[int]:
    """With one child everything is about that child; with more it has to be decided per item."""
    return [children[0]["id"]] if len(children) == 1 else []


def _thread_child_ids(thread: dict[str, Any], children: list[dict[str, Any]]) -> list[int]:
    hints = _child_hints(thread)
    return [c["id"] for c in children if c["name"] in hints] or _only_child(children)


def _update_children(cfg: dict[str, Any], profile) -> list[dict[str, Any]]:
    """Add new children to the config; keep the calendars already assigned."""
    children = _children(profile)
    for child in children:
        entry = cfg["children"].setdefault(str(child["id"]), {})
        entry["name"] = child["name"]
        entry["institution"] = child["institution"]
    store.save_config(cfg)
    return children


async def _read_posts(client, profile, warnings: list[str]) -> AsyncIterator[Post]:
    """Every post Aula has, newest first."""
    # Aula only returns posts when asked with the guardian's own institution
    # profile(s) as well as the children's, so a post cannot be attributed to a
    # child from the request; with several children the agent decides from the
    # groups/class the post is shared with.
    profile_ids = list(dict.fromkeys([*profile.institution_profile_ids, *(c.id for c in profile.children)]))
    if not profile_ids:
        return
    for offset in range(0, MAX_POST_PAGES * POSTS_PER_PAGE, POSTS_PER_PAGE):
        # Aula reads `index` as an element offset, but the library sends `page - 1`.
        posts = await client.get_posts(profile_ids, page=offset + 1, limit=POSTS_PER_PAGE)
        for post in posts:
            yield post
        if len(posts) < POSTS_PER_PAGE:
            return
    warnings.append(f"Der var flere end {MAX_POST_PAGES * POSTS_PER_PAGE} opslag; de ældste er ikke læst.")


async def _take_posts(
    client, profile, arch: archive.Archive, intake: Intake, only_child: list[int], warnings: list[str]
) -> None:
    async for post in _read_posts(client, profile, warnings):
        key = f"post:{post.id}"
        arch.save_post(post._raw or {}, child_ids=only_child)
        files = await _archive_files(_from_aula(client), arch, key, post.attachments, warnings)
        published = _aware(post.publish_at or post.timestamp)

        async def build(post=post, key=key, published=published, files=files) -> dict[str, Any]:
            return {
                "key": key,
                "type": "post",
                "child_ids": only_child,
                "title": post.title,
                "author": post.owner.full_name if post.owner else None,
                "published": _iso(published),
                "edited": _iso(post.edited_at),
                "groups": [g.get("name") for g in post.shared_with_groups if isinstance(g, dict)],
                "body": post.content_markdown[:MAX_BODY_CHARS],
                "attachments": _inbox_attachments(files),
            }

        version = _version(post.edited_at, post.content_html, len(post.attachments))
        await intake.offer(key, version, _aware(post.edited_at) or published, build)


async def _take_messages(
    client,
    ledger: Ledger,
    arch: archive.Archive,
    intake: Intake,
    since: dt.datetime,
    children: list[dict],
    warnings: list[str],
) -> None:
    threads = await client.get_all_message_threads_result(since.date())
    if not threads.complete:
        warnings.append(f"Beskedlisten blev ikke læst helt ({threads.termination_reason}).")
    listed = {str(t.get("id")): t for t in threads.items}
    retry = ledger.threads_to_retry()
    for thread_id, thread in {**retry, **listed}.items():
        last = thread.get("lastMessageDate") or thread.get("lastUpdatedDate") or ""
        if thread_id not in retry and ledger.thread_read_at(thread_id) == last:
            continue
        msgs = await client.get_all_messages_for_thread_result(thread_id)
        all_files = True
        hints = _child_hints(thread)
        child_ids = _thread_child_ids(thread, children)
        arch.save_thread(
            thread_id, msgs.items, thread=listed.get(thread_id), complete=msgs.complete, child_ids=child_ids
        )
        for raw in msgs.items:
            if raw.get("messageType") not in archive.MESSAGE_ITEM_TYPES:
                continue
            msg = Message.from_dict(raw)
            sent = _aware(msg.send_datetime)
            key = f"message:{thread_id}:{msg.id}"
            files = await _archive_files(_from_aula(client), arch, key, msg.attachments, warnings)
            all_files &= all(f.path for f in files if f.kind in ("file", "image"))

            async def build(msg=msg, key=key, sent=sent, files=files) -> dict[str, Any]:
                return {
                    "key": key,
                    "type": "message",
                    "thread_id": thread_id,
                    "child_ids": child_ids,
                    "child_hints": hints,
                    "subject": thread.get("subject"),
                    "sender": msg.sender_name,
                    "sent": _iso(sent),
                    "body": html_to_markdown(msg.content_html)[:MAX_BODY_CHARS],
                    "attachments": _inbox_attachments(files),
                }

            all_files &= await intake.offer(key, _version(msg.content_html, len(msg.attachments)), sent, build)

        if msgs.complete and all_files:
            ledger.stop_retrying_thread(thread_id)
            ledger.mark_thread_read(thread_id, last)
            continue
        # Read it again next run (messages already handed over are skipped), but not forever.
        keep = {k: thread.get(k) for k in ("id", "subject", "lastMessageDate", "lastUpdatedDate", "regardingChildren")}
        attempts = ledger.retry_thread(thread_id, keep)
        if attempts >= MAX_THREAD_ATTEMPTS:
            ledger.stop_retrying_thread(thread_id)
            ledger.mark_thread_read(thread_id, last)
            if not msgs.complete:
                warnings.append(f"Tråd {thread_id} kunne ikke læses helt efter {attempts} forsøg; giver op.")
            else:
                warnings.append(f"Tråd {thread_id}: filer kunne ikke hentes efter {attempts} forsøg; giver op.")
        elif not msgs.complete:
            warnings.append(f"Tråd {thread_id} blev ikke læst helt ({msgs.termination_reason}); prøver igen næste gang.")


async def _read_aula_calendar(client, profile, now: dt.datetime, warnings: list[str]) -> list[dict[str, Any]]:
    """What Aula's own calendar already has (not lessons), so the agent can skip duplicates."""
    if not profile.children:
        return []
    out: list[dict[str, Any]] = []
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
            out.append({"title": ev.title, "start": _iso(ev.start_datetime), "end": _iso(ev.end_datetime), "child_id": ev.belongs_to})
    except Exception as exc:
        warnings.append(f"Kunne ikke læse Aula-kalenderen: {exc}")
    return out


# ----------------------------------------------------------------------------- fetch


def _back_up(warnings: list[str]) -> None:
    """Back up the archive; a failure must not stop the nightly run from reaching ``apply``."""
    try:
        backup.take()
    except credentials.GoogleLoginRequired as exc:
        warnings.append(f"Backup af arkivet er ikke taget: {exc} Forbind Google igen.")
    except Exception as exc:
        warnings.append(f"Backup af arkivet fejlede: {exc}")


async def fetch(*, first_run_days: int = FIRST_RUN_DAYS) -> dict[str, Any]:
    """Fetch new items for the agent and write the inbox file.

    Returns a summary dict (new keys, how many wait, warnings).
    """
    cfg, ledger = store.load_config(), Ledger.load()
    now = dt.datetime.now(TZ)
    first_run = ledger.last_fetch is None
    cutoff = now - dt.timedelta(days=first_run_days)
    since = cutoff if first_run else dt.datetime.fromisoformat(ledger.last_fetch) - dt.timedelta(days=1)
    warnings: list[str] = []
    intake = Intake(ledger, cutoff)

    async with await open_client() as client:
        profile = await client.get_profile()
        children = _update_children(cfg, profile)
        only_child = _only_child(children)
        with archive.open() as arch:
            await _take_posts(client, profile, arch, intake, only_child, warnings)
            await _take_messages(client, ledger, arch, intake, since, children, warnings)
        aula_calendar = await _read_aula_calendar(client, profile, now, warnings)

    ledger.last_fetch = now.isoformat(timespec="seconds")
    ledger.save()
    _back_up(warnings)
    contract.write_inbox(ledger, cfg, aula_calendar, warnings)
    return {"new": intake.new_keys, "pending": len(ledger.pending_keys()), "warnings": warnings, "first_run": first_run}


# ----------------------------------------------------------------------------- backfill


class BackfillSource(Protocol):
    """What backfill reads: everything Aula still has, as Aula sends it.

    Read from Aula itself here; a rescue copy of the same data can stand in for it.
    """

    children: list[dict[str, Any]]

    def posts(self) -> AsyncIterator[dict[str, Any]]:
        """Every post, as Aula's raw JSON."""
        ...

    def threads(self) -> AsyncIterator[tuple[dict[str, Any], PaginationResult[dict]]]:
        """Every message thread (raw) with all its messages, system messages too."""
        ...

    async def download(self, att: Attachment) -> bytes:
        """The content of a file or image."""
        ...


class _AulaBackfill:
    def __init__(self, client: AulaApiClient, profile, warnings: list[str]):
        self._client, self._profile, self._warnings = client, profile, warnings
        self.children = _children(profile)
        self.download = _from_aula(client)

    async def posts(self) -> AsyncIterator[dict[str, Any]]:
        async for post in _read_posts(self._client, self._profile, self._warnings):
            yield post._raw or {}

    async def threads(self) -> AsyncIterator[tuple[dict[str, Any], PaginationResult[dict]]]:
        listed = await self._client.get_all_message_threads_result(BACKFILL_SINCE)
        if not listed.complete:
            self._warnings.append(f"Beskedlisten blev ikke læst helt ({listed.termination_reason}).")
        for thread in listed.items:
            yield thread, await self._client.get_all_messages_for_thread_result(str(thread.get("id")))


class _RescueBackfill:
    """A rescue copy: what Aula sent, saved in a folder as ``posts.json`` (raw posts),
    ``threads.json`` (``{thread, messages}``, raw) and ``files/<item folder>/<attachment id>-<name>``.

    ``child_albums.json`` (tagged photos) is not read yet. The children are the ones
    in the config, and the copy was taken on the date in the folder's name.
    """

    def __init__(self, folder: Path):
        date = re.search(r"\d{4}-\d{2}-\d{2}", folder.name)
        if date is None:
            raise BadRescueCopy(f"Mappens navn skal have datoen, kopien blev taget, fx rescue-2026-11-05 (ikke {folder.name!r}).")
        self.taken = dt.datetime.combine(dt.date.fromisoformat(date.group()), dt.time(), TZ)
        try:
            self._posts = json.loads((folder / "posts.json").read_text(encoding="utf-8"))
            self._threads = json.loads((folder / "threads.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BadRescueCopy(f"{folder}: {exc}") from exc
        self._files = {p.name: p for p in (folder / "files").glob("*/*") if p.is_file()}
        self.children = [
            {"id": int(cid), "name": c.get("name"), "institution": c.get("institution")}
            for cid, c in store.load_config()["children"].items()
        ]

    async def posts(self) -> AsyncIterator[dict[str, Any]]:
        for raw in self._posts:
            yield raw

    async def threads(self) -> AsyncIterator[tuple[dict[str, Any], PaginationResult[dict]]]:
        for entry in self._threads:
            yield entry["thread"], PaginationResult(items=entry["messages"], complete=True, termination_reason="exhausted")

    async def download(self, att: Attachment) -> bytes:
        file = self._files.get(archive.stored_name(att))
        if file is None:
            raise FileNotFoundError("ikke i redningskopien")
        return file.read_bytes()


@dataclass
class Backfilled:
    posts: int = 0
    threads: int = 0
    messages: int = 0
    new_versions: int = 0
    warnings: list[str] = field(default_factory=list)


async def _backfill_from(source: BackfillSource, warnings: list[str], *, at: dt.datetime | None = None) -> Backfilled:
    result = Backfilled(warnings=warnings)
    only_child = _only_child(source.children)
    with archive.open(at=at) as arch:
        async for raw in source.posts():
            post = Post.from_dict(raw)
            result.posts += 1
            result.new_versions += arch.save_post(raw, child_ids=only_child)
            await _archive_files(source.download, arch, f"post:{post.id}", post.attachments, warnings)
        async for thread, msgs in source.threads():
            thread_id = str(thread.get("id"))
            result.threads += 1
            saved = arch.save_thread(
                thread_id, msgs.items, thread=thread, complete=msgs.complete,
                child_ids=_thread_child_ids(thread, source.children),
            )
            result.messages += len(saved)
            result.new_versions += sum(saved.values())
            for raw in msgs.items:
                if raw.get("messageType") in archive.MESSAGE_ITEM_TYPES:
                    msg = Message.from_dict(raw)
                    await _archive_files(source.download, arch, f"message:{thread_id}:{msg.id}", msg.attachments, warnings)
            if not msgs.complete:
                warnings.append(f"Tråd {thread_id} blev ikke læst helt ({msgs.termination_reason}); kør backfill igen.")
    return result


async def backfill(rescue: Path | None = None) -> Backfilled:
    """Read everything Aula still has into the archive: all posts and all threads.

    With ``rescue``, a rescue copy is read instead of Aula (nothing is fetched
    from Aula): its files are copied into the archive, and its item versions are
    saved as of the date the copy was taken.

    Touches neither the inbox nor intake (``seen``, ``pending``, ``last_fetch``),
    and can be run again: what is already archived is skipped. Ends with a
    backup, like ``fetch``; a failed backup is a warning.
    """
    warnings: list[str] = []
    if rescue is not None:
        copy = _RescueBackfill(rescue)
        result = await _backfill_from(copy, warnings, at=copy.taken)
    else:
        async with await open_client() as client:
            profile = await client.get_profile()
            result = await _backfill_from(_AulaBackfill(client, profile, warnings), warnings)
    _back_up(warnings)
    return result
