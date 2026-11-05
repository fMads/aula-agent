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
    """Take a backup now (``fetch`` does at the end, and so will ``backfill``).

    Raises :class:`gcal.GoogleLoginRequired` without Google access.
    """
    cfg = store.load_config()
    google = gcal._resolve(google)
    at = (now or dt.datetime.now()).astimezone().isoformat(timespec="seconds")
    with archive.open() as arch:
        folder = arch.drive_id("folder")
        if not folder:
            folder = google.create_folder(cfg["archive_folder_name"])
            arch.remember_drive_id("folder", folder)

        uploaded = 0
        for item_key, file in arch.files_to_back_up():
            google.upload_file(store.DATA_DIR / file.path, file.path, folder)
            arch.mark_backed_up(item_key, file.attachment_id)
            uploaded += 1

        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / ARCHIVE_NAME
            arch.copy_to(copy)
            if file_id := arch.drive_id("archive"):
                google.replace_file(file_id, copy)
            else:
                arch.remember_drive_id("archive", google.upload_file(copy, ARCHIVE_NAME, folder)["fileId"])

        arch.record_backup(at, uploaded)
    return Result(at, uploaded)
