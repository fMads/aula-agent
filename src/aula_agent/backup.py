"""Back up the archive to a private Google Drive folder, so it is not lost with the machine.

The folder (``archive_folder_name`` in the config, ``Aula-arkiv`` by default) is
created by aula-agent and never shared, unlike the "Aula" folder that holds the
calendar attachments. Every backup replaces the same Drive copy of the archive
file; the archive's files are uploaded once each, under their path in ``data/``.
What is in Drive and when the backup was taken is kept in the archive itself.
"""

from __future__ import annotations

import datetime as dt
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import archive, gcal, store

ARCHIVE_NAME = "archive.sqlite"


@dataclass(frozen=True)
class Result:
    at: str
    files: int  # how many files were uploaded this time


def take(google: gcal.Google | Callable[[], gcal.Google] | None = None, *, now: dt.datetime | None = None) -> Result:
    """Take a backup now (``fetch`` and ``backfill`` do at the end).

    When the folder or the archive's copy has been deleted from Drive (or is in
    the trash), the backup starts over: a new folder, with every file.
    Raises :class:`gcal.GoogleLoginRequired` without Google access.
    """
    cfg = store.load_config()
    google = gcal._resolve(google)
    at = (now or dt.datetime.now()).astimezone().isoformat(timespec="seconds")
    with archive.open() as arch:
        try:
            uploaded = _upload_files(google, arch, cfg["archive_folder_name"])
        except Exception:
            # Perhaps the folder is gone from Drive; the archive's copy tells.
            if _replace_archive(google, arch) is not False:
                raise
            in_drive = False
        else:
            in_drive = _replace_archive(google, arch)
            if in_drive is None:
                _upload_archive(google, arch)
                in_drive = True
        if not in_drive:
            arch.forget_drive()
            uploaded = _upload_files(google, arch, cfg["archive_folder_name"])
            _upload_archive(google, arch)
        arch.record_backup(at, uploaded)
    return Result(at, uploaded)


def _upload_files(google: gcal.Google, arch: archive.Archive, folder_name: str) -> int:
    """Upload the files not in the backup yet (making the folder first, if need be)."""
    folder = arch.drive_id("folder")
    if not folder:
        folder = google.create_folder(folder_name)
        arch.remember_drive_id("folder", folder)
    uploaded = 0
    for item_key, file in arch.files_to_back_up():
        google.upload_file(store.DATA_DIR / file.path, file.path, folder)
        arch.mark_backed_up(item_key, file.attachment_id)
        uploaded += 1
    return uploaded


def _replace_archive(google: gcal.Google, arch: archive.Archive) -> bool | None:
    """Replace the archive's copy in Drive: False when it is gone, None when there is none yet."""
    file_id = arch.drive_id("archive")
    if not file_id:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / ARCHIVE_NAME
        arch.copy_to(copy)
        return google.replace_file(file_id, copy)


def _upload_archive(google: gcal.Google, arch: archive.Archive) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / ARCHIVE_NAME
        arch.copy_to(copy)
        arch.remember_drive_id("archive", google.upload_file(copy, ARCHIVE_NAME, arch.drive_id("folder"))["fileId"])
