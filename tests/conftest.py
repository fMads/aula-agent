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
        self.folders: list[str] = []
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
        return "folder1"

    def upload_file(self, path, name, folder_id):
        assert path.is_file() and folder_id == "folder1"
        self.uploads.append(name)
        n = len(self.uploads)
        return {"fileId": f"file{n}", "fileUrl": f"https://drive/file{n}", "title": name, "mimeType": "application/pdf"}

    def bodies(self):
        return list(self.events.values())


@pytest.fixture
def google():
    return FakeGoogle()
