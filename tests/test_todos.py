"""Todos: from the agent's classification through ``apply`` to "Skal gøres" on /overblik, until ticked off."""

import datetime as dt
import json
import re

from click.testing import CliRunner

from aula_agent import cli, store
from aula_agent.ledger import Ledger
from test_aula_source import FakeAula, aula, child, iso, message, post, run_fetch  # noqa: F401  (aula is a fixture)
from test_web import ORIGIN, auth, configure, make_client

TODAY = dt.date(2026, 11, 6)  # the web page's "today"; due dates are far from it either way
TOPIC = {"tur": "Ture ud af huset"}


def apply(doc):
    path = store.DATA_DIR / "events-test.json"
    path.write_text(json.dumps(doc))
    return CliRunner().invoke(cli.main, ["apply", str(path)])


def trip(item, todo=None, **c):
    return {"item": item, "category": "arrangement", "topics": ["tur"], "summary": "Tur i skoven.",
            **({"todo": todo} if todo else {}), **c}


def classify(*classifications, processed=None):
    keys = [c["item"] for c in classifications] if processed is None else processed
    r = apply({"processed": keys, "events": [], "classifications": list(classifications), "new_topics": TOPIC})
    assert r.exit_code == 0, r.output


def overview(today=TODAY):
    configure()
    with make_client(today=lambda: today) as c:
        return c.get("/overblik", headers=auth()).text


def to_do(html):
    """The todo texts under "Skal gøres", in order."""
    return re.findall(r'<span class="todo">(.*?)</span>', _between(html, "<h2>Skal gøres", "<details"))


def done(html):
    """The todo texts under the folded "Klaret", in order."""
    return re.findall(r'<span class="todo">(.*?)</span>', _between(html, "<details", "</details>"))


def _between(html, start, end):
    part = html[html.index(start):]
    return part[:part.index(end)]


def press(button, item, version):
    configure()
    with make_client(today=lambda: TODAY) as c:
        r = c.post(f"/todos/{button}", data={"item": item, "version": str(version)},
                   headers={**auth(), "Origin": ORIGIN}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/overblik"), r.headers.get("location")


def version_of(html, text):
    """The (item, version) the "Klaret"/"Fortryd" form next to a todo posts."""
    li = next(li for li in html.split("<li") if f'<span class="todo">{text}</span>' in li)
    return re.search(r'name="item" value="([^"]+)"', li)[1], int(re.search(r'name="version" value="(\d+)"', li)[1])


def test_a_current_todo_is_open_after_apply_and_klaret_and_fortryd_move_it(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 1)], [], {})
    run_fetch()
    classify(trip("post:1", {"text": "Betal 50 kr. til turen", "due": "2099-11-20"}))

    html = overview()
    assert to_do(html) == ["Betal 50 kr. til turen"] and done(html) == []
    assert "Skovtur" in _between(html, "<h2>Skal gøres", "<details") and "20.11.2099" in html
    assert html.index("<h2>Skal gøres") < html.index("<h2>Arrangement")

    press("check", *version_of(html, "Betal 50 kr. til turen"))
    html = overview()
    assert to_do(html) == [] and done(html) == ["Betal 50 kr. til turen"]

    press("uncheck", *version_of(html, "Betal 50 kr. til turen"))
    assert to_do(overview()) == ["Betal 50 kr. til turen"]


def test_a_todo_that_was_not_current_when_it_was_classified_starts_as_done(aula):
    posts = [post(1, "Tur i går", 2), post(2, "Ny tur", 3), post(3, "Gammel tur", 15), post(4, "Ældgammel", 120)]
    aula["client"] = FakeAula([child(1, "Emma")], posts, [], {})
    run_fetch()  # 1-3 pending, 4 in to_classify
    classify(
        trip("post:1", {"text": "Betal turen", "due": "2020-01-01"}),  # passed before it was classified
        trip("post:2", {"text": "Medbring madpakke"}),  # no due, 3 days old
        trip("post:3", {"text": "Aflever sedlen"}),  # no due, 15 days old
        trip("post:4", {"text": "Tilmeld i SFO"}),  # from the queue, 120 days old
        processed=["post:1", "post:2", "post:3"],
    )

    html = overview()
    assert to_do(html) == ["Medbring madpakke"]
    assert sorted(done(html)) == ["Aflever sedlen", "Betal turen", "Tilmeld i SFO"]

    press("uncheck", *version_of(html, "Tilmeld i SFO"))  # "Fortryd" opens one that started as done
    assert to_do(overview()) == ["Medbring madpakke", "Tilmeld i SFO"]


def test_an_open_todo_past_its_due_stays_as_overdue_first_and_those_without_due_come_last(aula):
    posts = [post(i, f"Tur {i}", 1) for i in range(1, 5)]
    aula["client"] = FakeAula([child(1, "Emma")], posts, [], {})
    run_fetch()
    classify(
        trip("post:1", {"text": "Medbring regntøj"}),
        trip("post:2", {"text": "Betal turen", "due": "2099-03-01"}),
        trip("post:3", {"text": "Tilmeld turen", "due": "2099-01-10"}),
        trip("post:4", {"text": "Aflever sedlen", "due": "2099-02-01"}),
    )

    later = dt.date(2099, 2, 15)
    html = overview(today=later)
    assert to_do(html) == ["Tilmeld turen", "Aflever sedlen", "Betal turen", "Medbring regntøj"]
    overdue = [li for li in html.split("<li") if "overskredet" in li]
    assert len(overdue) == 2 and "Tilmeld turen" in overdue[0] and "Aflever sedlen" in overdue[1]
    assert "overskredet" not in overview()  # before the due dates


def test_a_new_version_of_the_item_gives_an_open_todo_again(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 2)], [], {})
    run_fetch()
    classify(trip("post:1", {"text": "Betal 50 kr. til turen", "due": "2099-11-20"}))
    press("check", *version_of(overview(), "Betal 50 kr. til turen"))
    assert to_do(overview()) == []

    aula["client"].posts = [post(1, "Skovtur (rettet)", 2, html="<p>Nu 60 kr.</p>", edited=iso(0.5))]
    run_fetch()
    classify(trip("post:1", {"text": "Betal 60 kr. til turen", "due": "2099-11-20"}))

    html = overview()
    assert to_do(html) == ["Betal 60 kr. til turen"] and done(html) == []
    assert Ledger.load().todo_checked(*version_of(html, "Betal 60 kr. til turen")) is False


def test_a_todo_from_a_sensitive_thread_shows_only_that_it_is_sensitive_and_the_button(aula):
    threads = [{"id": 5, "subject": "Samtale om skoledagen", "lastMessageDate": iso(1), "sensitive": True}]
    aula["client"] = FakeAula([child(1, "Emma")], [], threads, {"5": [message("a")]})
    run_fetch()
    classify(trip("message:5:a", {"text": "Ring til skolen om samtalen"}, summary=None))

    part = _between(overview(), "<h2>Skal gøres", "<details")
    assert "🔒 Følsom tråd" in part and "Klaret" in part
    assert "Ring til skolen" not in part and "Samtale om skoledagen" not in part


def test_klaret_needs_same_origin_and_an_existing_todo(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 1)], [], {})
    run_fetch()
    classify(trip("post:1", {"text": "Betal turen"}))
    item, version = version_of(overview(), "Betal turen")
    configure()
    with make_client(today=lambda: TODAY) as c:
        for headers in ({**auth(), "Origin": "https://evil.example"}, auth()):
            r = c.post("/todos/check", data={"item": item, "version": str(version)}, headers=headers)
            assert r.status_code == 403
        r = c.post("/todos/check", data={"item": item, "version": str(version + 1)},
                   headers={**auth(), "Origin": ORIGIN}, follow_redirects=False)
        assert "findes ikke" in r.headers["location"].replace("%20", " ")
        with store.lock():
            r = c.post("/todos/check", data={"item": item, "version": str(version)},
                       headers={**auth(), "Origin": ORIGIN}, follow_redirects=False)
        assert "Pr%C3%B8v%20igen" in r.headers["location"]
    assert to_do(overview()) == ["Betal turen"]
    assert Ledger.load().todo_checked(item, version + 1) is False
