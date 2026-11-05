import itertools

import pytest

from aula_agent import store


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(store, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(store, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(store, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(store, "GOOGLE_TOKEN", tmp_path / "google_token.json")
    monkeypatch.setattr(store, "GOOGLE_CLIENT_SECRET", tmp_path / "google_client_secret.json")
    return tmp_path


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeCalendar:
    """Just enough of the Calendar v3 client: calendars().insert and events().*."""

    def __init__(self):
        self.calendars_created = []
        self.events_by_id = {}  # (calendar_id, event_id) -> body
        self.calls = []
        self._ids = itertools.count(1)

    def calendars(self):
        outer = self

        class Calendars:
            def insert(self, body):
                cal_id = f"cal{next(outer._ids)}@group.calendar.google.com"
                outer.calendars_created.append(body["summary"])
                return _Call(lambda: {"id": cal_id, **body})

        return Calendars()

    def events(self):
        outer = self

        class Events:
            def insert(self, calendarId, body, supportsAttachments=None):
                event_id = f"ev{next(outer._ids)}"
                outer.calls.append(("insert", calendarId, supportsAttachments))
                outer.events_by_id[(calendarId, event_id)] = body
                return _Call(lambda: {"id": event_id, "htmlLink": f"https://cal/{event_id}"})

            def patch(self, calendarId, eventId, body, supportsAttachments=None):
                outer.calls.append(("patch", calendarId, supportsAttachments))
                outer.events_by_id[(calendarId, eventId)] = {**outer.events_by_id[(calendarId, eventId)], **body}
                return _Call(lambda: {"id": eventId})

            def delete(self, calendarId, eventId):
                outer.calls.append(("delete", calendarId))
                del outer.events_by_id[(calendarId, eventId)]
                return _Call(lambda: {})

            def list(self, calendarId, privateExtendedProperty):
                prop, value = privateExtendedProperty.split("=", 1)
                items = [
                    {"id": eid}
                    for (cid, eid), body in outer.events_by_id.items()
                    if cid == calendarId and body["extendedProperties"]["private"].get(prop) == value
                ]
                return _Call(lambda: {"items": items})

        return Events()


class FakeDrive:
    def __init__(self):
        self.uploads = []
        self.folders = []

    def files(self):
        return self

    def create(self, body, fields, media_body=None):
        if media_body is None:
            self.folders.append(body["name"])
            return _Call(lambda: {"id": "folder1"})
        self.uploads.append(body["name"])
        n = len(self.uploads)
        return _Call(
            lambda: {"id": f"file{n}", "name": body["name"], "mimeType": "application/pdf", "webViewLink": f"https://drive/file{n}"}
        )


@pytest.fixture
def google():
    return FakeCalendar(), FakeDrive()
