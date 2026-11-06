"""Indstillinger (/settings) and the warning line on the front page (#52)."""

import datetime as dt
import re

import pytest

from aula_agent import archive, ledger as ledger_module, runs, store
from aula_agent.ledger import Ledger, SyncedEntry
from aula_agent.runs import Outcome
from test_web import auth, classify, configure, make_client, post

TODAY = dt.date(2026, 11, 6)


def get(path):
    configure()
    with make_client(today=lambda: TODAY) as c:
        return c.get(path, headers=auth()).text


def section(html, name):
    m = re.search(rf'<section id="{name}">(.*?)</section>', html, re.S)
    return m and m[1]


# ------------------------------------------------------------------ the warning line


@pytest.fixture
def aula_logged_in(monkeypatch, tmp_path):
    session = tmp_path / "aula-session.json"
    session.write_text("{}")
    monkeypatch.setattr("aula.config.DEFAULT_TOKEN_FILE", session)
    monkeypatch.setattr("aula.config.load_config", lambda: {"mitid_username": "u"})


@pytest.fixture
def aula_logged_out(monkeypatch, tmp_path):
    monkeypatch.setattr("aula.config.DEFAULT_TOKEN_FILE", tmp_path / "no-aula-session.json")


def google_connected():
    store.path("GOOGLE_TOKEN").write_text("{}")


def ran(command, outcome=Outcome.OK, *, hours_ago=1.0):
    """A run in the run log, ``hours_ago`` hours ago."""
    at = dt.datetime.now().astimezone() - dt.timedelta(hours=hours_ago)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ledger_module, "_now", lambda: at.isoformat(timespec="seconds"))
        runs.record(command, outcome, "opdigtet")


def warnings(html):
    """The warning line's entries: (text, the links' targets)."""
    found = re.search(r'<ul id="advarsler"[^>]*>(.*?)</ul>', html, re.S)
    if not found:
        return []
    return [
        (re.sub(r"<[^>]+>", "", li).strip(), re.findall(r'href="([^"]+)"', li))
        for li in re.findall(r"<li>(.*?)</li>", found[1], re.S)
    ]


def a_good_night():
    ran("fetch", hours_ago=3)
    ran("apply", hours_ago=2.9)


def test_with_everything_in_order_the_front_page_has_no_warning(aula_logged_in):
    google_connected()
    a_good_night()
    with archive.open() as a:
        a.save_post(post(1, "Skovtur"))
    html = get("/")
    assert warnings(html) == [] and 'id="advarsler"' not in html


def test_aula_login_missing_or_expired_warns_with_a_link_to_log_in(aula_logged_out):
    google_connected()
    a_good_night()
    assert warnings(get("/")) == [("Aula-login mangler. Log ind", ["/aula/login"])]


def test_an_expired_aula_login_warns_once_with_a_link_to_log_in(aula_logged_in):
    google_connected()
    a_good_night()
    ran("fetch", Outcome.AULA_LOGIN, hours_ago=1)
    assert warnings(get("/")) == [("Aula-login er udløbet. Log ind", ["/aula/login"])]


def test_google_not_connected_warns_with_a_link_to_google(aula_logged_in):
    a_good_night()
    assert warnings(get("/")) == [("Google er ikke forbundet. Forbind", ["/google"])]


def test_google_login_expired_on_the_last_apply_warns_once_until_google_is_connected_again(aula_logged_in):
    google_connected()
    a_good_night()
    ran("apply", Outcome.GOOGLE_LOGIN, hours_ago=1)
    assert warnings(get("/")) == [("Google-login er udløbet. Forbind", ["/google"])]
    ran("google-login", hours_ago=0.5)
    assert warnings(get("/")) == []


@pytest.mark.parametrize("command", ["fetch", "apply"])
def test_the_last_run_failing_warns_with_a_link_to_the_settings_until_one_succeeds(aula_logged_in, command):
    google_connected()
    a_good_night()
    ran(command, Outcome.BAD_EVENTS if command == "apply" else Outcome.ERROR, hours_ago=1)
    [(text, links)] = warnings(get("/"))
    assert text.startswith(f"Sidste kørsel ({command}) fejlede") and links == ["/settings"]
    ran(command, hours_ago=0.5)
    assert warnings(get("/")) == []


def test_no_successful_fetch_for_over_24_hours_warns(aula_logged_in):
    google_connected()
    [(text, links)] = warnings(get("/"))
    assert text.startswith("Der er ikke hentet fra Aula endnu") and links == ["/settings"]

    ran("fetch", Outcome.ERROR, hours_ago=30)
    ran("fetch", hours_ago=25)
    [(text, links)] = warnings(get("/"))
    assert text.startswith("Der er ikke hentet fra Aula i over et døgn") and links == ["/settings"]

    ran("fetch", hours_ago=23)
    assert warnings(get("/")) == []


def test_the_warning_line_stands_on_the_front_page_also_before_the_archive_is_ready_but_not_on_the_settings(aula_logged_out):
    google_connected()
    a_good_night()
    html = get("/")
    assert "ikke klar" in html and [links for _, links in warnings(html)] == [["/aula/login"]]
    assert warnings(get("/settings")) == []


# ------------------------------------------------------------------ Indstillinger


def two_children():
    cfg = store.load_config()
    cfg["children"].update({"1": {"name": "Emma Test"}, "2": {"name": "Oscar Test"}})
    store.save_config(cfg)


def test_the_settings_count_the_items_not_classified_yet_and_the_front_page_does_not():
    with archive.open() as a:
        for pid, title in ((1, "Endnu ikke inddelt"), (2, "Heller ikke inddelt"), (3, "Inddelt")):
            a.save_post(post(pid, title))
        classify(a, "post:3", "info")
    assert "2 endnu ikke inddelt" in get("/settings")
    assert "endnu ikke inddelt" not in get("/")


def test_the_settings_show_whom_each_upcoming_event_was_removed_for_and_nothing_to_undo():
    two_children()
    ledger = Ledger()
    ledger.mark_removed("k1", 1, title="<b>Skovtur</b>", start="2026-11-20", end="2026-11-20")
    ledger.mark_removed("k2", 1, title="Skolefoto", start="2026-11-06", end="2026-11-06")
    ledger.mark_removed("k2", 2, title="Skolefoto", start="2026-11-06", end="2026-11-06")
    ledger.mark_removed("k3", 2, title="Gammel tur", start="2026-10-01", end="2026-10-01")
    ledger.save()
    html = get("/settings")
    removed = section(html, "fjernet")
    per_child = dict(re.findall(r"<h3>(.*?)</h3>\s*<ul[^>]*>(.*?)</ul>", removed, re.S))
    assert list(per_child) == ["Emma", "Oscar"]
    assert "&lt;b&gt;Skovtur&lt;/b&gt;" in per_child["Emma"] and "20.11.2026" in per_child["Emma"]
    assert "Skolefoto" in per_child["Emma"] and "Skolefoto" in per_child["Oscar"]
    assert "Skovtur" not in per_child["Oscar"] and "Gammel tur" not in removed  # over: the agent no longer gets it
    assert "<form" not in removed and "<button" not in removed


def test_the_settings_without_removed_events_say_so():
    assert "Intet fjernet" in section(get("/settings"), "fjernet")


def test_the_settings_say_who_is_logged_in_and_have_no_upcoming_events_or_buttons_to_remove_them():
    two_children()
    ledger = Ledger()
    ledger.record(SyncedEntry("k1", [1, 2], "joint", "e1", "Skovtur", "2026-11-20", "2026-11-20"))
    ledger.save()
    html = get("/settings")
    assert "Logget ind som parent.one@example.com" in html
    assert "Kommende begivenheder" not in html and "Skovtur" not in html
    assert "/events/remove" not in html and not re.search(r"\son[a-z]+=", html)  # no inline handlers left
