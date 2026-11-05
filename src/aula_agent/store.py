"""Local files under ``data/``: paths, JSON I/O, config and the process lock.

Everything lives under ``data/`` in the project (override with ``AULA_AGENT_DATA``).
The directory is git-ignored because it holds personal data. In a git worktree
(T3 Code and Codex start runs in one) ``data/`` becomes a link to the main
checkout's ``data/``, so every checkout shares the same logins and state. What aula-agent
remembers between runs (``state.json``) is owned by :mod:`aula_agent.ledger`.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _main_checkout(root: Path) -> Path | None:
    """The main checkout when ``root`` is a linked git worktree, else None."""
    git = root / ".git"
    if not git.is_file():
        return None
    try:
        line = git.read_text(encoding="utf-8").strip()
        if not line.startswith("gitdir:"):
            return None
        gitdir = (root / line.removeprefix("gitdir:").strip()).resolve()
        common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        return None
    main = common.parent
    return main if main != root.resolve() else None


def default_data_dir(root: Path = PROJECT_ROOT) -> Path:
    """``<root>/data``. In a linked worktree without its own ``data/`` it is
    created as a link to the main checkout's ``data/``."""
    data = root / "data"
    if data.exists() or data.is_symlink():
        return data
    main = _main_checkout(root)
    if main and (main / "data").is_dir():
        with contextlib.suppress(OSError):
            data.symlink_to(main / "data", target_is_directory=True)
            return data
        return main / "data"
    return data


DATA_DIR = Path(os.environ["AULA_AGENT_DATA"]) if os.environ.get("AULA_AGENT_DATA") else default_data_dir()

TIMEZONE = "Europe/Copenhagen"

# Paths are derived from DATA_DIR on every access, so moving DATA_DIR (tests do)
# moves all of them: ``store.CONFIG_FILE``, ``store.INBOX_FILE``, ...
_FILES = {
    "CONFIG_FILE": "config.json",
    "STATE_FILE": "state.json",
    "INBOX_FILE": "inbox.json",
    "ATTACHMENTS_DIR": "attachments",
    "ARCHIVE_FILE": "archive.sqlite",
    "GOOGLE_CLIENT_SECRET": "google_client_secret.json",
    "GOOGLE_TOKEN": "google_token.json",
}


def __getattr__(name: str) -> Path:
    if name in _FILES:
        return DATA_DIR / _FILES[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def path(name: str) -> Path:
    """Same as ``store.<NAME>``, for use inside this package's own modules."""
    return DATA_DIR / _FILES[name]


def read_json(file: Path, default: Any) -> Any:
    if not file.exists():
        return default
    return json.loads(file.read_text(encoding="utf-8"))


def write_json(file: Path, data: Any, *, private: bool = False) -> None:
    """Write atomically so a crash never leaves half a file behind.

    ``private`` makes the file readable by the owner only (tokens, keys).
    """
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=f".{file.name}.")
    if private:
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")
    os.replace(tmp, file)


def load_config() -> dict[str, Any]:
    cfg = read_json(path("CONFIG_FILE"), {})
    cfg.setdefault("children", {})  # str(child_id) -> {"name", "institution", "calendar_id", "calendar_name"}
    cfg.setdefault("calendar_name_template", "Skole – {name}")
    cfg.setdefault("drive_folder_name", "Aula")  # attachments of calendar events; may be shared
    cfg.setdefault("archive_folder_name", "Aula-arkiv")  # backup of the archive; never shared
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    write_json(path("CONFIG_FILE"), cfg)


def web_url(suffix: str = "") -> str | None:
    """Public URL of the web UI with ``suffix`` appended, or None when it is not set up."""
    url = ((load_config().get("web") or {}).get("public_url") or "").rstrip("/")
    return f"{url}{suffix}" if url else None


class Busy(RuntimeError):
    """Another aula-agent process holds the lock."""


@contextlib.contextmanager
def lock(*, wait: bool = True) -> Iterator[None]:
    """Serialise changes to ``data/`` between the CLI (nightly run) and the web UI.

    Not re-entrant: never take it again while holding it (the second call would
    block forever on ``wait=True``).
    """
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
