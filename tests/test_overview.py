"""The overview's timeline on /overblik (trin 2): Gælder nu, Kommende week by week, Stående info, Tidligere."""

import datetime as dt
import re

from aula_agent import archive, store
from aula_agent.ledger import Ledger, SyncedEntry
from test_web import auth, classify, configure, make_client, post

TODAY = dt.date(2026, 11, 6)  # a Friday in week 45 (Monday 2 November)


def overview(**params):
    configure()
    with make_client(today=lambda: TODAY) as c:
        r = c.get("/overblik", params=params, headers=auth())
    assert r.status_code == 200
    return r.text


def section(html, name):
    """One part of the timeline (``<section id="...">``)."""
    return html.split(f'<section id="{name}"', 1)[1].split("</section>", 1)[0]


def titles(html):
    """The items' titles as linked, in order."""
    return re.findall(r'<a class="item-link" href="/overblik/item/[^"]+">(.*?)</a>', html)


def message(thread, mid, sent, subject, *, sensitive=False):
    return dict(
        thread_id=thread,
        messages=[{"id": mid, "messageType": "Message", "sendDateTime": sent, "text": {"html": "<p>Tekst</p>"}}],
        thread={"id": thread, "subject": subject, "sensitive": sensitive},
    )


def save_message(a, spec, **kw):
    a.save_thread(spec["thread_id"], spec["messages"], thread=spec["thread"], **kw)


def test_a_period_covering_today_is_current_and_replies_are_shown_nowhere():
    with archive.open() as a:
        a.save_post(post(1, "Svømning i november", publish="2026-10-20T08:00:00"))
        a.save_post(post(2, "Emneuge i december", publish="2026-10-20T08:00:00"))
        save_message(a, message("7", "m1", "2026-11-05T09:00:00", "Svar om svømning"))
        a.create_topic("svømning", "Svømmeundervisning")
        classify(a, "post:1", "periode", topics=["svømning"], summary="Onsdage, husk badetøj.",
                 start="2026-11-02", end="2026-11-30")
        classify(a, "post:2", "periode", topics=["svømning"], start="2026-12-07", end="2026-12-11")
        classify(a, "message:7:m1", "svar")

    html = overview()

    now = section(html, "gaelder-nu")
    assert titles(now) == ["Svømning i november"]
    assert "Onsdage, husk badetøj." in now and "Periode" in now and "opslag" in now
    assert "Svar om svømning" not in html


def weeks(html):
    """The weeks in a part of the timeline, by their Monday, in order: Monday -> what is in the week."""
    parts = html.split(' data-week="')[1:]
    return {p[:10]: p for p in parts}


def synced(key, title, start, *, source, child_ids=(1,), end=None):
    ledger = Ledger.load()
    ledger.record(SyncedEntry(key, list(child_ids), f"cal{child_ids[0]}", f"ev-{key}", title, start, end or start, source))
    ledger.save()


def test_upcoming_is_week_by_week_with_a_deadline_in_its_own_week_and_events_linked_to_their_item():
    with archive.open() as a:
        a.save_post(post(3, "Juleklip", publish="2026-10-28T08:00:00"))
        a.save_post(post(4, "Trivselsundersøgelse", publish="2026-10-29T08:00:00"))
        a.create_topic("jul", "Julearrangementer")
        classify(a, "post:3", "arrangement", topics=["jul"], summary="Fre 4/12, tilmelding senest 20/11.", start="2026-12-04")
        classify(a, "post:4", "frist", topics=["jul"], start="2026-11-15")
    synced("post:3#1", "Juleklip", "2026-12-04T10:00", end="2026-12-04T12:00", source="post:3")
    synced("post:3#2", "Frist: tilmelding juleklip", "2026-11-20", source="post:3")

    upcoming = weeks(section(overview(), "kommende"))

    assert list(upcoming) == ["2026-11-09", "2026-11-16", "2026-11-30"]
    assert titles(upcoming["2026-11-09"]) == ["Trivselsundersøgelse"]
    deadline = upcoming["2026-11-16"]
    assert "Frist: tilmelding juleklip" in deadline and 'href="/overblik/item/post:3"' in deadline
    assert "Uge 47" in deadline
    december = upcoming["2026-11-30"]
    assert titles(december) == ["Juleklip"] and december.count("Juleklip") == 1  # the event on the span's day: once


def topics_in(html):
    """The topic headings in Stående info, in order, each with the items' titles under it."""
    parts = html.split('<div class="topic"')[1:]
    return {re.search(r"<h3>(.*?)</h3>", p)[1]: titles(p) for p in parts}


def test_standing_info_is_grouped_by_topic_and_not_in_the_weeks():
    with archive.open() as a:
        a.save_post(post(1, "Skema for 2.B", publish="2026-08-10T08:00:00"))
        a.save_post(post(2, "Mødetider", publish="2026-11-03T08:00:00"))
        a.save_post(post(3, "Regler for fødselsdage", publish="2026-09-05T08:00:00"))
        a.create_topic("skoleåret", "Skema, mødetider og regler for året")
        a.create_topic("fødselsdage", "Invitationer og gaver")
        classify(a, "post:1", "info", topics=["skoleåret"], summary="Idræt tirsdag og torsdag.")
        classify(a, "post:2", "info", topics=["skoleåret", "fødselsdage"])
        classify(a, "post:3", "info", topics=["fødselsdage"])

    html = overview()

    assert topics_in(section(html, "staaende-info")) == {
        "fødselsdage": ["Regler for fødselsdage"],
        "skoleåret": ["Mødetider", "Skema for 2.B"],  # newest first
    }
    assert "Idræt tirsdag og torsdag." in section(html, "staaende-info")
    assert titles(section(html, "kommende")) == []


def test_earlier_weeks_are_newest_first_for_eight_weeks_and_then_vis_aeldre():
    with archive.open() as a:
        a.save_post(post(1, "Motionsdag", publish="2026-10-01T08:00:00"))
        a.save_post(post(2, "Lus i klassen", publish="2026-10-27T08:00:00"))
        a.save_post(post(3, "Skolestart", publish="2026-08-01T08:00:00"))
        a.save_post(post(4, "Forældremøde", publish="2026-09-01T08:00:00"))
        a.create_topic("skole", "Skolens hverdag")
        classify(a, "post:1", "arrangement", topics=["skole"], start="2026-10-09")
        classify(a, "post:2", "orientering", topics=["skole"])  # past, by its date
        classify(a, "post:3", "arrangement", topics=["skole"], start="2026-08-12")
        classify(a, "post:4", "arrangement", topics=["skole"], start="2026-09-10")

    earlier = section(overview(), "tidligere")

    assert list(weeks(earlier)) == ["2026-10-26", "2026-10-05", "2026-09-07"]
    assert titles(earlier) == ["Lus i klassen", "Motionsdag", "Forældremøde"]
    assert 'class="row past"' not in earlier  # shown as usual, not dimmed
    assert 'href="/overblik?before=2026-09-07"' in earlier

    older = section(overview(before="2026-09-07"), "tidligere")
    assert list(weeks(older)) == ["2026-08-10"] and titles(older) == ["Skolestart"]
    assert "?before=" not in older  # nothing older


def important(pid, title, start, end, publish="2026-10-20T08:00:00"):
    return {**post(pid, title, publish=publish), "isImportant": True, "importantFrom": start, "importantTo": end}


def test_current_has_aulas_important_posts_within_their_dates_and_orienteringer_not_past():
    with archive.open() as a:
        a.save_post(important(1, "Ny ordning for afhentning", "2026-11-01T00:00:00", "2026-11-20T00:00:00"))
        a.save_post(important(2, "Vigtigt om efterårsferien", "2026-10-01T00:00:00", "2026-10-20T00:00:00"))
        a.save_post(important(3, "Vigtigt om juletræet", "2026-12-01T00:00:00", "2026-12-20T00:00:00"))
        a.save_post(post(4, "Lus i klassen", publish="2026-11-02T08:00:00"))
        a.save_post(post(5, "Referat fra skolebestyrelsen", publish="2026-10-01T08:00:00"))
        a.create_topic("skole", "Skolens hverdag")
        for key in ("post:1", "post:2", "post:3"):
            classify(a, key, "info", topics=["skole"])
        classify(a, "post:4", "orientering", topics=["skole"])
        classify(a, "post:5", "orientering", topics=["skole"])  # more than 14 days ago: past

    now = section(overview(), "gaelder-nu")

    assert titles(now) == ["Lus i klassen", "Ny ordning for afhentning"]
    assert "vigtig i Aula" in now


def two_children():
    cfg = store.load_config()
    cfg["children"] = {"1": {"name": "Emma Test"}, "2": {"name": "Oscar Test"}}
    store.save_config(cfg)


def test_the_child_filter_shows_the_childs_items_and_those_without_a_child_and_only_with_two_children():
    with archive.open() as a:
        a.save_post(post(1, "Tur for Emma", publish="2026-11-02T08:00:00"), child_ids=[1])
        a.save_post(post(2, "Tur for Oscar", publish="2026-11-02T08:00:00"), child_ids=[2])
        a.save_post(post(3, "Skolefoto for alle", publish="2026-11-02T08:00:00"))
        a.create_topic("tur", "Ture ud af huset")
        classify(a, "post:1", "arrangement", topics=["tur"], start="2026-11-10")
        classify(a, "post:2", "arrangement", topics=["tur"], start="2026-11-11")
        classify(a, "post:3", "arrangement", topics=["tur"], start="2026-11-12")
    synced("post:2#2", "Frist: seddel til Oscars tur", "2026-11-09", source="post:2", child_ids=(2,))

    assert 'id="barn-filter"' not in overview()  # one child (or none): no filter

    two_children()
    html = overview()
    chips = section(html.replace('<nav id="barn-filter"', '<section id="barn-filter"', 1), "barn-filter")
    assert 'href="/overblik?child=1"' in chips and ">Oscar</a>" in chips and ">Alle børn</a>" in chips
    assert titles(section(html, "kommende")) == ["Tur for Emma", "Tur for Oscar", "Skolefoto for alle"]

    emma = section(overview(child="1"), "kommende")
    assert titles(emma) == ["Tur for Emma", "Skolefoto for alle"] and "Frist: seddel" not in emma
    oscar = section(overview(child="2"), "kommende")
    assert titles(oscar) == ["Tur for Oscar", "Skolefoto for alle"] and "Frist: seddel" in oscar


def test_a_sensitive_thread_shows_only_its_date_source_and_category_and_the_unclassified_are_counted_at_the_bottom():
    with archive.open() as a:
        spec = message("9", "m1", "2026-11-03T08:00:00+01:00", "Samtale om skoledagen", sensitive=True)
        spec["messages"][0]["sender"] = {"fullName": "Lærer Hansen"}
        save_message(a, spec)
        save_message(a, message("8", "m1", "2026-08-03T08:00:00+01:00", "Aftale om hentning", sensitive=True))
        a.save_post(post(1, "Endnu ikke læst"))
        a.save_post(post(2, "Heller ikke læst"))
        a.create_topic("trivselssamtale", "Trivsel og samtaler")
        classify(a, "message:9:m1", "orientering", topics=["trivselssamtale"], summary="Et resumé der ikke må ses")
        classify(a, "message:8:m1", "info", topics=["trivselssamtale"])

    html = overview()

    now = section(html, "gaelder-nu")
    assert "🔒 Følsom tråd" in now and "besked" in now and "03.11.2026" in now and "Orientering" in now
    standing = topics_in(section(html, "staaende-info"))
    assert standing == {"Uden emne": ["🔒 Følsom tråd"]}
    for hidden in ("Samtale om skoledagen", "Aftale om hentning", "Et resumé der ikke må ses", "trivselssamtale",
                   "Lærer Hansen"):
        assert hidden not in html.split("</form>", 1)[1]  # the search field's list of topics has them all
    assert "Endnu ikke læst" not in html
    assert html.rindex("2 endnu ikke inddelt") > html.index('<section id="tidligere"')


def test_an_open_todo_with_due_stands_in_its_week_under_upcoming_too():
    with archive.open(at=dt.datetime(2026, 11, 2, 9, tzinfo=dt.UTC)) as a:
        a.save_post(post(1, "Skolefoto", publish="2026-11-02T08:00:00"))
        a.save_post(post(2, "Svømning", publish="2026-11-02T08:00:00"))
        a.create_topic("skole", "Skolens hverdag")
        for key, todo in (("post:1", archive.Todo("Bestil billeder", dt.date(2026, 11, 18))),
                          ("post:2", archive.Todo("Køb badehætte"))):
            a.classify(key, archive.Classification(archive.Category.INFO, topics=("skole",), todo=todo))

    html = overview()

    upcoming = weeks(section(html, "kommende"))
    assert list(upcoming) == ["2026-11-16"]
    assert "Bestil billeder" in upcoming["2026-11-16"] and "Køb badehætte" not in section(html, "kommende")
    assert "Bestil billeder" in section(html, "skal-goeres") and "Køb badehætte" in section(html, "skal-goeres")


def test_upcoming_is_from_today_earlier_is_until_yesterday_and_nothing_current_or_without_a_span_is_upcoming():
    with archive.open() as a:
        a.save_post(post(1, "Teater onsdag", publish="2026-10-28T08:00:00"))
        a.save_post(post(2, "Bagedag i dag", publish="2026-10-28T08:00:00"))
        a.save_post(post(3, "Svømning i november", publish="2026-10-20T08:00:00"))
        a.save_post(post(4, "Lus i klassen", publish="2026-11-05T08:00:00"))
        a.save_post(post(5, "Ny ordning i garderoben", publish="2026-11-06T07:00:00"))
        a.save_post(post(6, "Husk madpakke", publish="2026-11-06T07:30:00"))
        a.create_topic("skole", "Skolens hverdag")
        classify(a, "post:1", "arrangement", topics=["skole"], start="2026-11-04")
        classify(a, "post:2", "arrangement", topics=["skole"], start="2026-11-06")
        classify(a, "post:3", "periode", topics=["skole"], start="2026-11-02", end="2026-11-30")
        classify(a, "post:4", "orientering", topics=["skole"])
        classify(a, "post:5", "orientering", topics=["skole"])
        classify(a, "post:6", "arrangement", topics=["skole"])  # no span, came today: neither upcoming nor lost

    html = overview()

    assert titles(section(html, "kommende")) == ["Bagedag i dag"]
    assert titles(section(html, "gaelder-nu")) == ["Ny ordning i garderoben", "Lus i klassen", "Svømning i november"]
    earlier = section(html, "tidligere")
    assert list(weeks(earlier)) == ["2026-11-02"]  # this week until yesterday
    assert titles(earlier) == ["Husk madpakke", "Lus i klassen", "Teater onsdag", "Svømning i november"]


def test_an_arrangement_over_several_days_under_way_today_is_current_with_its_event_shown_once():
    with archive.open() as a:
        a.save_post(post(1, "Lejrskole", publish="2026-10-20T08:00:00"))
        a.save_post(post(2, "Bagedag", publish="2026-10-20T08:00:00"))
        a.create_topic("lejrskole", "Lejrskole og ture med overnatning")
        classify(a, "post:1", "arrangement", topics=["lejrskole"], start="2026-11-05", end="2026-11-07")
        classify(a, "post:2", "arrangement", topics=["lejrskole"], start="2026-11-05")  # one day, yesterday
    synced("post:1#1", "Lejrskole", "2026-11-05", end="2026-11-07", source="post:1")

    html = overview()

    now = section(html, "gaelder-nu")
    assert titles(now) == ["Lejrskole"] and "gælder 05.11.2026–07.11.2026" in now
    assert "Lejrskole" not in section(html, "kommende")
