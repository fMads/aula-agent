"""Unclassified items in the nightly run: the inbox's ``to_classify`` queue (ADR 0003)."""

import json

from click.testing import CliRunner

from aula_agent import archive, cli, store
from test_aula_source import FakeAula, aula, child, file_att, iso, message, post, run_backfill, run_fetch  # noqa: F401


def inbox():
    return json.loads(store.INBOX_FILE.read_text())


def queued():
    return [i["key"] for i in inbox()["to_classify"]]


def apply(doc, *args):
    path = store.DATA_DIR / "events-test.json"
    path.write_text(json.dumps(doc))
    return CliRunner().invoke(cli.main, ["apply", str(path), *args])


def info(item, **c):
    return {"item": item, "category": "info", "topics": ["skema"], "summary": "Skemaet for skoleåret.", **c}


TOPIC = {"skema": "Skema og mødetider"}


def test_backfilled_items_are_in_to_classify_and_gone_once_classified(aula):
    threads = [{"id": 7, "subject": "Skolefest", "lastMessageDate": iso(200)}]
    msgs = {"7": [message("a", 200, sender={"fullName": "Lærer Hansen"}, attachments=[file_att(5, "program.pdf")])]}
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skema", 120)], threads, msgs)
    run_backfill()
    run_fetch()  # both are older than the cutoff: archived, never pending

    assert inbox()["items"] == []
    by_key = {i["key"]: i for i in inbox()["to_classify"]}
    assert list(by_key) == ["message:7:a", "post:1"]  # oldest first
    msg, p = by_key["message:7:a"], by_key["post:1"]
    assert (msg["type"], msg["thread_id"], msg["subject"], msg["sender"], msg["child_ids"]) == (
        "message", "7", "Skolefest", "Lærer Hansen", [1]
    )
    assert msg["body"] == "a" and msg["sent"].startswith(iso(200)[:10])
    assert msg["attachments"] == [
        {"name": "program.pdf", "kind": "file", "path": "attachments/message_7_a/5-program.pdf"}
    ]
    assert (p["type"], p["title"], p["author"], p["groups"], p["body"]) == (
        "post", "Skema", "Lærer Hansen", ["2.A"], "Hej"
    )
    assert p["published"].startswith(iso(120)[:10])
    assert isinstance(p["archive_version"], int) and p["sensitive"] is False

    r = apply({"processed": [], "events": [], "classifications": [info("post:1")], "new_topics": TOPIC})

    assert r.exit_code == 0, r.output
    run_fetch()
    assert queued() == ["message:7:a"]


def test_at_most_100_oldest_first_and_pending_items_do_not_count(aula):
    new = [post(i, f"Nyt {i}", i) for i in range(1, 3)]  # within the cutoff: pending
    edited = post(999, "Rettet", 300, edited=iso(1))  # old, but edited yesterday: pending and oldest
    old = [post(i, f"Gammelt {i}", i) for i in range(40, 141)]  # 101 archived, unclassified
    aula["client"] = FakeAula([child(1, "Emma")], [*new, edited, *old], [], {})
    run_fetch()

    assert sorted(i["key"] for i in inbox()["items"]) == ["post:1", "post:2", "post:999"]
    assert queued() == [f"post:{i}" for i in range(140, 40, -1)]  # 100, the newest one waits


def test_a_new_version_of_a_classified_item_is_queued_again(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skema", 120)], [], {})
    run_fetch()
    first = inbox()["to_classify"][0]["archive_version"]
    apply({"processed": [], "events": [], "classifications": [info("post:1")], "new_topics": TOPIC})
    run_fetch()
    assert queued() == []

    aula["client"].posts = [post(1, "Skema (rettet)", 120, html="<p>Nyt skema</p>")]  # still older than the cutoff
    run_fetch()

    assert inbox()["items"] == []
    [again] = inbox()["to_classify"]
    assert (again["key"], again["title"], again["body"]) == ("post:1", "Skema (rettet)", "Nyt skema")
    assert again["archive_version"] != first


def test_an_event_from_a_queued_item_is_rejected(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skolefest", 120)], [], {})
    run_fetch()
    event = {"key": "post:1#1", "source": "post:1", "child_ids": [1], "title": "Skolefest", "start": "2099-06-01"}

    r = apply({"processed": [], "events": [event], "classifications": [info("post:1")], "new_topics": TOPIC})

    assert r.exit_code == cli.EXIT_BAD_EVENTS
    assert "post:1 er fra to_classify og må ikke give events" in r.output
    with archive.open() as a:
        assert a.item("post:1").classification is None


def test_a_queued_item_may_be_left_unclassified_and_stays_in_the_queue(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skema", 120), post(2, "Lus", 100)], [], {})
    run_fetch()

    r = apply({"processed": [], "events": [], "classifications": [info("post:1")], "new_topics": TOPIC})

    assert r.exit_code == 0, r.output
    run_fetch()
    assert queued() == ["post:2"]


def test_a_queued_item_is_classified_in_the_version_the_agent_got(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skema", 120)], [], {})
    run_fetch()
    aula["client"].posts = [post(1, "Skema (rettet)", 120, html="<p>Nyt skema</p>")]
    with archive.open() as a:  # a new version arrives (backfill) after the agent read the inbox
        a.save_post(aula["client"].posts[0]._raw)

    apply({"processed": [], "events": [], "classifications": [info("post:1")], "new_topics": TOPIC})

    with archive.open() as a:
        first, second = a.item("post:1").versions
        assert first.classification.category == "info" and second.classification is None


def test_a_queued_item_from_a_sensitive_thread_gets_no_summary(aula):
    threads = [{"id": 5, "subject": "Fortroligt", "lastMessageDate": iso(200), "sensitive": True}]
    aula["client"] = FakeAula([child(1, "Emma")], [], threads, {"5": [message("a", 200)]})
    run_backfill()
    run_fetch()
    assert inbox()["to_classify"][0]["sensitive"] is True

    r = apply({"processed": [], "events": [], "classifications": [info("message:5:a")], "new_topics": TOPIC})

    assert r.exit_code == cli.EXIT_BAD_EVENTS and "følsom tråd – intet resumé" in r.output


def test_reclassify_puts_every_item_in_to_classify_again_and_keeps_the_topics(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skema", 120), post(2, "Skema igen", 100)], [], {})
    run_fetch()
    apply({"processed": [], "events": [], "classifications": [info("post:1"), info("post:2")], "new_topics": TOPIC})
    run_fetch()
    assert queued() == []

    r = CliRunner().invoke(cli.main, ["reclassify"])

    assert r.exit_code == 0, r.output
    assert r.output.startswith("2 classifications slettet.")
    run_fetch()
    assert queued() == ["post:1", "post:2"]
    assert [(t["name"], t["items"]) for t in inbox()["topics"]] == [("skema", 0)]
    from aula_agent.ledger import Ledger
    assert Ledger.load().last_run("reclassify")["outcome"] == "ok"
