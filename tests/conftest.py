import itertools

import pytest

from aula_agent import store


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Every test gets its own data/ (all paths in store follow DATA_DIR)."""
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    return tmp_path


class FakeGoogle:
    """In-memory adapter with the same methods as :class:`aula_agent.gcal.Google`."""

    def __init__(self):
        self.calendars: dict[str, str] = {}  # id -> summary
        self.events: dict[tuple[str, str], dict] = {}  # (calendar_id, event_id) -> body
        self.uploads: list[str] = []
        self.folders: list[str] = []  # names, in the order they were created
        self.drive: dict[str, dict] = {}  # file id -> {"name", "folder", "content", "trashed"}
        self.replaced: list[str] = []  # file ids whose content was replaced
        self._folder_ids: dict[str, str] = {}  # folder id -> name, while the folder exists
        self._trashed_folders: set[str] = set()
        self.calls: list[tuple] = []
        self._ids = itertools.count(1)

    def create_calendar(self, summary):
        cal_id = f"cal{next(self._ids)}@group.calendar.google.com"
        self.calendars[cal_id] = summary
        return cal_id

    def find_event(self, calendar_id, key):
        self.calls.append(("find", calendar_id, key))
        return next(
            (eid for (cid, eid), body in self.events.items()
             if cid == calendar_id and body["extendedProperties"]["private"]["aulaAgentKey"] == key),
            None,
        )

    def insert_event(self, calendar_id, body):
        event_id = f"ev{next(self._ids)}"
        self.calls.append(("insert", calendar_id, event_id))
        self.events[(calendar_id, event_id)] = body
        return event_id, f"https://cal/{event_id}"

    def patch_event(self, calendar_id, event_id, body):
        self.calls.append(("patch", calendar_id, event_id))
        self.events[(calendar_id, event_id)] = {**self.events.get((calendar_id, event_id), {}), **body}
        return event_id, f"https://cal/{event_id}"

    def delete_event(self, calendar_id, event_id):
        self.calls.append(("delete", calendar_id, event_id))
        return self.events.pop((calendar_id, event_id), None) is not None

    def create_folder(self, name):
        self.folders.append(name)
        folder_id = f"folder{len(self.folders)}"
        self._folder_ids[folder_id] = name
        return folder_id

    def upload_file(self, path, name, folder_id):
        assert path.is_file()
        if folder_id not in self._folder_ids:
            raise RuntimeError(f"Drive svarer 404: File not found: {folder_id}")
        self.uploads.append(name)
        n = len(self.uploads)
        self.drive[f"file{n}"] = {"name": name, "folder": folder_id, "content": path.read_bytes(), "trashed": False}
        return {"fileId": f"file{n}", "fileUrl": f"https://drive/file{n}", "title": name, "mimeType": "application/pdf"}

    def replace_file(self, file_id, path):
        assert path.is_file()
        if file_id not in self.drive:
            return False
        self.replaced.append(file_id)
        self.drive[file_id]["content"] = path.read_bytes()
        return not self._trashed(self.drive[file_id])

    # -- what the user can do in Drive

    def trash(self, folder_name):
        """Move a folder to the trash (its files with it)."""
        self._trashed_folders |= {fid for fid, name in self._folder_ids.items() if name == folder_name}

    def delete(self, folder_name):
        """Delete a folder for good (empty the trash), its files with it."""
        gone = {fid for fid, name in self._folder_ids.items() if name == folder_name}
        self.drive = {i: f for i, f in self.drive.items() if f["folder"] not in gone}
        self._folder_ids = {fid: name for fid, name in self._folder_ids.items() if fid not in gone}

    def _trashed(self, file):
        return file["trashed"] or file["folder"] in self._trashed_folders

    def in_folder(self, folder_name):
        """Names of the files in a Drive folder (not in the trash)."""
        return sorted(
            f["name"] for f in self.drive.values()
            if self._folder_ids.get(f["folder"]) == folder_name and not self._trashed(f)
        )

    def bodies(self):
        return list(self.events.values())


@pytest.fixture
def google():
    return FakeGoogle()
