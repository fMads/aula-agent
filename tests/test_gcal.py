import pytest

from aula_agent import gcal, store
from aula_agent.contract import EventError
from aula_agent.ledger import Ledger


def setup_children(*children):
    cfg = store.load_config()
    for cid, name in children:
        cfg["children"][str(cid)] = {"name": name, "institution": "Skolen"}
    store.save_config(cfg)


def add_pending(key, **item):
    ledger = Ledger.load()
    ledger.add_pending({"key": key, "child_ids": [], **item})
    ledger.save()


def ev(key="k1", **kw):
    return {"key": key, "child_ids": [1], "title": "Skovtur", "start": "2099-11-12", **kw}


# ----------------------------------------------------------------------------- event body


def test_body_has_inclusive_all_day_end_footer_and_key():
    from aula_agent.contract import parse

    (event,) = parse(
        {"events": [{"key": "k", "child_ids": [1], "title": "Lejrskole", "start": "2026-11-10", "end": "2026-11-12", "description": "Husk sovepose"}]},
        children=["1"], pending_keys=[],
    ).events
    item = {"type": "post", "title": "Lejrskole i 5.A", "author": "Lærer Hansen", "published": "2026-11-01T10:00+01:00"}
    body = gcal.build_event_body(event, item)
    assert body["start"] == {"date": "2026-11-10"} and body["end"] == {"date": "2026-11-13"}
    assert body["description"].startswith("Husk sovepose\n\nFra Aula: opslag »Lejrskole i 5.A« af Lærer Hansen (2026-11-01)")
    assert body["extendedProperties"]["private"]["aulaAgentKey"] == "k"


def test_timed_body_is_in_copenhagen():
    from aula_agent.contract import parse

    (event,) = parse({"events": [ev(start="2026-11-20T17:00")]}, children=["1"], pending_keys=[]).events
    body = gcal.build_event_body(event)
    assert body["start"] == {"dateTime": "2026-11-20T17:00:00", "timeZone": "Europe/Copenhagen"}
    assert body["end"] == {"dateTime": "2026-11-20T18:00:00", "timeZone": "Europe/Copenhagen"}


# ----------------------------------------------------------------------------- sync


def test_sync_creates_calendar_per_child_uploads_once_and_marks_processed(google, data_dir):
    setup_children((1, "Emma Jensen"), (2, "Oscar Jensen"))
    add_pending("post:9", type="post", title="Juleklip")
    (data_dir / "attachments").mkdir()
    (data_dir / "attachments" / "55-indbydelse.pdf").write_bytes(b"%PDF")
    doc = {
        "processed": ["post:9"],
        "classifications": [{"item": "post:9", "category": "svar"}],
        "events": [ev("post:9#1", source="post:9", child_ids=[1, 2], title="Juleklip", attachments=["attachments/55-indbydelse.pdf"])],
    }
    result = gcal.sync(doc, google=google)

    assert sorted(google.calendars.values()) == ["Skole – Emma", "Skole – Oscar"]
    assert len(result["created"]) == 2 and result["processed"] == ["post:9"]
    assert google.uploads == ["indbydelse.pdf"]  # uploaded once, attached to both
    assert google.folders == ["Aula"]
    assert all(b["attachments"][0]["fileUrl"] == "https://drive/file1" for b in google.bodies())
    assert all("Fra Aula: opslag »Juleklip«" in b["description"] for b in google.bodies())
    ledger = Ledger.load()
    assert ledger.pending_keys() == set()
    assert [u.child_ids for u in ledger.upcoming()] == [[1, 2]]


def set_public_url(url):
    cfg = store.load_config()
    cfg["web"] = {"public_url": url}
    store.save_config(cfg)


def test_description_links_to_the_items_detail_page_when_the_web_page_has_an_address(google):
    setup_children((1, "Emma"))
    set_public_url("https://aula.example.dk")
    add_pending("message:4:7", type="message", subject="Skovtur")
    gcal.sync({"processed": ["message:4:7"], "classifications": [{"item": "message:4:7", "category": "svar"}], "events": [ev("message:4:7#1", source="message:4:7", description="Madpakke")]}, google=google)

    (body,) = google.bodies()
    assert "https://aula.example.dk/item/message:4:7" in body["description"]
    assert body["description"].startswith("Madpakke\n\nFra Aula: besked »Skovtur«")


def test_description_of_an_event_from_a_sensitive_thread_has_no_subject_but_the_link(google):
    setup_children((1, "Emma"))
    set_public_url("https://aula.example.dk")
    add_pending("message:4:7", type="message", subject="Samtale om skoledagen", sender="Lærer Hansen", sensitive=True)
    gcal.sync({"processed": ["message:4:7"], "classifications": [{"item": "message:4:7", "category": "svar"}],
               "events": [ev("message:4:7#1", source="message:4:7", description="Mød op på kontoret")]}, google=google)

    (body,) = google.bodies()
    assert body["description"].startswith("Mød op på kontoret\n\nFra Aula: besked (følsom tråd)")
    assert "Samtale om skoledagen" not in body["description"] and "»" not in body["description"]
    assert "Hansen" not in body["description"]  # the detail page hides a sensitive thread's sender too
    assert "https://aula.example.dk/item/message:4:7" in body["description"]


def test_an_event_from_before_the_link_gets_it_when_it_is_updated(google):
    setup_children((1, "Emma"))
    add_pending("post:9", type="post", title="Juleklip")
    gcal.sync({"processed": ["post:9"], "classifications": [{"item": "post:9", "category": "svar"}],
               "events": [ev("post:9#1", source="post:9")]}, google=google)
    assert "/item/" not in google.bodies()[0]["description"]

    set_public_url("https://aula.example.dk")
    result = gcal.sync({"events": [ev("post:9#1", source="post:9", start="2099-11-13")]}, google=google)

    assert len(result["updated"]) == 1
    (body,) = google.bodies()
    assert "Hele teksten: https://aula.example.dk/item/post:9" in body["description"]
    assert "Fra Aula: opslag »Juleklip«" in body["description"]


def test_description_has_no_link_without_a_web_address(google):
    setup_children((1, "Emma"))
    add_pending("post:9", type="post", title="Juleklip")
    gcal.sync({"processed": ["post:9"], "classifications": [{"item": "post:9", "category": "svar"}], "events": [ev("post:9#1", source="post:9")]}, google=google)

    (body,) = google.bodies()
    assert "http" not in body["description"] and "/item/" not in body["description"]


def test_same_key_updates_and_clears_removed_fields(google, data_dir):
    setup_children((1, "Emma"))
    (data_dir / "attachments").mkdir()
    (data_dir / "attachments" / "1-seddel.pdf").write_bytes(b"%PDF")
    gcal.sync({"events": [ev(location="Skoven", description="Madpakke", attachments=["attachments/1-seddel.pdf"])]}, google=google)
    assert google.bodies()[0]["attachments"] and google.bodies()[0]["location"] == "Skoven"
    result = gcal.sync({"events": [ev(start="2099-11-13")]}, google=google)

    assert len(google.events) == 1 and len(result["updated"]) == 1
    (body,) = google.bodies()
    assert body["start"] == {"date": "2099-11-13"}
    assert body["location"] == "" and body["attachments"] == []
    assert not body["description"].startswith("Madpakke")
    assert Ledger.load().upcoming()[0].start == "2099-11-13"


def test_event_found_by_key_when_local_state_lost(google, data_dir):
    setup_children((1, "Emma"))
    gcal.sync({"events": [ev()]}, google=google)
    (data_dir / "state.json").unlink()
    result = gcal.sync({"events": [ev(title="Skovtur (rettet)")]}, google=google)
    assert len(google.events) == 1 and len(result["updated"]) == 1


def test_cancelled_deletes(google):
    setup_children((1, "Emma"))
    gcal.sync({"events": [ev()]}, google=google)
    result = gcal.sync({"events": [{"key": "k1", "child_ids": [1], "cancelled": True}]}, google=google)
    assert google.events == {} and len(result["deleted"]) == 1
    assert Ledger.load().upcoming() == []


def test_cancellation_needs_only_the_key_and_removes_it_for_every_child(google):
    setup_children((1, "Emma"), (2, "Oscar"))
    gcal.sync({"events": [ev(child_ids=[1, 2])]}, google=google)
    result = gcal.sync({"events": [{"key": "k1", "cancelled": True}]}, google=google)
    assert google.events == {} and len(result["deleted"]) == 2
    assert Ledger.load().upcoming() == []


def test_cancelling_unknown_event_without_children_is_skipped(google):
    setup_children((1, "Emma"))
    result = gcal.sync({"events": [{"key": "k1", "cancelled": True}]}, google=google)
    assert google.calls == [] and result["skipped"] == ["aflyst k1 (ikke i kalenderen)"]


def test_correction_without_a_child_removes_it_from_that_childs_calendar(google):
    setup_children((1, "Emma"), (2, "Oscar"))
    gcal.sync({"events": [ev(child_ids=[1, 2], title="Juleklip")]}, google=google)
    emma_cal = store.load_config()["children"]["1"]["calendar_id"]
    lines = []
    gcal.sync({"events": [ev(child_ids=[2], title="Juleklip")]}, dry_run=True, echo=lines.append)
    assert "[prøvekørsel] Slet: 2099-11-12 Juleklip → Emma (ikke længere med)" in lines
    result = gcal.sync({"events": [ev(child_ids=[2], title="Juleklip")]}, google=google)
    assert len(result["updated"]) == 1 and len(result["deleted"]) == 1
    assert len(google.events) == 1 and all(cal != emma_cal for cal, _ in google.events)
    assert [u.child_ids for u in Ledger.load().upcoming()] == [[2]]


def test_cancelling_for_child_without_calendar_creates_no_calendar(google):
    setup_children((1, "Emma"))
    result = gcal.sync({"events": [{"key": "k1", "child_ids": [1], "cancelled": True}]}, google=google)
    assert google.calendars == {} and google.calls == []
    assert result["skipped"] == ["aflyst k1 → Emma (ikke i kalenderen)"]


def test_invalid_doc_changes_nothing(google):
    setup_children((1, "Emma"))
    add_pending("post:1", type="post")
    doc = {"processed": ["post:1"], "events": [ev("ok"), ev("bad", start="i morgen")]}
    with pytest.raises(EventError):
        gcal.sync(doc, google=google)
    assert google.calls == [] and google.calendars == {}
    assert "post:1" in Ledger.load().pending_keys()


def test_dry_run_prints_the_plan_and_touches_nothing():
    setup_children((1, "Emma"))
    add_pending("post:1", type="post")
    lines = []
    doc = {"processed": ["post:1"], "classifications": [{"item": "post:1", "category": "svar"}], "events": [ev(title="Juleklip")]}
    gcal.sync(doc, dry_run=True, google=lambda: pytest.fail("no Google in dry-run"), echo=lines.append)
    assert lines[:2] == ["[prøvekørsel] Classification: post:1 svar", "[prøvekørsel] Opret: 2099-11-12 Juleklip → Emma"]
    assert "post:1" in Ledger.load().pending_keys()
    assert not store.load_config()["children"]["1"].get("calendar_id")


def test_plan_says_update_for_known_events(google):
    setup_children((1, "Emma"))
    gcal.sync({"events": [ev()]}, google=google)
    lines = []
    gcal.sync({"events": [ev()]}, dry_run=True, echo=lines.append)
    assert lines[0].startswith("[prøvekørsel] Opdatér:")


def test_calendar_name_falls_back_to_child_id(google):
    cfg = store.load_config()
    cfg["children"]["7"] = {"name": "", "institution": "Skolen"}
    store.save_config(cfg)
    gcal.sync({"events": [ev(child_ids=[7])]}, google=google)
    assert list(google.calendars.values()) == ["Skole – 7"]


def test_removed_by_user_is_never_recreated(google):
    setup_children((1, "Emma"))
    gcal.sync({"events": [ev()]}, google=google)
    assert gcal.remove_event("k1", 1, google=google) == 1
    assert google.events == {}

    result = gcal.sync({"events": [ev(title="Skovtur (rettet)")]}, google=google)
    assert google.events == {} and not result["created"]
    assert result["skipped"] == ["2099-11-12 Skovtur (rettet) → Emma (fjernet af dig)"]
    assert Ledger.load().removed_upcoming() == [
        {"key": "k1", "child_ids": [1], "title": "Skovtur", "start": "2099-11-12", "end": "2099-11-12"}
    ]


def test_removing_for_one_child_keeps_it_for_the_other(google):
    setup_children((1, "Emma"), (2, "Oscar"))
    gcal.sync({"events": [ev(child_ids=[1, 2])]}, google=google)
    assert gcal.remove_event("k1", 2, google=google) == 1
    assert [u.child_ids for u in Ledger.load().upcoming()] == [[1]] and len(google.events) == 1

    result = gcal.sync({"events": [ev(child_ids=[1, 2], title="Skovtur (rettet)")]}, google=google)
    assert len(result["updated"]) == 1 and not result["created"] and not result["deleted"]
    assert result["skipped"] == ["2099-11-12 Skovtur (rettet) → Oscar (fjernet af dig)"]
    assert len(google.events) == 1 and Ledger.load().is_removed("k1", 2)


def test_removing_without_a_child_removes_it_for_everyone(google):
    setup_children((1, "Emma"), (2, "Oscar"))
    gcal.sync({"events": [ev(child_ids=[1, 2])]}, google=google)
    assert gcal.remove_event("k1", google=google) == 2
    ledger = Ledger.load()
    assert google.events == {} and ledger.is_removed("k1", 1) and ledger.is_removed("k1", 2)


def test_remove_tolerates_event_already_gone_in_google(google):
    setup_children((1, "Emma"))
    gcal.sync({"events": [ev()]}, google=google)
    google.events.clear()  # deleted by hand in Google Calendar
    assert gcal.remove_event("k1", 1, google=google) == 1
    assert Ledger.load().is_removed("k1", 1)


def test_remove_unknown_key():
    with pytest.raises(KeyError):
        gcal.remove_event("nope", google=object())
    with pytest.raises(KeyError):
        gcal.remove_event("k1", 99, google=object())


def test_switching_between_all_day_and_timed_replaces_the_event(google):
    setup_children((1, "Emma"))
    gcal.sync({"events": [ev()]}, google=google)
    result = gcal.sync({"events": [ev(start="2099-11-12T10:00")]}, google=google)
    assert len(result["updated"]) == 1 and len(google.events) == 1
    (body,) = google.bodies()
    assert "dateTime" in body["start"] and "date" not in body["start"]
    assert [c[0] for c in google.calls][-2:] == ["delete", "insert"]
    assert Ledger.load().upcoming()[0].start == "2099-11-12T10:00"


# ----------------------------------------------------------------------------- joint calendars


def setup_joint(*children, calendar_id="joint@group.calendar.google.com"):
    """Children who share one calendar (a joint calendar)."""
    cfg = store.load_config()
    for cid, name in children:
        cfg["children"][str(cid)] = {"name": name, "institution": "Skolen", "calendar_id": calendar_id, "calendar_name": "Skole – fælles"}
    store.save_config(cfg)
    return calendar_id


def test_event_for_two_children_in_a_joint_calendar_is_one_entry(google):
    joint = setup_joint((1, "Emma Jensen"), (2, "Oscar Jensen"))
    lines = []
    gcal.sync({"events": [ev(child_ids=[1, 2], title="Juleklip")]}, dry_run=True, echo=lines.append)
    assert lines[0] == "[prøvekørsel] Opret: 2099-11-12 Juleklip → Emma Jensen, Oscar Jensen"
    assert len(lines) == 2  # and "Markeres som behandlet"

    result = gcal.sync({"events": [ev(child_ids=[1, 2], title="Juleklip")]}, google=google)
    assert len(result["created"]) == 1 and list(google.events) == [(joint, "ev1")]
    ledger = Ledger.load()
    (entry,) = ledger.entries("k1")
    assert entry.calendar_id == joint and entry.child_ids == [1, 2]
    assert [u.child_ids for u in ledger.upcoming()] == [[1, 2]]


def test_correction_without_one_child_updates_the_joint_entry_for_the_other(google):
    joint = setup_joint((1, "Emma"), (2, "Oscar"))
    gcal.sync({"events": [ev(child_ids=[1, 2], title="Juleklip")]}, google=google)
    lines = []
    gcal.sync({"events": [ev(child_ids=[2], title="Juleklip")]}, dry_run=True, echo=lines.append)
    assert lines == ["[prøvekørsel] Opdatér: 2099-11-12 Juleklip → Oscar", "[prøvekørsel] Markeres som behandlet: 0"]

    result = gcal.sync({"events": [ev(child_ids=[2], title="Juleklip")]}, google=google)
    assert len(result["updated"]) == 1 and not result["deleted"]
    assert list(google.events) == [(joint, "ev1")]
    assert [e.child_ids for e in Ledger.load().entries("k1")] == [[2]]


def test_entry_in_a_joint_calendar_is_deleted_when_none_of_its_children_is_left(google):
    setup_joint((1, "Emma"), (2, "Oscar"))
    setup_children((3, "Freja"))
    gcal.sync({"events": [ev(child_ids=[1, 2], title="Juleklip")]}, google=google)
    result = gcal.sync({"events": [ev(child_ids=[3], title="Juleklip")]}, google=google)
    assert result["deleted"] == ["2099-11-12 Juleklip → Emma, Oscar (ikke længere med)"]
    assert len(result["created"]) == 1 and len(google.events) == 1
    assert [e.child_ids for e in Ledger.load().entries("k1")] == [[3]]


def test_cancellation_deletes_from_joint_and_own_calendars(google):
    setup_joint((1, "Emma"), (2, "Oscar"))
    setup_children((3, "Freja"))
    gcal.sync({"events": [ev(child_ids=[1, 2, 3])]}, google=google)
    assert len(google.events) == 2
    result = gcal.sync({"events": [{"key": "k1", "cancelled": True}]}, google=google)
    assert google.events == {} and len(result["deleted"]) == 2
    assert Ledger.load().upcoming() == []


def test_mixed_setup_gives_one_entry_per_calendar(google):
    joint = setup_joint((1, "Emma"), (2, "Oscar"))
    setup_children((3, "Freja"))
    result = gcal.sync({"events": [ev(child_ids=[1, 2, 3], title="Juleklip")]}, google=google)
    assert result["created"] == ["2099-11-12 Juleklip → Emma, Oscar", "2099-11-12 Juleklip → Freja"]
    ida_cal = store.load_config()["children"]["3"]["calendar_id"]
    assert sorted((e.calendar_id, e.child_ids) for e in Ledger.load().entries("k1")) == sorted([(joint, [1, 2]), (ida_cal, [3])])
    assert [u.child_ids for u in Ledger.load().upcoming()] == [[1, 2, 3]]


def test_removing_one_child_in_a_joint_calendar_keeps_the_entry_for_the_other(google):
    joint = setup_joint((1, "Emma"), (2, "Oscar"))
    gcal.sync({"events": [ev(child_ids=[1, 2])]}, google=google)
    assert gcal.remove_event("k1", 1, google=google) == 1
    assert list(google.events) == [(joint, "ev1")]
    ledger = Ledger.load()
    assert ledger.is_removed("k1", 1) and not ledger.is_removed("k1", 2)
    assert [u.child_ids for u in ledger.upcoming()] == [[2]]

    result = gcal.sync({"events": [ev(child_ids=[1, 2], title="Skovtur (rettet)")]}, google=google)
    assert result["skipped"] == ["2099-11-12 Skovtur (rettet) → Emma (fjernet af dig)"]
    assert result["updated"] == ["2099-11-12 Skovtur (rettet) → Oscar"]
    assert [e.child_ids for e in Ledger.load().entries("k1")] == [[2]]

    assert gcal.remove_event("k1", 2, google=google) == 1
    assert google.events == {} and Ledger.load().entries("k1") == []


def test_state_from_the_old_workaround_keeps_its_one_child_without_duplicates(google, data_dir):
    joint = setup_joint((1, "Emma"), (2, "Oscar"))
    google.events[(joint, "e-old")] = {"extendedProperties": {"private": {"aulaAgentKey": "k1"}}}
    store.write_json(data_dir / "state.json", {"events": {f"k1|{joint}": {
        "key": "k1", "child_id": 2, "calendar_id": joint, "event_id": "e-old", "title": "Skovtur",
        "start": "2099-11-12", "end": "2099-11-12", "source": None, "source_item": {}, "link": None,
    }}})
    assert [u.child_ids for u in Ledger.load().upcoming()] == [[2]]  # only the child it knew, until corrected

    result = gcal.sync({"events": [ev(child_ids=[1, 2])]}, google=google)
    assert result["updated"] == ["2099-11-12 Skovtur → Emma, Oscar"] and not result["created"] and not result["deleted"]
    assert list(google.events) == [(joint, "e-old")]
    assert [e.child_ids for e in Ledger.load().entries("k1")] == [[1, 2]]


# ----------------------------------------------------------------------------- event history


def history(item_key):
    return [(h.key, [(line.item, line.action) for line in h.lines]) for h in Ledger.load().events_by_item(item_key)]


def test_event_created_by_one_item_and_corrected_by_another_has_both_in_its_history(google):
    setup_children((1, "Emma"), (2, "Oscar"))
    add_pending("post:1", type="post", title="Skovtur")
    gcal.sync({"processed": ["post:1"], "classifications": [{"item": "post:1", "category": "svar"}], "events": [ev("post:1#1", source="post:1", child_ids=[1, 2])]}, google=google)
    add_pending("message:7:a", type="message", subject="Ny dato")
    gcal.sync(
        {"processed": ["message:7:a"], "classifications": [{"item": "message:7:a", "category": "svar"}],
         "events": [ev("post:1#1", source="message:7:a", child_ids=[1, 2], start="2099-11-13")]},
        google=google,
    )

    lines = [("post:1", "created"), ("message:7:a", "updated")]
    assert history("post:1") == history("message:7:a") == [("post:1#1", lines)]
    (h,) = Ledger.load().events_by_item("post:1")
    assert (h.title, h.start, h.cancelled, h.child_ids) == ("Skovtur", "2099-11-13", False, [1, 2])
    assert all(line.at for line in h.lines)


def test_cancelled_event_stays_in_its_history_but_not_among_upcoming_events(google, data_dir):
    from aula_agent import contract

    setup_children((1, "Emma"), (2, "Oscar"))
    gcal.sync({"events": [ev("post:1#1", source="post:1", child_ids=[1, 2])]}, google=google)
    gcal.sync({"events": [{"key": "post:1#1", "cancelled": True, "source": "message:7:a"}]}, google=google)

    assert google.events == {}
    (h,) = Ledger.load().events_by_item("message:7:a")
    assert (h.key, h.title, h.start, h.cancelled, h.child_ids) == ("post:1#1", "Skovtur", "2099-11-12", True, [1, 2])
    assert [(line.item, line.action) for line in h.lines] == [("post:1", "created"), ("message:7:a", "cancelled")]
    ledger = Ledger.load()
    assert ledger.upcoming() == [] and ledger.synced_count() == 0
    contract.write_inbox(ledger, store.load_config(), [], [])
    assert store.read_json(data_dir / "inbox.json", {})["existing_events"] == []


def test_cancelling_an_unknown_event_adds_no_history(google):
    setup_children((1, "Emma"))
    gcal.sync({"events": [{"key": "k1", "cancelled": True, "source": "post:1"}]}, google=google)
    assert history("post:1") == []


def test_dry_run_adds_no_history(google):
    setup_children((1, "Emma"))
    gcal.sync({"events": [ev(source="post:1")]}, dry_run=True, google=google, echo=lambda _: None)
    assert history("post:1") == []


def test_applying_the_same_correction_again_adds_no_line(google):
    setup_children((1, "Emma"))
    gcal.sync({"events": [ev(source="post:1")]}, google=google)
    correction = {"events": [ev(source="message:7:a", start="2099-11-13")]}
    gcal.sync(correction, google=google)
    gcal.sync(correction, google=google)
    assert history("post:1") == [("k1", [("post:1", "created"), ("message:7:a", "updated")])]


def test_old_entry_corrected_after_upgrade_keeps_its_source_as_creator(google, data_dir):
    setup_children((1, "Emma"))
    gcal.sync({"events": [ev(source="post:1")]}, google=google)
    state = store.read_json(data_dir / "state.json", {})
    del state["history"]  # as written before event history
    store.write_json(data_dir / "state.json", state)
    gcal.sync({"events": [ev(source="message:7:a", start="2099-11-13")]}, google=google)
    (h,) = Ledger.load().events_by_item("message:7:a")
    assert [(line.item, line.action, bool(line.at)) for line in h.lines] == [("post:1", "created", False), ("message:7:a", "updated", True)]


def test_correction_after_the_user_removed_the_event_is_an_update(google):
    setup_children((1, "Emma"), (2, "Oscar"))
    gcal.sync({"events": [ev(source="post:1")]}, google=google)
    gcal.remove_event("k1", 1, google=google)
    gcal.sync({"events": [ev(source="message:7:a", child_ids=[1, 2])]}, google=google)
    assert history("message:7:a") == [("k1", [("post:1", "created"), ("message:7:a", "updated")])]
    assert Ledger.load().events_by_item("post:1")[0].child_ids == [2]


def test_old_entry_cancelled_after_upgrade_keeps_its_children(google, data_dir):
    setup_children((1, "Emma"), (2, "Oscar"))
    gcal.sync({"events": [ev(source="post:1", child_ids=[1, 2])]}, google=google)
    state = store.read_json(data_dir / "state.json", {})
    del state["history"]  # as written before event history
    store.write_json(data_dir / "state.json", state)
    gcal.sync({"events": [{"key": "k1", "cancelled": True}]}, google=google)
    (h,) = Ledger.load().events_by_item("post:1")
    assert (h.cancelled, h.child_ids, h.title) == (True, [1, 2], "Skovtur")


def test_correction_of_an_event_the_user_removed_for_every_child_is_in_its_history(google):
    setup_children((1, "Emma"), (2, "Oscar"))
    gcal.sync({"events": [ev(source="post:1", child_ids=[1, 2])]}, google=google)
    gcal.remove_event("k1", google=google)
    result = gcal.sync({"events": [ev(source="message:7:a", child_ids=[1, 2], start="2099-11-13")]}, google=google)

    assert result["created"] == result["updated"] == [] and len(result["skipped"]) == 2
    assert history("message:7:a") == [("k1", [("post:1", "created"), ("message:7:a", "updated")])]
    (h,) = Ledger.load().events_by_item("message:7:a")
    assert h.start == "2099-11-13"


def test_cancellation_of_an_event_the_user_removed_for_every_child_is_in_its_history(google):
    setup_children((1, "Emma"), (2, "Oscar"))
    gcal.sync({"events": [ev(source="post:1", child_ids=[1, 2])]}, google=google)
    gcal.remove_event("k1", google=google)
    gcal.sync({"events": [{"key": "k1", "cancelled": True, "source": "message:7:a"}]}, google=google)

    (h,) = Ledger.load().events_by_item("message:7:a")
    assert [(line.item, line.action) for line in h.lines] == [("post:1", "created"), ("message:7:a", "cancelled")]
    assert (h.cancelled, h.title, h.child_ids) == (True, "Skovtur", [1, 2])


def test_dry_run_of_a_correction_of_a_removed_event_adds_no_history(google):
    setup_children((1, "Emma"))
    gcal.sync({"events": [ev(source="post:1")]}, google=google)
    gcal.remove_event("k1", google=google)
    gcal.sync({"events": [ev(source="message:7:a", start="2099-11-13")]}, dry_run=True, google=google, echo=lambda _: None)
    assert history("message:7:a") == []
