"""Classification of pending items: from the inbox through ``apply`` into the archive (ADR 0003)."""

import datetime as dt
import json

from click.testing import CliRunner

from aula_agent import archive, cli, store
from aula_agent.credentials import GoogleLoginRequired
from test_aula_source import FakeAula, aula, child, iso, message, post, run_fetch  # noqa: F401  (aula is a fixture)


def inbox():
    return json.loads(store.INBOX_FILE.read_text())


def apply(doc, *args):
    path = store.DATA_DIR / "events-test.json"
    path.write_text(json.dumps(doc))
    return CliRunner().invoke(cli.main, ["apply", str(path), *args])


def swimming(item="post:1", **c):
    return {
        "item": item, "category": "periode", "span": {"start": "2099-11-02", "end": "2099-11-23"},
        "topics": ["svømning"], "summary": "Svømning mandage i november, husk badetøj.", **c,
    }


def classified(key):
    with archive.open() as a:
        return a.item(key).classification


def test_apply_saves_the_classification_for_the_items_version(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Svømning", 1)], [], {})
    run_fetch()

    r = apply({"processed": ["post:1"], "events": [], "classifications": [swimming()],
               "new_topics": {"svømning": "Svømmeundervisning og badetøj"}})

    assert r.exit_code == 0, r.output
    c = classified("post:1")
    assert (c.category, c.span_start, c.span_end) == ("periode", dt.date(2099, 11, 2), dt.date(2099, 11, 23))
    assert c.topics == ("svømning",) and c.summary == "Svømning mandage i november, husk badetøj."
    with archive.open() as a:
        assert a.item("post:1").versions[-1].classification == c


def test_a_processed_item_without_classification_is_rejected_and_nothing_is_saved(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Svømning", 1)], [], {})
    run_fetch()

    r = apply({"processed": ["post:1"], "events": []})

    assert r.exit_code == cli.EXIT_BAD_EVENTS and "post:1: classification mangler" in r.output
    assert classified("post:1") is None


def test_the_classification_is_saved_when_the_google_login_is_missing(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Svømning", 1)], [], {})
    run_fetch()
    aula["google"] = GoogleLoginRequired("Ikke forbundet")

    r = apply({"processed": ["post:1"], "events": [], "classifications": [swimming()],
               "new_topics": {"svømning": "Svømmeundervisning"}})

    assert r.exit_code == cli.EXIT_GOOGLE_LOGIN
    assert classified("post:1").category == "periode"


def test_a_dry_run_shows_the_classifications_and_saves_nothing(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Svømning", 1)], [], {})
    run_fetch()

    r = apply({"processed": ["post:1"], "events": [],
               "classifications": [swimming(todo={"text": "Køb badehætte", "due": "2099-11-01"})],
               "new_topics": {"svømning": "Svømmeundervisning"}}, "--dry-run")

    assert r.exit_code == 0, r.output
    assert "[prøvekørsel] Classification: post:1 periode 2099-11-02–2099-11-23 todo: Køb badehætte (2099-11-01)" in r.output
    assert "[prøvekørsel] Nyt topic: svømning" in r.output
    assert classified("post:1") is None
    with archive.open() as a:
        assert a.topics() == []


def test_a_new_topic_is_in_the_next_inbox_with_its_count(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Svømning", 2), post(2, "Svømning igen", 1)], [], {})
    run_fetch()
    assert inbox()["topics"] == []

    apply({"processed": ["post:1", "post:2"], "events": [],
           "classifications": [swimming("post:1"), swimming("post:2", topics=["svømning", "lus"])],
           "new_topics": {"svømning": "Svømmeundervisning", "lus": "Lus i klassen"}})
    run_fetch()

    assert inbox()["topics"] == [
        {"name": "lus", "description": "Lus i klassen", "items": 1},
        {"name": "svømning", "description": "Svømmeundervisning", "items": 2},
    ]


def test_aulas_children_win_over_the_agents(aula):
    threads = [
        {"id": 1, "subject": "Tur", "lastMessageDate": iso(1)},
        {"id": 2, "subject": "Tur", "lastMessageDate": iso(1), "regardingChildren": [{"displayName": "Oscar"}]},
    ]
    aula["client"] = FakeAula(
        [child(1, "Emma"), child(2, "Oscar")], [], threads, {"1": [message("a")], "2": [message("b")]}
    )
    run_fetch()

    r = apply({
        "processed": ["message:1:a", "message:2:b"], "events": [],
        "classifications": [swimming("message:1:a", child_ids=[1]), swimming("message:2:b", child_ids=[1])],
        "new_topics": {"svømning": "Svømmeundervisning"},
    })

    assert r.exit_code == 0, r.output
    with archive.open() as a:
        assert a.item("message:1:a").child_ids == [1]  # Aula set none: the agent's
        assert a.item("message:2:b").child_ids == [2]  # Aula's


def test_a_sensitive_thread_is_marked_in_the_inbox_and_gets_no_summary(aula):
    threads = [{"id": 5, "subject": "Fortroligt", "lastMessageDate": iso(1), "sensitive": True}]
    aula["client"] = FakeAula([child(1, "Emma")], [], threads, {"5": [message("a")]})
    run_fetch()
    assert inbox()["items"][0]["sensitive"] is True

    r = apply({"processed": ["message:5:a"], "events": [], "classifications": [swimming("message:5:a")],
               "new_topics": {"svømning": "Svømmeundervisning"}})
    assert r.exit_code == cli.EXIT_BAD_EVENTS and "følsom tråd – intet resumé" in r.output

    r = apply({"processed": ["message:5:a"], "events": [], "classifications": [swimming("message:5:a", summary=None)],
               "new_topics": {"svømning": "Svømmeundervisning"}})
    assert r.exit_code == 0, r.output
    assert classified("message:5:a").summary is None


def test_the_classification_belongs_to_the_version_the_agent_got(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Svømning", 1)], [], {})
    run_fetch()
    aula["client"].posts = [post(1, "Svømning (rettet)", 1, html="<p>Rettet</p>", edited=iso(0.5))]
    with archive.open() as a:  # a new version arrives (backfill) after the agent read the inbox
        a.save_post(aula["client"].posts[0]._raw)

    apply({"processed": ["post:1"], "events": [], "classifications": [swimming()],
           "new_topics": {"svømning": "Svømmeundervisning"}})

    with archive.open() as a:
        first, second = a.item("post:1").versions
        assert first.classification.category == "periode" and second.classification is None
        assert a.item("post:1").classification.category == "periode"  # the newest one there is
        assert [i.key for i in a.unclassified()] == ["post:1"]  # its newest version still lacks one


def archive_posts(*specs):
    """Posts straight into the archive (as backfill does): (id, days ago)."""
    with archive.open() as a:
        for pid, days_ago in specs:
            a.save_post(post(pid, f"Opslag {pid}", days_ago)._raw)


def test_unclassified_items_are_oldest_first_without_the_excluded_ones():
    archive_posts((1, 3), (2, 10), (3, 1), (4, 5))
    with archive.open() as a:
        a.create_topic("svømning", "Svømmeundervisning")
        a.classify("post:4", archive.Classification(archive.Category.INFO, topics=("svømning",), summary="Skema"))

        assert [i.key for i in a.unclassified()] == ["post:2", "post:1", "post:3"]
        assert [i.key for i in a.unclassified(exclude=["post:2"], limit=1)] == ["post:1"]


def test_a_topic_counts_the_items_whose_newest_classification_has_it():
    archive_posts((1, 3), (2, 2))
    with archive.open() as a:
        assert a.create_topic("lus", "Lus i klassen") is True
        assert a.create_topic("lus", "Noget andet") is False  # exists: the description stays
        a.create_topic("svømning", "Svømmeundervisning")
        a.classify("post:1", archive.Classification(archive.Category.ORIENTERING, topics=("lus",), summary="Lus"))
        a.classify("post:2", archive.Classification(archive.Category.ORIENTERING, topics=("lus",), summary="Lus"))
        a.classify("post:2", archive.Classification(archive.Category.PERIODE, topics=("svømning",), summary="Svøm"))

        assert [(t.name, t.description, t.items) for t in a.topics()] == [
            ("lus", "Lus i klassen", 1), ("svømning", "Svømmeundervisning", 1),
        ]
        assert a.classify("post:99", archive.Classification(archive.Category.SVAR)) is False


def test_an_archive_from_before_classifications_gets_them_and_keeps_its_items(data_dir):
    import sqlite3

    archive_posts((1, 3))
    db = sqlite3.connect(store.path("ARCHIVE_FILE"))
    with db:
        db.executescript("DROP TABLE classification_topics; DROP TABLE classifications; DROP TABLE topics;"
                         " ALTER TABLE versions DROP COLUMN first_classified_at;")
        db.execute("PRAGMA user_version = 3")
    db.close()

    with archive.open() as a:
        assert [i.key for i in a.unclassified()] == ["post:1"] and a.topics() == []


def test_apply_saves_the_todo_with_the_classification(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Svømning", 1), post(2, "Lus", 1)], [], {})
    run_fetch()

    r = apply({"processed": ["post:1", "post:2"], "events": [],
               "classifications": [swimming(todo={"text": "Betal 50 kr. for svømning", "due": "2099-11-01"}),
                                   swimming("post:2")],
               "new_topics": {"svømning": "Svømmeundervisning"}})

    assert r.exit_code == 0, r.output
    assert classified("post:1").todo == archive.Todo("Betal 50 kr. for svømning", dt.date(2099, 11, 1))
    assert classified("post:2").todo is None
    with archive.open() as a:
        [t] = a.todos()
        item = a.item("post:1")
    assert (t.item.key, t.version.id, t.text, t.due) == ("post:1", item.latest.id, "Betal 50 kr. for svømning",
                                                          dt.date(2099, 11, 1))
    assert t.classified_at == item.latest.classification.saved_at


def test_the_todos_are_the_newest_classifications():
    archive_posts((1, 3), (2, 3))
    with archive.open() as a:
        a.create_topic("tur", "Ture ud af huset")
        a.classify("post:1", archive.Classification(archive.Category.FRIST, topics=("tur",), summary="Tur",
                                                    todo=archive.Todo("Tilmeld")))
        a.classify("post:2", archive.Classification(archive.Category.FRIST, topics=("tur",), summary="Tur",
                                                    todo=archive.Todo("Betal")))
        a.classify("post:2", archive.Classification(archive.Category.INFO, topics=("tur",), summary="Tur aflyst"))

        assert [(t.item.key, t.text) for t in a.todos()] == [("post:1", "Tilmeld")]


def test_an_archive_from_before_todos_keeps_its_classifications(data_dir):
    import sqlite3

    archive_posts((1, 3))
    with archive.open() as a:
        a.create_topic("lus", "Lus i klassen")
        a.classify("post:1", archive.Classification(archive.Category.ORIENTERING, topics=("lus",), summary="Lus"))
    db = sqlite3.connect(store.path("ARCHIVE_FILE"))
    with db:  # as schema 4 had it
        db.execute("ALTER TABLE classifications DROP COLUMN todo_text")
        db.execute("ALTER TABLE classifications DROP COLUMN todo_due")
        db.execute("ALTER TABLE versions DROP COLUMN first_classified_at")
        db.execute("PRAGMA user_version = 4")
    db.close()

    with archive.open() as a:
        c = a.item("post:1").classification
        assert (c.summary, c.todo) == ("Lus", None) and a.todos() == []


def test_an_archive_from_before_first_classified_counts_its_classifications_as_the_first(data_dir):
    import sqlite3

    archive_posts((1, 3))
    with archive.open(at=dt.datetime(2026, 11, 2, 9, 0).astimezone()) as a:
        a.create_topic("tur", "Ture ud af huset")
        a.classify("post:1", archive.Classification(archive.Category.FRIST, topics=("tur",), summary="Tur",
                                                    todo=archive.Todo("Tilmeld")))
    db = sqlite3.connect(store.path("ARCHIVE_FILE"))
    with db:  # as schema 5 had it
        db.execute("ALTER TABLE versions DROP COLUMN first_classified_at")
        db.execute("PRAGMA user_version = 5")
    db.close()

    with archive.open(at=dt.datetime(2026, 11, 20, 9, 0).astimezone()) as a:
        a.classify("post:1", archive.Classification(archive.Category.FRIST, topics=("tur",), summary="Tur igen",
                                                    todo=archive.Todo("Tilmeld")))
        [t] = a.todos()
    assert t.classified_at.startswith("2026-11-02")
