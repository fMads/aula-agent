"""Local files: config (children -> calendars) and state (what has been seen and synced).

Everything lives under ``data/`` in the project (override with ``AULA_AGENT_DATA``).
The directory is git-ignored because it holds personal data.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("AULA_AGENT_DATA", PROJECT_ROOT / "data"))

CONFIG_FILE = DATA_DIR / "config.json"
STATE_FILE = DATA_DIR / "state.json"
INBOX_FILE = DATA_DIR / "inbox.json"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
GOOGLE_CLIENT_SECRET = DATA_DIR / "google_client_secret.json"
GOOGLE_TOKEN = DATA_DIR / "google_token.json"

TIMEZONE = "Europe/Copenhagen"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """Write atomically so a crash never leaves half a file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")
    os.replace(tmp, path)


def load_config() -> dict[str, Any]:
    cfg = read_json(CONFIG_FILE, {})
    cfg.setdefault("children", {})  # str(child_id) -> {"name", "institution", "calendar_id"}
    cfg.setdefault("calendar_name_template", "Skole – {name}")
    cfg.setdefault("drive_folder_name", "Aula")
    return cfg


def web_url(path: str = "") -> str | None:
    """Public URL of the web UI with ``path`` appended, or None when it is not set up."""
    url = ((load_config().get("web") or {}).get("public_url") or "").rstrip("/")
    return f"{url}{path}" if url else None


def save_config(cfg: dict[str, Any]) -> None:
    write_json(CONFIG_FILE, cfg)


def load_state() -> dict[str, Any]:
    st = read_json(STATE_FILE, {})
    st.setdefault("seen", {})  # item key -> version string
    st.setdefault("threads", {})  # thread id -> lastMessageDate seen
    st.setdefault("pending", {})  # item key -> inbox item not yet processed
    st.setdefault("events", {})  # "<key>|<calendar_id>" -> synced event info
    st.setdefault("uploads", {})  # local attachment path -> Drive file info
    st.setdefault("ignored", {})  # event key -> {"title", "start", "removed_at"}: removed by the user
    st.setdefault("runs", [])  # newest last: {"at", "command", "ok", "summary"}
    st.setdefault("drive_folder_id", None)
    st.setdefault("last_fetch", None)
    return st


def save_state(st: dict[str, Any]) -> None:
    write_json(STATE_FILE, st)


class Busy(RuntimeError):
    """Another aula-agent process holds the lock."""


@contextlib.contextmanager
def lock(*, wait: bool = True) -> Iterator[None]:
    """Serialise state changes between the CLI (nightly run) and the web UI."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / ".lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Busy("En synkronisering kører lige nu.") from exc
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def log_run(command: str, ok: bool, summary: str, keep: int = 50) -> None:
    with lock():
        _append_run(command, ok, summary, keep)


def _append_run(command: str, ok: bool, summary: str, keep: int) -> None:
    st = load_state()
    st["runs"].append(
        {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "command": command, "ok": ok, "summary": summary}
    )
    st["runs"] = st["runs"][-keep:]
    save_state(st)
