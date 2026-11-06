import datetime as dt

from aula_agent import store
from aula_agent.ledger import Ledger, SyncedEntry

TODAY = "2026-11-05"


def entry(key, child_id, calendar_id, start, end=None, **kw):
    return SyncedEntry(key, [child_id], calendar_id, f"e-{key}-{child_id}", kw.pop("title", key), start, end or start, **kw)


def test_round_trips_through_state_json(data_dir):
    ledger = Ledger()
    ledger.record(entry("k1", 1, "c1", "2026-12-01"))
    ledger.last_fetch = "2026-11-05T21:00:00+01:00"
    ledger.save()
    again = Ledger.load()
    assert again.synced("k1", "c1").event_id == "e-k1-1"
    assert again.last_fetch == "2026-11-05T21:00:00+01:00"


def test_reads_state_written_by_older_versions(data_dir):
    store.write_json(
        data_dir / "state.json",
        {"events": {"k|c": {"key": "k", "child_id": 1, "calendar_id": "c", "event_id": "e", "title": "T", "start": "2099-01-01",
                            "end": "2099-01-01", "source": None, "source_item": {}, "link": None, "extra": "ignored"}},
         "ignored": {"r": {"title": "R", "start": "2099-01-01", "removed_at": "x"}},
         "runs": [{"at": "x", "command": "fetch", "ok": False, "summary": "Fejl"}]},
    )
    ledger = Ledger.load()
    assert ledger.synced("k", "c").title == "T"
    assert ledger.removed_upcoming() == [{"key": "r", "child_ids": None, "title": "R", "start": "2099-01-01", "end": "2099-01-01"}]
    assert ledger.is_removed("r", 1) and ledger.is_removed("r", 2)  # removed before removal was per child: everyone
    assert ledger.last_run("fetch")["outcome"] == "error"


def test_old_login_failures_keep_their_meaning(data_dir):
    store.write_json(data_dir / "state.json", {"runs": [
        {"at": "x", "command": "fetch", "ok": False, "summary": "Aula-login mangler: Den gemte Aula-session er udløbet."},
        {"at": "y", "command": "apply", "ok": False, "summary": "Google-login mangler: udløbet"},
    ]})
    ledger = Ledger.load()
    assert ledger.last_run("fetch")["outcome"] == "aula_login"
    assert ledger.last_run("apply")["outcome"] == "google_login"


def test_upcoming_groups_children_and_keeps_events_until_their_last_day():
    ledger = Ledger()
    ledger.record(entry("trip", 1, "c1", "2026-11-04", "2026-11-06"))  # started yesterday, not over
    ledger.record(entry("trip", 2, "c2", "2026-11-04", "2026-11-06"))
    ledger.record(entry("party", 1, "c1", "2026-11-05T17:00", "2026-11-05T19:00"))
    ledger.record(entry("old", 1, "c1", "2026-11-04"))
    up = ledger.upcoming(TODAY)
    assert [(u.key, u.child_ids) for u in up] == [("trip", [1, 2]), ("party", [1])]


def test_removed_upcoming_uses_the_last_day():
    ledger = Ledger()
    ledger.mark_removed("camp", 1, title="Lejrskole", start="2026-11-03", end="2026-11-07")
    ledger.mark_removed("past", 1, title="Fortid", start="2026-11-01", end="2026-11-02")
    assert [r["key"] for r in ledger.removed_upcoming(TODAY)] == ["camp"]
    assert ledger.is_removed("past", 1)


def test_removal_is_per_child():
    ledger = Ledger()
    ledger.mark_removed("cup", 2, title="Stævne", start="2099-01-01", end="2099-01-01")
    assert ledger.is_removed("cup", 2) and ledger.is_removed("cup", "2")
    assert not ledger.is_removed("cup", 1)
    ledger.mark_removed("cup", 1, title="Stævne", start="2099-01-01", end="2099-01-01")
    assert ledger.removed_upcoming()[0]["child_ids"] == [1, 2]


def test_pending_merges_children_and_processing_reports_what_waited():
    ledger = Ledger()
    ledger.add_pending({"key": "message:1:a", "child_ids": [2], "sent": "2026-11-02"})
    ledger.add_pending({"key": "message:1:a", "child_ids": [1], "sent": "2026-11-02", "body": "rettet"})
    ledger.add_pending({"key": "post:1", "child_ids": [], "published": "2026-11-01"})
    assert [i["key"] for i in ledger.pending()] == ["post:1", "message:1:a"]  # oldest first
    assert ledger.pending_item("message:1:a")["child_ids"] == [1, 2]
    assert ledger.mark_processed(["post:1", "unknown"]) == ["post:1"]
    assert ledger.pending_keys() == {"message:1:a"}


def test_source_item_for_synced_key():
    ledger = Ledger()
    ledger.record(entry("k", 1, "c1", "2026-12-01", source_item={"title": "Opslag"}))
    assert ledger.source_item_for("k") == {"title": "Opslag"}
    assert ledger.source_item_for("other") is None


def test_run_log_keeps_the_newest_fifty():
    ledger = Ledger()
    for i in range(55):
        ledger.log_run("fetch", "ok", str(i))
    ledger.log_run("aula-login", "aula_login", "udløbet")
    runs = ledger.runs()
    assert len(runs) == 50 and runs[0]["summary"] == "udløbet" and runs[0]["ok"] is False
    assert ledger.last_run("fetch")["summary"] == "54"
    assert ledger.last_run("apply") is None


def test_today_default_is_today():
    ledger = Ledger()
    today = dt.date.today().isoformat()
    ledger.record(entry("k", 1, "c1", today))
    assert len(ledger.upcoming()) == 1


def test_entries_from_before_joint_calendars_keep_their_one_child(data_dir):
    store.write_json(data_dir / "state.json", {"events": {
        "k|c1": {"key": "k", "child_id": 1, "calendar_id": "c1", "event_id": "e1", "title": "T", "start": "2099-01-01", "end": "2099-01-01"},
        "k|c2": {"key": "k", "child_id": 2, "calendar_id": "c2", "event_id": "e2", "title": "T", "start": "2099-01-01", "end": "2099-01-01"},
    }})
    ledger = Ledger.load()
    assert ledger.synced("k", "c1").child_ids == [1] and ledger.synced("k", "c2").child_ids == [2]
    assert [u.child_ids for u in ledger.upcoming()] == [[1, 2]]
    ledger.save()
    assert Ledger.load().synced("k", "c2").child_ids == [2]


def test_entries_from_before_event_history_were_created_by_their_source(data_dir):
    store.write_json(data_dir / "state.json", {"events": {
        "post:1#1|c1": {"key": "post:1#1", "child_ids": [1], "calendar_id": "c1", "event_id": "e1", "title": "Skovtur",
                        "start": "2099-01-01", "end": "2099-01-01", "source": "post:1"},
        "post:1#1|c2": {"key": "post:1#1", "child_id": 2, "calendar_id": "c2", "event_id": "e2", "title": "Skovtur",
                        "start": "2099-01-01", "end": "2099-01-01", "source": "post:1"},
    }})
    (h,) = Ledger.load().events_by_item("post:1")
    assert (h.key, h.title, h.start, h.cancelled, h.child_ids) == ("post:1#1", "Skovtur", "2099-01-01", False, [1, 2])
    assert [(line.item, line.action, line.at) for line in h.lines] == [("post:1", "created", None)]


def test_history_is_kept_in_state_json(data_dir):
    ledger = Ledger()
    ledger.record(entry("post:1#1", 1, "c1", "2026-12-01", source="post:1"))
    ledger.add_history("post:1#1", "created", "post:1", title="Skovtur", start="2026-12-01", end="2026-12-01")
    ledger.save()
    (h,) = Ledger.load().events_by_item("post:1")
    assert [(line.item, line.action) for line in h.lines] == [("post:1", "created")]
    assert Ledger.load().events_by_item("post:2") == []


def test_a_todo_is_ticked_off_per_item_version_and_can_be_undone(data_dir):
    ledger = Ledger()
    assert not ledger.todo_checked("post:1", 7) and ledger.todo_checked("post:1", 7, by_default=True)

    ledger.check_todo("post:1", 7)
    ledger.save()
    ledger = Ledger.load()
    assert ledger.todo_checked("post:1", 7, by_default=False) and ledger.todo_checked_at("post:1", 7)
    assert not ledger.todo_checked("post:1", 8)  # a new version of the item: open again
    assert not ledger.todo_checked("post:2", 7)

    ledger.uncheck_todo("post:1", 7)
    assert not ledger.todo_checked("post:1", 7, by_default=True)  # undone stays open, also one that started as done
    assert ledger.todo_checked_at("post:1", 7) is None
