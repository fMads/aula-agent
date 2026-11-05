"""The archive: everything aula-agent has saved from Aula, kept after Aula deletes it.

One SQLite file, ``data/archive.sqlite`` (ADR 0001). Only this module touches
it, the way only :mod:`aula_agent.ledger` touches ``state.json``. Callers talk in
domain terms (save a post, save its files, how much is archived); the schema,
the versioning rule and where files are placed stay in here.

The archive fills up independently of intake and cutoff, and never forgets:
nothing is cleaned up or marked as gone from Aula.

Version rule: an item gets a new item version only when its title, its text or
the set of its attachment ids changes. Signed URLs, comment counts, bookmarks
and the like do not count, and while there is no new version the stored raw
JSON stays as it was.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import re
import sqlite3
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aula.models.attachment import Attachment
from aula.models.message import Message
from aula.models.post import Post

from . import store

SCHEMA_VERSION = 3

# Message types that are items; the rest (RecipientsAdded, SystemReply, AutoReply,
# MessageDeleted, ...) are system messages and are only kept in the raw thread.
MESSAGE_ITEM_TYPES = ("Message", "MessageEdited", "Forward")

_SCHEMA_1 = """
CREATE TABLE items (
    key        TEXT PRIMARY KEY,   -- item key, as everywhere else: post:123, message:<thread>:<id>
    kind       TEXT NOT NULL,      -- post | message
    thread_id  TEXT,
    sensitive  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE versions (
    id         INTEGER PRIMARY KEY,
    item_key   TEXT NOT NULL REFERENCES items(key),
    hash       TEXT NOT NULL,      -- the archive's own version hash (see the version rule)
    saved_at   TEXT NOT NULL,
    title      TEXT,
    author     TEXT,
    date       TEXT,               -- published / sent
    groups     TEXT NOT NULL,      -- JSON list
    child_ids  TEXT NOT NULL,      -- JSON list
    body       TEXT NOT NULL,      -- markdown
    raw        TEXT NOT NULL       -- Aula's own JSON for the post or message
);
CREATE INDEX versions_item ON versions(item_key, id);
CREATE TABLE files (
    item_key       TEXT NOT NULL REFERENCES items(key),
    attachment_id  TEXT NOT NULL,  -- Aula's attachment id: a file is known by it, not by its URL
    kind           TEXT NOT NULL,  -- file | image | link | secure_document
    name           TEXT NOT NULL,
    detail         TEXT,           -- URL of a link, document type of a secure document
    path           TEXT,           -- relative to data/; empty until downloaded, always for links and secure documents
    backed_up      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (item_key, attachment_id)
);
"""

_SCHEMA_2 = """
CREATE TABLE threads (
    id         TEXT PRIMARY KEY,
    saved_at   TEXT NOT NULL,
    raw        TEXT NOT NULL,      -- Aula's own JSON for the thread
    messages   TEXT NOT NULL       -- the latest raw list of all its messages, system messages too
);
"""

_SCHEMA_3 = """
CREATE TABLE drive (
    name  TEXT PRIMARY KEY,        -- what aula-agent keeps in Drive for the backup: folder | archive
    id    TEXT NOT NULL            -- its Drive file id
);
CREATE TABLE backups (
    at     TEXT NOT NULL,          -- when a backup was taken
    files  INTEGER NOT NULL        -- how many files it uploaded
);
"""


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _safe(name: str) -> str:
    name = re.sub(r"[^\w.\- æøåÆØÅ]+", "_", name).strip(" ._")
    return name[:120] or "fil"


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat(timespec="minutes") if value else None


# ----------------------------------------------------------------------------- what the archive holds


@dataclass
class Version:
    """An item as it looked when it was saved."""

    saved_at: str
    title: str | None
    author: str | None
    date: str | None
    groups: list[str]
    child_ids: list[int]
    body: str
    raw: dict[str, Any]


@dataclass
class File:
    """A file, image, link or secure document attached to an item."""

    attachment_id: str
    kind: str
    name: str
    detail: str | None
    path: str | None  # relative to data/, None while it could not be downloaded


@dataclass
class Item:
    key: str
    kind: str
    thread_id: str | None
    sensitive: bool
    versions: list[Version] = field(default_factory=list)  # oldest first
    files: list[File] = field(default_factory=list)


@dataclass
class Thread:
    """A message thread as Aula last sent it, with all its messages."""

    id: str
    saved_at: str
    raw: dict[str, Any]
    messages: list[dict[str, Any]]


@dataclass
class Status:
    posts: int = 0
    messages: int = 0
    last_saved: str | None = None  # when the newest item version was saved
    last_backup: str | None = None  # when the newest backup was taken


# ----------------------------------------------------------------------------- the archive


class Archive:
    def __init__(self, db: sqlite3.Connection, at: str | None = None):
        self._db = db
        self._at = at

    def _saved_at(self) -> str:
        return self._at or _now()

    # -- write side (from fetching)

    def save_post(self, raw: dict[str, Any], *, child_ids: Sequence[int] = ()) -> bool:
        """Save a post as Aula sent it. True when it gave a new item version."""
        post = Post.from_dict(raw)
        return self._save_item(
            f"post:{post.id}",
            "post",
            raw,
            title=post.title,
            author=post.owner.full_name if post.owner else None,
            date=_iso(post.publish_at or post.timestamp),
            groups=[g.get("name") for g in post.shared_with_groups if isinstance(g, dict)],
            child_ids=list(child_ids),
            body=post.content_markdown,
            attachments=post.attachments,
        )

    def save_thread(
        self,
        thread_id: str,
        messages: list[dict[str, Any]],
        *,
        thread: dict[str, Any] | None = None,
        complete: bool = True,
        child_ids: Sequence[int] = (),
    ) -> dict[str, bool]:
        """Save a message thread with all its messages, as Aula sent them.

        ``thread`` is Aula's thread; None keeps the one already saved (a thread
        read again without being listed). The raw list of messages is replaced
        only by a ``complete`` read, so a partial read never loses messages.
        Messages of :data:`MESSAGE_ITEM_TYPES` become items marked with the
        thread's sensitivity. Returns, per item key, whether it gave a new item version.
        """
        stored = self._db.execute("SELECT raw, messages FROM threads WHERE id = ?", (thread_id,)).fetchone()
        raw_thread = thread if thread is not None else (json.loads(stored[0]) if stored else {})
        raw_messages = messages if complete or not stored else json.loads(stored[1])
        self._db.execute(
            "INSERT INTO threads (id, saved_at, raw, messages) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (id) DO UPDATE SET saved_at = excluded.saved_at, raw = excluded.raw, messages = excluded.messages",
            (thread_id, self._saved_at(), _json(raw_thread), _json(raw_messages)),
        )
        sensitive = bool(raw_thread.get("sensitive"))
        new: dict[str, bool] = {}
        for raw in messages:
            if raw.get("messageType") not in MESSAGE_ITEM_TYPES:
                continue
            msg = Message.from_dict(raw)
            key = f"message:{thread_id}:{msg.id}"
            new[key] = self._save_item(
                key,
                "message",
                raw,
                title=raw_thread.get("subject"),
                author=msg.sender_name,
                date=_iso(msg.send_datetime),
                groups=[],
                child_ids=list(child_ids),
                body=msg.content_markdown,
                attachments=msg.attachments,
                thread_id=thread_id,
                sensitive=sensitive,
            )
        return new

    def _save_item(
        self, key, kind, raw, *, title, author, date, groups, child_ids, body, attachments, thread_id=None, sensitive=False
    ) -> bool:
        version = _version_hash(title, body, attachments)
        self._db.execute(
            "INSERT INTO items (key, kind, thread_id, sensitive) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (key) DO UPDATE SET thread_id = excluded.thread_id, sensitive = excluded.sensitive",
            (key, kind, thread_id, int(sensitive)),
        )
        latest = self._db.execute(
            "SELECT hash FROM versions WHERE item_key = ? ORDER BY id DESC LIMIT 1", (key,)
        ).fetchone()
        if latest and latest[0] == version:
            return False
        self._db.execute(
            "INSERT INTO versions (item_key, hash, saved_at, title, author, date, groups, child_ids, body, raw)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, version, self._saved_at(), title, author, date, json.dumps(groups, ensure_ascii=False),
             json.dumps(child_ids), body, _json(raw)),
        )
        return True

    async def save_files(
        self, item_key: str, attachments: list[Attachment], download: Callable[[Attachment], Awaitable[bytes | None]]
    ) -> list[File]:
        """Register an item's attachments and download the files and images not stored yet.

        ``download`` gets the attachment (with Aula's current URL) and returns its
        bytes, or None when it failed; such a file stays registered without a path
        and is downloaded the next time this is called. Returns the item's
        attachments in the given order, as the archive now has them.
        """
        folder = store.path("ATTACHMENTS_DIR") / _safe(item_key.replace(":", "_"))
        out: list[File] = []
        for att in attachments:
            if att.document is not None:
                file = File(_attachment_id(att), "secure_document", att.name, att.document.document_type or None, None)
            elif att.link is not None:
                file = File(_attachment_id(att), "link", att.name, att.link.url, None)
            elif att.file is not None:
                file = File(_attachment_id(att), "image" if att.media is not None else "file", att.name, None, None)
                target = folder / stored_name(att)
                if not target.exists() and att.file.url:
                    data = await download(att)
                    if data is not None:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                if target.exists():
                    file.path = str(target.relative_to(store.DATA_DIR))
            else:
                continue
            self._db.execute(
                "INSERT INTO files (item_key, attachment_id, kind, name, detail, path) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (item_key, attachment_id) DO UPDATE SET"
                " kind = excluded.kind, name = excluded.name, detail = excluded.detail, path = excluded.path",
                (item_key, file.attachment_id, file.kind, file.name, file.detail, file.path),
            )
            out.append(file)
        return out

    # -- backup (from :mod:`aula_agent.backup`); each change is committed at once,
    # so a backup that fails half-way does not upload the same file again

    def drive_id(self, name: str) -> str | None:
        """The Drive id of the backup's ``folder`` or of its copy of the ``archive``."""
        row = self._db.execute("SELECT id FROM drive WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def remember_drive_id(self, name: str, drive_id: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO drive (name, id) VALUES (?, ?)", (name, drive_id))
        self._db.commit()

    def files_to_back_up(self) -> list[tuple[str, File]]:
        """Stored files not in the backup yet, as (item key, file)."""
        return [
            (row[0], File(*row[1:]))
            for row in self._db.execute(
                "SELECT item_key, attachment_id, kind, name, detail, path FROM files"
                " WHERE path IS NOT NULL AND NOT backed_up ORDER BY rowid"
            )
        ]

    def mark_backed_up(self, item_key: str, attachment_id: str) -> None:
        self._db.execute("UPDATE files SET backed_up = 1 WHERE item_key = ? AND attachment_id = ?", (item_key, attachment_id))
        self._db.commit()

    def copy_to(self, target: Path) -> None:
        """Write a consistent copy of the archive file (SQLite's online backup)."""
        self._db.commit()
        copy = sqlite3.connect(target)
        try:
            self._db.backup(copy)
        finally:
            copy.close()

    def record_backup(self, at: str, files: int) -> None:
        self._db.execute("INSERT INTO backups (at, files) VALUES (?, ?)", (at, files))
        self._db.commit()

    # -- read side (web, backup, tests)

    def item(self, key: str) -> Item | None:
        row = self._db.execute("SELECT key, kind, thread_id, sensitive FROM items WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        item = Item(row[0], row[1], row[2], bool(row[3]))
        for saved_at, title, author, date, groups, child_ids, body, raw in self._db.execute(
            "SELECT saved_at, title, author, date, groups, child_ids, body, raw FROM versions"
            " WHERE item_key = ? ORDER BY id",
            (key,),
        ):
            item.versions.append(
                Version(saved_at, title, author, date, json.loads(groups), json.loads(child_ids), body, json.loads(raw))
            )
        item.files = [
            File(*row)
            for row in self._db.execute(
                "SELECT attachment_id, kind, name, detail, path FROM files WHERE item_key = ? ORDER BY rowid", (key,)
            )
        ]
        return item

    def thread(self, thread_id: str) -> Thread | None:
        row = self._db.execute("SELECT id, saved_at, raw, messages FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return Thread(row[0], row[1], json.loads(row[2]), json.loads(row[3])) if row else None

    def items(self) -> list[Item]:
        return [self.item(key) for (key,) in self._db.execute("SELECT key FROM items ORDER BY key")]

    def status(self) -> Status:
        counts = dict(self._db.execute("SELECT kind, count(*) FROM items GROUP BY kind").fetchall())
        last = self._db.execute("SELECT max(saved_at) FROM versions").fetchone()[0]
        last_backup = None
        if self._db.execute("PRAGMA user_version").fetchone()[0] >= 3:  # read-only: may not be migrated yet
            last_backup = self._db.execute("SELECT max(at) FROM backups").fetchone()[0]
        return Status(
            posts=counts.get("post", 0), messages=counts.get("message", 0), last_saved=last, last_backup=last_backup
        )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _version_hash(title: str | None, body: str, attachments: list[Attachment]) -> str:
    ids = sorted(_attachment_id(a) for a in attachments)
    return hashlib.sha256(json.dumps([title or "", body, ids], ensure_ascii=False).encode()).hexdigest()[:16]


def stored_name(att: Attachment) -> str:
    """The name a file or image is stored under, in its item's folder (a rescue copy uses the same)."""
    return f"{_attachment_id(att)}-{_safe(att.name)}"


def _attachment_id(att: Attachment) -> str:
    for candidate in (att.id, att.file and att.file.id, att.document and att.document.id):
        if candidate is not None:
            return str(candidate)
    return f"name:{att.name}"


def _migrate(db: sqlite3.Connection) -> None:
    current = db.execute("PRAGMA user_version").fetchone()[0]
    if current == SCHEMA_VERSION:
        return
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"Arkivet har skema {current}, men denne version af aula-agent kender kun {SCHEMA_VERSION}.")
    if current < 1:
        db.executescript(_SCHEMA_1)
    if current < 2:
        db.executescript(_SCHEMA_2)
    if current < 3:
        db.executescript(_SCHEMA_3)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextlib.contextmanager
def open(*, at: dt.datetime | None = None) -> Iterator[Archive]:  # noqa: A001 - archive.open() reads well at the call site
    """Open the archive (creating it the first time); changes are committed on a clean exit.

    What is saved is saved as of ``at`` (default: now); a rescue copy is saved as of when it was taken.
    """
    file = store.path("ARCHIVE_FILE")
    file.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(file, timeout=30)
    try:
        with db:
            _migrate(db)
        with db:
            yield Archive(db, at.isoformat(timespec="seconds") if at else None)
    finally:
        db.close()


def status() -> Status:
    """How much the archive holds, read without creating or changing the file (for the web page)."""
    file = store.path("ARCHIVE_FILE")
    if not file.exists():
        return Status()
    db = sqlite3.connect(f"{file.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            return Status()
        return Archive(db).status()
    finally:
        db.close()
