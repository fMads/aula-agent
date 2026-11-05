import pytest

from aula_agent import gcal, store


def setup_children(*children):
    cfg = store.load_config()
    for cid, name in children:
        cfg["children"][str(cid)] = {"name": name, "institution": "Skolen"}
    store.save_config(cfg)


def add_pending(key, **item):
    st = store.load_state()
    st["pending"][key] = {"key": key, **item}
    store.save_state(st)


# ---------------------------------------------------------------- event body


def test_all_day_end_is_inclusive():
    body = gcal.build_event_body({"key": "k", "title": "Lejrskole", "start": "2026-11-10", "end": "2026-11-12"})
    assert body["start"] == {"date": "2026-11-10"}
    assert body["end"] == {"date": "2026-11-13"}


def test_all_day_single_day_default():
    body = gcal.build_event_body({"key": "k", "title": "Juleklip", "start": "2026-12-04"})
    assert body["end"] == {"date": "2026-12-05"}


def test_timed_event_defaults_to_one_hour_in_copenhagen():
    body = gcal.build_event_body({"key": "k", "title": "Forældremøde", "start": "2026-11-20T17:00"})
    assert body["start"] == {"dateTime": "2026-11-20T17:00:00", "timeZone": "Europe/Copenhagen"}
    assert body["end"] == {"dateTime": "2026-11-20T18:00:00", "timeZone": "Europe/Copenhagen"}


def test_description_gets_source_footer():
    item = {"type": "post", "title": "Juleklip i 2.A", "author": "Lærer Hansen", "published": "2026-11-01T10:00+01:00"}
    body = gcal.build_event_body({"key": "k", "title": "Juleklip", "start": "2026-12-04", "description": "Husk saks"}, item)
    assert body["description"].startswith("Husk saks\n\nFra Aula: opslag »Juleklip i 2.A« af Lærer Hansen (2026-11-01)")
    assert body["extendedProperties"]["private"]["aulaAgentKey"] == "k"


@pytest.mark.parametrize(
    "ev, message",
    [
        ({"key": "k", "start": "2026-11-10"}, "title mangler"),
        ({"key": "k", "title": "x", "start": "10/11"}, "ugyldig"),
        ({"key": "k", "title": "x", "start": "2026-11-10T10:00", "end": "2026-11-10T09:00"}, "før start"),
        ({"key": "k", "title": "x", "start": "2026-11-10", "end": "2026-11-10T09:00"}, "hel dag"),
    ],
)
def test_bad_events_rejected(ev, message):
    with pytest.raises(gcal.EventError, match=message):
        gcal.build_event_body(ev)


def test_validate_catches_unknown_child_missing_file_and_duplicate_key():
    setup_children((1, "Emma"))
    doc = {
        "events": [
            {"key": "a", "child_ids": [2], "title": "x", "start": "2026-11-10"},
            {"key": "a", "child_ids": [1], "title": "x", "start": "2026-11-10", "attachments": ["nope.pdf"]},
        ]
    }
    errors = "\n".join(gcal.validate(doc, store.load_config()))
    assert "ukendt barn 2" in errors
    assert "to gange" in errors
    assert "findes ikke" in errors


# ---------------------------------------------------------------- sync


def test_sync_creates_calendar_per_child_uploads_once_and_marks_processed(google, data_dir):
    cal, drive = google
    setup_children((1, "Emma Jensen"), (2, "Oscar Jensen"))
    add_pending("post:9", type="post", title="Juleklip", child_ids=[1, 2])
    (data_dir / "attachments").mkdir()
    (data_dir / "attachments" / "55-indbydelse.pdf").write_bytes(b"%PDF")
    doc = {
        "processed": ["post:9"],
        "events": [
            {
                "key": "post:9#1",
                "source": "post:9",
                "child_ids": [1, 2],
                "title": "Juleklip",
                "start": "2026-12-04",
                "attachments": ["attachments/55-indbydelse.pdf"],
            }
        ],
    }
    result = gcal.sync(doc, svc=google)

    assert cal.calendars_created == ["Skole – Emma", "Skole – Oscar"]
    assert len(result["created"]) == 2
    assert drive.uploads == ["indbydelse.pdf"]  # uploaded once, attached to both
    assert drive.folders == ["Aula"]
    bodies = list(cal.events_by_id.values())
    assert all(b["attachments"][0]["fileUrl"] == "https://drive/file1" for b in bodies)
    assert all(call[2] is True for call in cal.calls)  # supportsAttachments
    assert store.load_state()["pending"] == {}
    cfg = store.load_config()
    assert cfg["children"]["1"]["calendar_id"] != cfg["children"]["2"]["calendar_id"]


def test_sync_same_key_updates_instead_of_duplicating(google):
    cal, _ = google
    setup_children((1, "Emma"))
    ev = {"key": "msg:t1#1", "child_ids": [1], "title": "Skovtur", "start": "2026-11-12"}
    gcal.sync({"events": [ev]}, svc=google)
    gcal.sync({"events": [{**ev, "start": "2026-11-13", "description": "Flyttet til torsdag"}]}, svc=google)

    assert len(cal.events_by_id) == 1
    (body,) = cal.events_by_id.values()
    assert body["start"] == {"date": "2026-11-13"}
    assert body["description"].startswith("Flyttet til torsdag")
    # source info from the first run is remembered for the update
    assert store.load_state()["events"]


def test_sync_finds_event_by_key_when_local_state_lost(google):
    cal, _ = google
    setup_children((1, "Emma"))
    ev = {"key": "k1", "child_ids": [1], "title": "Skovtur", "start": "2026-11-12"}
    gcal.sync({"events": [ev]}, svc=google)
    st = store.load_state()
    st["events"] = {}
    store.save_state(st)
    gcal.sync({"events": [{**ev, "title": "Skovtur (rettet)"}]}, svc=google)
    assert len(cal.events_by_id) == 1


def test_sync_cancelled_deletes(google):
    cal, _ = google
    setup_children((1, "Emma"))
    ev = {"key": "k1", "child_ids": [1], "title": "Skovtur", "start": "2026-11-12"}
    gcal.sync({"events": [ev]}, svc=google)
    result = gcal.sync({"events": [{"key": "k1", "child_ids": [1], "cancelled": True}]}, svc=google)
    assert cal.events_by_id == {}
    assert len(result["deleted"]) == 1
    assert store.load_state()["events"] == {}


def test_invalid_doc_changes_nothing(google):
    cal, _ = google
    setup_children((1, "Emma"))
    add_pending("post:1", type="post")
    doc = {
        "processed": ["post:1"],
        "events": [
            {"key": "ok", "child_ids": [1], "title": "Fint", "start": "2026-11-12"},
            {"key": "bad", "child_ids": [1], "title": "Dårligt", "start": "i morgen"},
        ],
    }
    with pytest.raises(gcal.EventError):
        gcal.sync(doc, svc=google)
    assert cal.events_by_id == {}
    assert "post:1" in store.load_state()["pending"]


def test_dry_run_touches_nothing(google):
    setup_children((1, "Emma"))
    add_pending("post:1", type="post")
    lines = []
    gcal.sync(
        {"processed": ["post:1"], "events": [{"key": "a", "child_ids": [1], "title": "x", "start": "2026-11-12"}]},
        dry_run=True,
        echo=lines.append,
    )
    assert lines and "post:1" in store.load_state()["pending"]
    assert not store.load_config()["children"]["1"].get("calendar_id")


def test_removed_by_user_is_never_recreated(google):
    cal, _ = google
    setup_children((1, "Emma"))
    ev = {"key": "k1", "child_ids": [1], "title": "Skovtur", "start": "2099-11-12"}
    gcal.sync({"events": [ev]}, svc=google)
    assert gcal.remove_event("k1", svc=google) == 1
    assert cal.events_by_id == {}

    result = gcal.sync({"events": [{**ev, "title": "Skovtur (rettet)"}]}, svc=google)
    assert cal.events_by_id == {}
    assert result["skipped"] and not result["created"]

    from aula_agent.aula_source import write_inbox

    write_inbox(store.load_state(), store.load_config(), [], [])
    import json

    inbox = json.loads(store.INBOX_FILE.read_text())
    assert inbox["removed_by_user"] == [{"key": "k1", "title": "Skovtur", "start": "2099-11-12"}]
