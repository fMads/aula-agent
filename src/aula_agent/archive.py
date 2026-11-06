"""The archive: everything aula-agent has saved from Aula, kept after Aula deletes it.

One SQLite file, ``data/archive.sqlite`` (ADR 0001). Only this module touches
it, the way only :mod:`aula_agent.ledger` touches ``state.json``. Callers talk in
domain terms (save a post, save its files, how much is archived); the schema,
the versioning rule and where files are placed stay in here.

The archive fills up independently of intake and cutoff, and never forgets
what came from Aula: no item, version or file is cleaned up or marked as gone from Aula.

Besides what came from Aula, it keeps the agent's classification of each item
version (with its todo, if any) and the topics they use (ADR 0003). An item version keeps its
classification until the agent gives it a new one or ``reclassify`` deletes them all
(:meth:`Archive.forget_classifications`); a new version has none until the agent gives it one.

It also keeps a free-text index (:meth:`Archive.search`) of each item as its newest version and
newest classification have it, and of each file by its name and, for PDF and docx, the text in it
(taken out once, when the file is stored). Saving and classifying keep it up to date.

Version rule: an item gets a new item version only when its title, its text or
the set of its attachment ids changes. Signed URLs, comment counts, bookmarks
and the like do not count, and while there is no new version the stored raw
JSON stays as it was.

What is saved is saved as of a time (now, or when a rescue copy was taken), and
the latest version is the one with the newest time. Something older than what
the archive has (a rescue copy read after ``fetch``) adds a version only when
its content is not among the item's versions already, and never replaces the
stored raw thread.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import re
import sqlite3
from collections.abc import Awaitable, Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from aula.models.attachment import Attachment
from aula.models.message import Message
from aula.models.post import Post
from aula.utils.dates import parse_api_datetime

from . import filetext, store

SCHEMA_VERSION = 7
_BACKUPS_SINCE = 3  # the schema version that added the backup tables
_CLASSIFICATIONS_SINCE = 4  # the schema version that added classifications and topics
_TODOS_SINCE = 5  # the schema version that added a todo to classifications
_FIRST_CLASSIFIED_SINCE = 6  # the schema version that remembers when a version was first classified
_SEARCH_SINCE = 7  # the schema version that added the free-text index

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


_SCHEMA_4 = """
CREATE TABLE topics (
    name         TEXT PRIMARY KEY,
    description  TEXT NOT NULL,    -- one line on what belongs under it
    created_at   TEXT NOT NULL
);
CREATE TABLE classifications (
    version_id   INTEGER PRIMARY KEY REFERENCES versions(id),  -- one per item version
    category     TEXT NOT NULL,    -- arrangement | frist | periode | info | orientering | svar
    span_start   TEXT,             -- YYYY-MM-DD, both inclusive; empty when the agent gave no span
    span_end     TEXT,
    summary      TEXT,             -- empty for replies and sensitive threads
    child_ids    TEXT NOT NULL,    -- JSON list: the agent's (Aula's are on the version and win)
    saved_at     TEXT NOT NULL
);
CREATE TABLE classification_topics (
    version_id   INTEGER NOT NULL REFERENCES classifications(version_id),
    topic        TEXT NOT NULL REFERENCES topics(name),
    position     INTEGER NOT NULL, -- the agent's order
    PRIMARY KEY (version_id, topic)
);
"""

_SCHEMA_5 = """
ALTER TABLE classifications ADD COLUMN todo_text TEXT;  -- empty when there is nothing to do
ALTER TABLE classifications ADD COLUMN todo_due TEXT;   -- YYYY-MM-DD, empty without a due date
"""

# when a version got its first classification: neither a new one nor ``reclassify`` changes it,
# so whether its todo was current stays as it was
_SCHEMA_6 = """
ALTER TABLE versions ADD COLUMN first_classified_at TEXT;
UPDATE versions SET first_classified_at = (SELECT saved_at FROM classifications WHERE version_id = versions.id);
"""

# The free-text index (FTS5): each item as its newest version and newest classification has it, and
# each file by its name and the text in it. The text is taken out of a stored file once (``extracted``).
# Danish letters stay as they are (no "å" -> "a"); case does not count.
_SCHEMA_7 = """
CREATE VIRTUAL TABLE search_items USING fts5(
    item_key UNINDEXED, title, body, author, summary, topics, tokenize = 'unicode61 remove_diacritics 0'
);
CREATE VIRTUAL TABLE search_files USING fts5(
    item_key UNINDEXED, attachment_id UNINDEXED, extracted UNINDEXED, name, text,
    tokenize = 'unicode61 remove_diacritics 0'
);
"""


def _stamp(at: dt.datetime) -> str:
    return at.astimezone().isoformat(timespec="seconds") if at.tzinfo is None else at.isoformat(timespec="seconds")


def _when(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp)


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
    id: int | None = None  # the archive's handle on this version (an inbox item's ``archive_version``)
    classification: Classification | None = None
    first_classified_at: str | None = None  # kept when it is classified again, also after ``reclassify``


class Kind(StrEnum):
    """What an attachment is."""

    FILE = "file"
    IMAGE = "image"
    LINK = "link"  # into a cloud drive
    SECURE_DOCUMENT = "secure_document"  # can only be seen in Aula


@dataclass
class File:
    """A file, image, link or secure document attached to an item."""

    attachment_id: str
    kind: Kind
    name: str
    detail: str | None
    path: str | None  # relative to data/, None while it could not be downloaded

    def __post_init__(self):
        self.kind = Kind(self.kind)

    @property
    def downloadable(self) -> bool:
        """A file or an image, which the archive stores; not a link or a secure document."""
        return self.kind in (Kind.FILE, Kind.IMAGE)


class Category(StrEnum):
    """The most important kind of information in an item version (one per version)."""

    ARRANGEMENT = "arrangement"  # an event: a day (or days) something happens
    FRIST = "frist"  # a deadline: sign up, hand in
    PERIODE = "periode"  # a period: theme week, swimming for four weeks
    INFO = "info"  # standing info that holds all year: timetable, rules
    ORIENTERING = "orientering"  # a short-lived notice: lice in the class, minutes of a meeting
    SVAR = "svar"  # a reply without new information ("Tak", "Ok")


@dataclass(frozen=True)
class Todo:
    """Something the parent has to do (pay, sign up, hand in, bring), until they tick it off."""

    text: str  # short, in the imperative
    due: dt.date | None = None


@dataclass(frozen=True)
class Classification:
    """The agent's reading of one item version (the facts - sender, date, Aula's children - are the item's)."""

    category: Category
    span_start: dt.date | None = None  # when the item applies: one day or from-to (both inclusive)
    span_end: dt.date | None = None
    topics: tuple[str, ...] = ()
    summary: str | None = None  # one line; none for replies and sensitive threads
    child_ids: tuple[int, ...] = ()  # the agent's, for when Aula set none
    saved_at: str | None = None  # when the archive saved it
    todo: Todo | None = None

    def span_text(self, date_format: str = "%Y-%m-%d") -> str:
        """The span as ``start`` or ``start–end`` ("" without one)."""
        if self.span_start is None:
            return ""
        start = self.span_start.strftime(date_format)
        if self.span_end is None or self.span_end == self.span_start:
            return start
        return f"{start}–{self.span_end.strftime(date_format)}"


@dataclass(frozen=True)
class Topic:
    """A topic that ties items about the same thing together."""

    name: str
    description: str
    created_at: str
    items: int  # how many items have it in their newest classification


NOTICE_CURRENT_FOR = dt.timedelta(days=14)  # a notice is past this long after it came


@dataclass
class Item:
    key: str
    kind: str
    thread_id: str | None
    sensitive: bool
    versions: list[Version] = field(default_factory=list)  # oldest first
    files: list[File] = field(default_factory=list)

    @property
    def latest(self) -> Version:
        """The newest version (an item in the archive has at least one)."""
        return self.versions[-1]

    @property
    def classified_version(self) -> Version | None:
        """The newest version that has a classification (the newest version may not have one yet)."""
        return next((v for v in reversed(self.versions) if v.classification), None)

    @property
    def classification(self) -> Classification | None:
        """The newest classification there is."""
        return self.classified_version.classification if self.classified_version else None

    def came_out(self, version: Version) -> str | None:
        """When the item came out as this version: the first version when it was published or sent,
        a later one (a correction) when it was edited in Aula, or else when the archive saw it."""
        if version is self.versions[0]:
            return version.date
        return _iso(parse_api_datetime(version.raw.get("editedAt"))) or version.saved_at

    @property
    def child_ids(self) -> list[int]:
        """The children the item is for: Aula's when it set them, else the agent's, else none (= every child)."""
        aula = self.latest.child_ids if self.versions else []
        return list(aula or (self.classification.child_ids if self.classification else ()))

    def past(self, today: dt.date) -> bool:
        """Whether it is no longer current (then it is dimmed, never hidden), by the newest classification.

        An arrangement, a deadline or a period is past when its span is over (without a span: when the
        item's day has passed). Standing info never is. A notice, a reply or an item not classified
        yet is past :data:`NOTICE_CURRENT_FOR` after it came, or when its span is over.
        """
        c = self.classification
        if c is not None and c.category == Category.INFO:
            return False
        end = c and (c.span_end or c.span_start)
        if end is not None and end < today:
            return True
        day = dt.date.fromisoformat(self.latest.date[:10]) if self.versions and self.latest.date else None
        if c is not None and c.category in (Category.ARRANGEMENT, Category.FRIST, Category.PERIODE):
            return end is None and day is not None and day < today
        return day is not None and today - day > NOTICE_CURRENT_FOR


TODO_FRESH_FOR = dt.timedelta(days=14)  # a todo without due counts on an item at most this old


@dataclass(frozen=True)
class ItemTodo:
    """A todo as the archive has it: the one in an item's newest classification, with that item version."""

    item: Item
    version: Version  # the classified version the todo belongs to

    @property
    def text(self) -> str:
        return self.version.classification.todo.text

    @property
    def due(self) -> dt.date | None:
        return self.version.classification.todo.due

    @property
    def classified_at(self) -> str:
        """When the version was first classified (a new classification of it does not change that)."""
        return self.version.first_classified_at or self.version.classification.saved_at

    @property
    def was_current(self) -> bool:
        """Whether it mattered when it was classified: due that day or later, or no due on an item
        at most :data:`TODO_FRESH_FOR` old. One that did not (an old item from the queue or a
        backfill) starts as ticked off, so they do not fill the list."""
        on = dt.date.fromisoformat(self.classified_at[:10])
        if self.due is not None:
            return self.due >= on
        came = self.came_out
        return bool(came) and on - dt.date.fromisoformat(came[:10]) <= TODO_FRESH_FOR

    @property
    def came_out(self) -> str | None:
        """When the item came out as this version (:meth:`Item.came_out`)."""
        return self.item.came_out(self.version)


@dataclass
class Thread:
    """A message thread as Aula last sent it, with all its messages."""

    id: str
    saved_at: str
    raw: dict[str, Any]
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class Hit:
    """An item that matched a search."""

    item: Item
    excerpt: tuple[tuple[str, bool], ...] = ()  # the text around the match as (text, marked) parts; () without one
    files: tuple[File, ...] = ()  # the item's files whose name or text matched


class Source(StrEnum):
    """Where an item came from, for a search filter."""

    POST = "opslag"
    MESSAGE = "besked"
    DOCUMENT = "dokument"  # an item with a document attached (a file that is not an image, or a secure document)


@dataclass
class Status:
    posts: int = 0
    messages: int = 0
    last_saved: str | None = None  # when the newest item version was saved
    last_backup: str | None = None  # when the newest backup was taken


# ----------------------------------------------------------------------------- the archive


class Archive:
    def __init__(self, db: sqlite3.Connection, at: dt.datetime | None = None):
        self._db = db
        self._at = at

    def _now(self) -> dt.datetime:
        return self._at or dt.datetime.now().astimezone()

    def _saved_at(self) -> str:
        return _stamp(self._now())

    # -- write side (from fetching)

    def save_post(self, raw: dict[str, Any], *, child_ids: Sequence[int] = ()) -> bool:
        """Save a post as Aula sent it. True when it gave a new item version."""
        post = Post.from_dict(raw)
        return self._save_item(
            post_key(post.id),
            "post",
            raw,
            title=post.title,
            author=post_author(post),
            date=_iso(post.publish_at or post.timestamp),
            groups=post_groups(post),
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
        stored = self._db.execute("SELECT raw, messages, saved_at FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if stored and _when(stored[2]) > self._now():  # older than what is saved: keep that
            raw_thread = json.loads(stored[0])
        else:
            raw_thread = thread if thread is not None else (json.loads(stored[0]) if stored else {})
            raw_messages = messages if complete or not stored else json.loads(stored[1])
            self._db.execute(
                "INSERT INTO threads (id, saved_at, raw, messages) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET saved_at = excluded.saved_at, raw = excluded.raw, messages = excluded.messages",
                (thread_id, self._saved_at(), _json(raw_thread), _json(raw_messages)),
            )
        sensitive = bool(raw_thread.get("sensitive"))
        new: dict[str, bool] = {}
        for key, raw, msg in thread_items(thread_id, messages):
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
        saved = self._db.execute("SELECT hash, saved_at, id FROM versions WHERE item_key = ?", (key,)).fetchall()
        latest = max(saved, key=lambda row: (_when(row[1]), row[2]), default=None)  # of equal times, the last saved
        if latest and _when(latest[1]) > self._now():  # older than the latest version: only if it is not there yet
            if any(row[0] == version for row in saved):
                return False
        elif latest and latest[0] == version:
            return False
        self._db.execute(
            "INSERT INTO versions (item_key, hash, saved_at, title, author, date, groups, child_ids, body, raw)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, version, self._saved_at(), title, author, date, json.dumps(groups, ensure_ascii=False),
             json.dumps(child_ids), body, _json(raw)),
        )
        self._index(key)
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
                file = File(attachment_id(att), Kind.SECURE_DOCUMENT, att.name, att.document.document_type or None, None)
            elif att.link is not None:
                file = File(attachment_id(att), Kind.LINK, att.name, att.link.url, None)
            elif att.file is not None:
                file = File(attachment_id(att), Kind.IMAGE if att.media is not None else Kind.FILE, att.name, None, None)
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
            self._index_file(item_key, file)
            out.append(file)
        return out

    # -- classification (from ``apply``)

    def create_topic(self, name: str, description: str) -> bool:
        """Add a topic; False when it exists already (its description stays)."""
        cur = self._db.execute(
            "INSERT INTO topics (name, description, created_at) VALUES (?, ?, ?) ON CONFLICT (name) DO NOTHING",
            (name, description, self._saved_at()),
        )
        return cur.rowcount == 1

    def classify(self, item_key: str, classification: Classification, *, version: int | None = None) -> bool:
        """Save a classification for an item version: ``version`` (an inbox item's
        ``archive_version``), or the item's newest. It replaces one the version had.

        False when there is no such item or version. Its topics must exist.
        """
        rows = self._db.execute("SELECT id, saved_at FROM versions WHERE item_key = ?", (item_key,)).fetchall()
        if version is None:
            version = max(rows, key=lambda row: (_when(row[1]), row[0]), default=(None,))[0]
        if version is None or version not in {row[0] for row in rows}:
            return False
        known = {name for (name,) in self._db.execute("SELECT name FROM topics")}
        if unknown := [t for t in classification.topics if t not in known]:
            raise ValueError(f"ukendte topics: {', '.join(unknown)}")
        c = classification
        self._db.execute(
            "INSERT OR REPLACE INTO classifications (version_id, category, span_start, span_end, summary, child_ids,"
            " saved_at, todo_text, todo_due) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (version, str(c.category), c.span_start and c.span_start.isoformat(), c.span_end and c.span_end.isoformat(),
             c.summary, json.dumps(list(c.child_ids)), self._saved_at(),
             c.todo and c.todo.text, c.todo and c.todo.due and c.todo.due.isoformat()),
        )
        self._db.execute(
            "UPDATE versions SET first_classified_at = coalesce(first_classified_at, ?) WHERE id = ?",
            (self._saved_at(), version),
        )
        self._db.execute("DELETE FROM classification_topics WHERE version_id = ?", (version,))
        self._db.executemany(
            "INSERT INTO classification_topics (version_id, topic, position) VALUES (?, ?, ?)",
            [(version, topic, i) for i, topic in enumerate(c.topics)],
        )
        self._index(item_key)
        return True

    def forget_classifications(self) -> int:
        """Delete every classification (``reclassify``), so every item is unclassified again; the topics stay,
        and so does when each version was first classified (whether its todo was current).

        Returns how many there were.
        """
        self._db.execute("DELETE FROM classification_topics")
        n = self._db.execute("DELETE FROM classifications").rowcount
        for (key,) in self._db.execute("SELECT key FROM items").fetchall():
            self._index(key)  # without their summaries and topics
        return n

    # -- backup (from :mod:`aula_agent.backup`); each change is committed at once,
    # so a backup that fails half-way does not upload the same file again

    def drive_id(self, name: str) -> str | None:
        """The Drive id of the backup's ``folder`` or of its copy of the ``archive``."""
        row = self._db.execute("SELECT id FROM drive WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def remember_drive_id(self, name: str, drive_id: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO drive (name, id) VALUES (?, ?)", (name, drive_id))
        self._db.commit()

    def forget_drive(self) -> None:
        """The backup is gone from Drive: forget its Drive ids, and that any file is backed up."""
        self._db.execute("DELETE FROM drive")
        self._db.execute("UPDATE files SET backed_up = 0")
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

    def record_backup(self, files: int) -> str:
        """Note that a backup was taken (as of the archive's time); returns when."""
        at = self._saved_at()
        self._db.execute("INSERT INTO backups (at, files) VALUES (?, ?)", (at, files))
        self._db.commit()
        return at

    # -- read side (web, backup, tests)

    def item(self, key: str) -> Item | None:
        row = self._db.execute("SELECT key, kind, thread_id, sensitive FROM items WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        item = Item(row[0], row[1], row[2], bool(row[3]))
        for vid, saved_at, title, author, date, groups, child_ids, body, raw, first_classified_at in self._db.execute(
            "SELECT id, saved_at, title, author, date, groups, child_ids, body, raw, first_classified_at FROM versions"
            " WHERE item_key = ? ORDER BY id",
            (key,),
        ):
            item.versions.append(
                Version(saved_at, title, author, date, json.loads(groups), json.loads(child_ids), body, json.loads(raw),
                        vid, self._classification(vid), first_classified_at)
            )
        item.versions.sort(key=lambda v: _when(v.saved_at))  # stable: equal times keep the order they were saved in
        item.files = [
            File(*row)
            for row in self._db.execute(
                "SELECT attachment_id, kind, name, detail, path FROM files WHERE item_key = ? ORDER BY rowid", (key,)
            )
        ]
        return item

    def _classification(self, version_id: int) -> Classification | None:
        row = self._db.execute(
            "SELECT category, span_start, span_end, summary, child_ids, saved_at, todo_text, todo_due"
            " FROM classifications WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            return None
        topics = tuple(
            t for (t,) in self._db.execute(
                "SELECT topic FROM classification_topics WHERE version_id = ? ORDER BY position", (version_id,)
            )
        )
        day = lambda text: dt.date.fromisoformat(text) if text else None  # noqa: E731
        return Classification(
            category=Category(row[0]), span_start=day(row[1]), span_end=day(row[2]), topics=topics, summary=row[3],
            child_ids=tuple(json.loads(row[4])), saved_at=row[5], todo=Todo(row[6], day(row[7])) if row[6] else None,
        )

    def topics(self) -> list[Topic]:
        """Every topic, by name, with how many items have it in their newest classification."""
        counts: dict[str, int] = {}
        for item in self.items():
            for topic in item.classification.topics if item.classification else ():
                counts[topic] = counts.get(topic, 0) + 1
        return [
            Topic(name, description, created_at, counts.get(name, 0))
            for name, description, created_at in self._db.execute(
                "SELECT name, description, created_at FROM topics ORDER BY name"
            )
        ]

    def todos(self) -> list[ItemTodo]:
        """The todos in the items' newest classifications, by item key (a new version's replaces the old one's)."""
        return [ItemTodo(item, item.classified_version) for item in self.items() if item.classification and item.classification.todo]

    def unclassified(self, *, exclude: Iterable[str] = (), limit: int | None = None) -> list[Item]:
        """Items whose newest version has no classification, oldest first (by the newest version's date).

        ``exclude``: item keys to leave out (the pending items, which the agent classifies anyway).
        """
        skip = set(exclude)
        out = [
            item for item in self.items()
            if item.key not in skip and item.versions and item.latest.classification is None
        ]
        out.sort(key=lambda i: (i.latest.date or "", i.key))
        return out[:limit] if limit is not None else out

    def search(
        self, query: str = "", *, child: int | None = None, category: Category | None = None,
        topic: str | None = None, source: Source | None = None,
    ) -> list[Hit]:
        """The items that match ``query`` (every word as the beginning of a word), newest first.

        An item matches on its title, text, sender, summary and topics, or on the name of or the text
        in one of its files; without a query every item matches. The filters narrow it: the items for
        ``child`` (an item for no child in particular is for every child), of ``category``, with
        ``topic`` (both by the newest classification), and from ``source``.
        """
        words = re.findall(r"\w+", query)
        if query.strip() and not words:  # only quotes, stars and the like
            return []
        excerpts: dict[str, tuple[tuple[str, bool], ...]] = {}
        in_files: dict[str, dict[str, tuple[tuple[str, bool], ...]]] = {}  # item key -> attachment id -> excerpt
        if words:
            match = " ".join(f'"{w}"*' for w in words)
            for key, snippet in self._db.execute(
                "SELECT item_key, snippet(search_items, -1, ?, ?, '…', 16) FROM search_items WHERE search_items MATCH ?",
                (_MARK, _UNMARK, match),
            ):
                excerpts[key] = _parts(snippet)
            for key, attachment, snippet in self._db.execute(
                "SELECT item_key, attachment_id, snippet(search_files, 4, ?, ?, '…', 16) FROM search_files"
                " WHERE search_files MATCH ?",
                (_MARK, _UNMARK, match),
            ):
                in_files.setdefault(key, {})[attachment] = _parts(snippet)
            keys = excerpts.keys() | in_files.keys()
        else:
            keys = {key for (key,) in self._db.execute("SELECT key FROM items")}
        out = []
        for key in keys:
            item = self.item(key)
            if item is None or not item.versions or not _wanted(item, child, category, topic, source):
                continue
            matched = in_files.get(key, {})
            files = tuple(f for f in item.files if f.attachment_id in matched)
            excerpt = excerpts.get(key) or next((x for x in matched.values() if x), ())
            out.append(Hit(item, excerpt, files))
        out.sort(key=lambda h: (h.item.latest.date or "", h.item.key), reverse=True)
        return out

    def reindex(self) -> None:
        """Build the free-text index for every item and file (the text is taken out of a stored file only once)."""
        for (key,) in self._db.execute("SELECT key FROM items").fetchall():
            self._index(key)
        for row in self._db.execute(
            "SELECT item_key, attachment_id, kind, name, detail, path FROM files ORDER BY rowid"
        ).fetchall():
            self._index_file(row[0], File(*row[1:]))

    def _index_file(self, item_key: str, file: File) -> None:
        """Put a file in the free-text index by its name, and by its text once it is stored."""
        old = self._db.execute(
            "SELECT name, extracted, text FROM search_files WHERE item_key = ? AND attachment_id = ?",
            (item_key, file.attachment_id),
        ).fetchone()
        stored = store.DATA_DIR / file.path if file.path else None
        if stored is not None and not stored.is_file():
            stored = None
        if old and old[0] == file.name and (old[1] or stored is None):
            return
        if old and old[1]:
            text = old[2]
        else:
            text = filetext.extract(stored) if stored is not None and file.kind == Kind.FILE else None
        self._db.execute("DELETE FROM search_files WHERE item_key = ? AND attachment_id = ?", (item_key, file.attachment_id))
        self._db.execute(
            "INSERT INTO search_files (item_key, attachment_id, extracted, name, text) VALUES (?, ?, ?, ?, ?)",
            (item_key, file.attachment_id, int(bool(old and old[1]) or stored is not None), file.name, text or ""),
        )

    def _index(self, key: str) -> None:
        """Put an item in the free-text index as its newest version and newest classification have it."""
        self._db.execute("DELETE FROM search_items WHERE item_key = ?", (key,))
        item = self.item(key)
        if item is None or not item.versions:
            return
        latest, c = item.latest, item.classification
        self._db.execute(
            "INSERT INTO search_items (item_key, title, body, author, summary, topics) VALUES (?, ?, ?, ?, ?, ?)",
            (key, latest.title or "", latest.body, latest.author or "", (c and c.summary) or "",
             " ".join(c.topics) if c else ""),
        )

    def thread(self, thread_id: str) -> Thread | None:
        row = self._db.execute("SELECT id, saved_at, raw, messages FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return Thread(row[0], row[1], json.loads(row[2]), json.loads(row[3])) if row else None

    def items(self) -> list[Item]:
        return [self.item(key) for (key,) in self._db.execute("SELECT key FROM items ORDER BY key")]

    def status(self) -> Status:
        counts = dict(self._db.execute("SELECT kind, count(*) FROM items GROUP BY kind").fetchall())
        last = self._db.execute("SELECT max(saved_at) FROM versions").fetchone()[0]
        last_backup = None
        if self._db.execute("PRAGMA user_version").fetchone()[0] >= _BACKUPS_SINCE:  # read-only: may not be migrated yet
            last_backup = self._db.execute("SELECT max(at) FROM backups").fetchone()[0]
        return Status(
            posts=counts.get("post", 0), messages=counts.get("message", 0), last_saved=last, last_backup=last_backup
        )


def post_key(post_id: Any) -> str:
    """A post's item key."""
    return f"post:{post_id}"


def thread_items(thread_id: str, messages: list[dict[str, Any]]) -> Iterator[tuple[str, dict[str, Any], Message]]:
    """The messages of a thread that are items (:data:`MESSAGE_ITEM_TYPES`), as (item key, raw, message)."""
    for raw in messages:
        if raw.get("messageType") in MESSAGE_ITEM_TYPES:
            msg = Message.from_dict(raw)
            yield f"message:{thread_id}:{msg.id}", raw, msg


def post_author(post: Post) -> str | None:
    return post.owner.full_name if post.owner else None


def post_groups(post: Post) -> list[str]:
    """The names of the groups (class, ...) a post is shared with."""
    return [g.get("name") for g in post.shared_with_groups if isinstance(g, dict)]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


_MARK, _UNMARK = "\x02", "\x03"  # around a match in an excerpt (characters Aula's text does not have)


def _parts(snippet: str) -> tuple[tuple[str, bool], ...]:
    """An FTS5 snippet as (text, marked) parts; () when nothing in it is marked."""
    if _MARK not in snippet:
        return ()
    parts = []
    for i, piece in enumerate(re.split(f"[{_MARK}{_UNMARK}]", snippet)):
        if piece:
            parts.append((piece, i % 2 == 1))
    return tuple(parts)


def _wanted(item: Item, child: int | None, category: Category | None, topic: str | None, source: Source | None) -> bool:
    c = item.classification
    if child is not None and item.child_ids and child not in item.child_ids:
        return False
    if category is not None and (c is None or c.category != category):
        return False
    if topic is not None and (c is None or topic not in c.topics):
        return False
    if source == Source.POST:
        return item.kind == "post"
    if source == Source.MESSAGE:
        return item.kind == "message"
    if source == Source.DOCUMENT:
        return any(f.kind in (Kind.FILE, Kind.SECURE_DOCUMENT) for f in item.files)
    return True


_URL_QUERY = re.compile(r"(https?://[^\s?#()<>\[\]\"']+)[?#][^\s()<>\[\]\"']*")


def _without_signatures(text: str) -> str:
    """The text with the query string and fragment cut off its URLs, where signatures and expiry times go."""
    return _URL_QUERY.sub(r"\1", text)


def _version_hash(title: str | None, body: str, attachments: list[Attachment]) -> str:
    ids = sorted(attachment_id(a) for a in attachments)
    content = [_without_signatures(title or ""), _without_signatures(body), ids]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()[:16]


def stored_name(att: Attachment) -> str:
    """The name a file or image is stored under, in its item's folder (a rescue copy uses the same)."""
    return f"{attachment_id(att)}-{_safe(att.name)}"


def attachment_id(att: Attachment) -> str:
    """The id the archive knows an attachment by (Aula's, not its URL)."""
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
    if current < _BACKUPS_SINCE:
        db.executescript(_SCHEMA_3)
    if current < _CLASSIFICATIONS_SINCE:
        db.executescript(_SCHEMA_4)
    if current < _TODOS_SINCE:
        db.executescript(_SCHEMA_5)
    if current < _FIRST_CLASSIFIED_SINCE:
        db.executescript(_SCHEMA_6)
    if current < _SEARCH_SINCE:
        db.executescript(_SCHEMA_7)
        Archive(db).reindex()
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextlib.contextmanager
def open(*, at: dt.datetime | None = None) -> Iterator[Archive]:  # noqa: A001 - archive.open() reads well at the call site
    """Open the archive (creating it the first time); changes are committed on a clean exit.

    What is saved (an item version, a backup) is saved as of ``at`` (default: now);
    a rescue copy is saved as of when it was taken.
    """
    file = store.path("ARCHIVE_FILE")
    file.parent.mkdir(parents=True, exist_ok=True)
    file.touch(mode=0o600)  # about other children and parents: the owner only (SQLite's journal follows)
    file.chmod(0o600)
    db = sqlite3.connect(file, timeout=30)
    try:
        with db:
            _migrate(db)
        with db:
            yield Archive(db, at)
    finally:
        db.close()


@contextlib.contextmanager
def read() -> Iterator[Archive | None]:
    """The archive opened read-only, without creating, migrating or changing the file (for the web page).

    None when there is no archive yet, or its schema is not this version's (the next ``fetch`` migrates it).
    """
    file = store.path("ARCHIVE_FILE")
    if not file.exists():
        yield None
        return
    db = sqlite3.connect(f"{file.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        yield Archive(db) if db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION else None
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
