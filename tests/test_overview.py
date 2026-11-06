"""The overview's timeline (trin 2): I dag og i morgen, Gælder nu and Kommende day by day on the front page (#54),
Stående info on /info and Tidligere week by week on /past (#53)."""

import datetime as dt
import re

from aula_agent import archive, store
from aula_agent.ledger import Ledger, SyncedEntry
from test_web import auth, classify, configure, make_client, post

TODAY = dt.date(2026, 11, 6)  # a Friday in week 45 (Monday 2 November)


def overview(path="/", **params):
    configure()
    with make_client(today=lambda: TODAY) as c:
        r = c.get(path, params=params, headers=auth(), follow_redirects=False)
    assert r.status_code == 200
    return r.text


def info(**params):
    return overview("/info", **params)


def past(**params):
    return overview("/past", **params)


def section(html, name):
    """One part of the timeline (``<section id="...">``)."""
    return html.split(f'<section id="{name}"', 1)[1].split("</section>", 1)[0]


def titles(html):
    """The items' titles as linked, in order."""
    return re.findall(r'<a class="item-link" href="/item/[^"]+">(.*?)</a>', html)


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


def test_upcoming_is_day_by_day_with_a_deadline_on_its_own_day_and_events_linked_to_their_item():
    with archive.open() as a:
        a.save_post(post(3, "Juleklip", publish="2026-10-28T08:00:00"))
        a.save_post(post(4, "Trivselsundersøgelse", publish="2026-10-29T08:00:00"))
        a.create_topic("jul", "Julearrangementer")
        classify(a, "post:3", "arrangement", topics=["jul"], summary="Fre 4/12, tilmelding senest 20/11.", start="2026-12-04")
        classify(a, "post:4", "frist", topics=["jul"], start="2026-11-15")
    synced("post:3#1", "Juleklip", "2026-12-04T10:00", end="2026-12-04T12:00", source="post:3")
    synced("post:3#2", "Frist: tilmelding juleklip", "2026-11-20", source="post:3")

    upcoming = days(section(overview(), "kommende"))

    assert list(upcoming) == ["2026-11-15", "2026-11-20", "2026-12-04"]
    assert titles(upcoming["2026-11-15"]) == ["Trivselsundersøgelse"] and "Søndag 15/11" in upcoming["2026-11-15"]
    deadline = upcoming["2026-11-20"]
    assert "Fredag 20/11" in deadline
    assert "Frist: tilmelding juleklip" in deadline and 'href="/item/post:3"' in deadline and "hele dagen" in deadline
    december = upcoming["2026-12-04"]
    assert titles(december) == ["Juleklip"] and december.count("Juleklip") == 1  # the event on the span's day: once


def kommende_order(html):
    """What Kommende has, in order: ("Uge 46", None) for a week line, the date for a day."""
    return [(f"Uge {dt.date.fromisoformat(v).isocalendar().week}", None) if k == "week" else (None, v)
            for k, v in re.findall(r'data-(week|day)="([^"]+)"', html)]


def test_upcoming_begins_after_tomorrow_has_no_empty_days_and_a_week_line_when_the_week_changes():
    with archive.open() as a:
        for pid, title, start in ((1, "Teater", "2026-11-08"), (2, "Bagedag", "2026-11-09"),
                                  (3, "Museum", "2026-11-11"), (4, "Motionsdag", "2026-11-17")):
            a.save_post(post(pid, title, publish="2026-11-01T08:00:00"))
            classify(a, f"post:{pid}", "arrangement", start=start)
    synced("post:9#1", "Forældremøde", "2026-11-11T19:00", source="post:9")

    upcoming = section(overview(), "kommende")

    assert kommende_order(upcoming) == [
        (None, "2026-11-08"),  # Sunday: still week 45, as today and tomorrow
        ("Uge 46", None), (None, "2026-11-09"), (None, "2026-11-11"),
        ("Uge 47", None), (None, "2026-11-17"),
    ]
    on = days(upcoming)
    assert "Søndag 8/11" in on["2026-11-08"] and "Mandag 9/11" in on["2026-11-09"] and "Tirsdag 17/11" in on["2026-11-17"]
    assert titles(on["2026-11-11"]) == ["Museum"] and "Forældremøde" in on["2026-11-11"] and "kl. 19:00" in on["2026-11-11"]


def test_today_and_tomorrow_are_shown_with_nothing_on_them():
    with archive.open() as a:
        a.create_topic("skole", "Institutionens hverdag")  # an archive, with nothing on the days

    html = overview()

    near = days(section(html, "i-dag-og-i-morgen"))
    assert "Intet i dag." in near["2026-11-06"] and "Intet i morgen." in near["2026-11-07"]
    assert "Intet kommende." in section(html, "kommende")


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

    html = info()

    assert topics_in(section(html, "staaende-info")) == {
        "fødselsdage": ["Regler for fødselsdage"],
        "skoleåret": ["Mødetider", "Skema for 2.B"],  # newest first
    }
    assert "Idræt tirsdag og torsdag." in section(html, "staaende-info")
    assert titles(section(overview(), "kommende")) == []


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

    earlier = section(past(), "tidligere")

    assert list(weeks(earlier)) == ["2026-10-26", "2026-10-05", "2026-09-07"]
    assert titles(earlier) == ["Lus i klassen", "Motionsdag", "Forældremøde"]
    assert 'class="row past"' not in earlier  # shown as usual, not dimmed
    assert 'href="/past?before=2026-09-07"' in earlier

    older = section(past(before="2026-09-07"), "tidligere")
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
    assert 'href="/?child=1"' in chips and ">Oscar</a>" in chips and ">Alle børn</a>" in chips
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
    standing = info()
    assert topics_in(section(standing, "staaende-info")) == {"Uden emne": ["🔒 Følsom tråd"]}
    for page in (html, standing, past()):
        for hidden in ("Samtale om skoledagen", "Aftale om hentning", "Et resumé der ikke må ses", "trivselssamtale",
                       "Lærer Hansen"):
            assert hidden not in page.split("</form>", 1)[1]  # the search field's list of topics has them all
        assert "Endnu ikke læst" not in page
    assert html.rindex("2 endnu ikke inddelt") > html.index('<section id="kommende"')


def test_an_open_todo_with_due_stands_on_its_day_under_upcoming_too():
    with archive.open(at=dt.datetime(2026, 11, 2, 9, tzinfo=dt.UTC)) as a:
        a.save_post(post(1, "Skolefoto", publish="2026-11-02T08:00:00"))
        a.save_post(post(2, "Svømning", publish="2026-11-02T08:00:00"))
        a.create_topic("skole", "Skolens hverdag")
        for key, todo in (("post:1", archive.Todo("Bestil billeder", dt.date(2026, 11, 18))),
                          ("post:2", archive.Todo("Køb badehætte"))):
            a.classify(key, archive.Classification(archive.Category.INFO, topics=("skole",), todo=todo))

    html = overview()

    upcoming = days(section(html, "kommende"))
    assert list(upcoming) == ["2026-11-18"]
    assert "Bestil billeder" in upcoming["2026-11-18"] and "Køb badehætte" not in section(html, "kommende")
    assert "Bestil billeder" in section(html, "skal-goeres") and "Køb badehætte" in section(html, "skal-goeres")


def test_today_is_from_today_earlier_is_until_yesterday_and_nothing_current_or_without_a_span_is_on_the_days():
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

    assert titles(section(html, "i-dag-og-i-morgen")) == ["Bagedag i dag"] and titles(section(html, "kommende")) == []
    assert titles(section(html, "gaelder-nu")) == ["Ny ordning i garderoben", "Lus i klassen", "Svømning i november"]
    earlier = section(past(), "tidligere")
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
    assert "Lejrskole" not in section(html, "kommende") + section(html, "i-dag-og-i-morgen")


def test_aulas_important_mark_with_a_from_but_no_to_holds_for_fourteen_days_from_the_from():
    with archive.open() as a:
        a.save_post(important(1, "Vigtigt om skolevejen", "2026-10-25T00:00:00", None, publish="2026-10-01T08:00:00"))
        a.save_post(important(2, "Vigtigt om cykelhjelme", "2026-10-20T00:00:00", None, publish="2026-11-01T08:00:00"))
        a.create_topic("skole", "Skolens hverdag")
        classify(a, "post:1", "info", topics=["skole"])
        classify(a, "post:2", "info", topics=["skole"])

    assert titles(section(overview(), "gaelder-nu")) == ["Vigtigt om skolevejen"]  # from 25/10: until 8/11


def test_an_upcoming_event_links_to_its_item_whenever_the_archive_has_it():
    with archive.open() as a:
        save_message(a, message("7", "m1", "2026-11-02T09:00:00", "Svar om turen"))
        save_message(a, message("9", "m1", "2026-11-02T09:00:00", "Samtale om skoledagen", sensitive=True))
        a.save_post(post(5, "Endnu ikke inddelt", publish="2026-11-02T08:00:00"))
        a.save_post(post(6, "Tur for Oscar", publish="2026-11-02T08:00:00"), child_ids=[2])
        classify(a, "message:7:m1", "svar")
        classify(a, "message:9:m1", "orientering")
        classify(a, "post:6", "arrangement", start="2026-11-24")
    synced("message:7:m1#1", "Frist: betal turen", "2026-11-20", source="message:7:m1")
    synced("post:5#1", "Skolefoto", "2026-11-21", source="post:5")
    synced("message:9:m1#1", "Samtale", "2026-11-22", source="message:9:m1")
    synced("post:6#1", "Madpakke til turen", "2026-11-23", source="post:6", child_ids=(1,))
    synced("post:7#1", "Uden kilde i arkivet", "2026-11-25", source="post:7")
    two_children()

    upcoming = section(overview(child="1"), "kommende")

    sources = dict(re.findall(r'class="item-link event-src" href="/item/([^"]+)">(.*?)</a>', upcoming))
    assert sources == {
        "message:7:m1": "Svar om turen", "post:5": "Endnu ikke inddelt", "message:9:m1": "🔒 Følsom tråd",
        "post:6": "Tur for Oscar",
    }
    assert "Samtale om skoledagen" not in upcoming and "Uden kilde i arkivet" in upcoming


def test_a_todo_due_on_the_day_its_item_stands_in_upcoming_is_on_the_items_line_not_a_line_of_its_own():
    with archive.open(at=dt.datetime(2026, 11, 2, 9, tzinfo=dt.UTC)) as a:
        a.save_post(post(1, "Juleklip", publish="2026-11-02T08:00:00"))
        a.save_post(post(2, "Skolefoto", publish="2026-11-02T08:00:00"))
        a.create_topic("skole", "Skolens hverdag")
        for key, start, todo in (("post:1", dt.date(2026, 11, 18), archive.Todo("Medbring saks", dt.date(2026, 11, 18))),
                                 ("post:2", dt.date(2026, 11, 20), archive.Todo("Bestil billeder", dt.date(2026, 11, 17)))):
            a.classify(key, archive.Classification(archive.Category.ARRANGEMENT, start, topics=("skole",), todo=todo))

    upcoming = section(overview(), "kommende")

    lines = upcoming.split("<li")[1:]
    assert len(lines) == 3  # Bestil billeder (17/11, its own line), Juleklip (18/11), Skolefoto (20/11)
    assert "todo-row" in lines[0] and "Bestil billeder" in lines[0]
    assert 'data-key="post:1"' in lines[1] and "Medbring saks" in lines[1] and 'action="/todos/check"' in lines[1]
    assert "Medbring saks" not in lines[0] + lines[2]


def test_the_front_page_has_neither_standing_info_nor_earlier_and_info_and_past_have_only_theirs():
    with archive.open() as a:
        a.save_post(post(1, "Skema", publish="2026-08-10T08:00:00"))
        a.save_post(post(2, "Motionsdag", publish="2026-10-01T08:00:00"))
        a.save_post(post(3, "Juleklip", publish="2026-11-02T08:00:00"))
        classify(a, "post:1", "info")
        classify(a, "post:2", "arrangement", start="2026-10-09")
        classify(a, "post:3", "arrangement", start="2026-12-04")

    front, standing, earlier = overview(), info(), past()

    assert 'id="staaende-info"' not in front and 'id="tidligere"' not in front
    assert "Skema" not in front and "Motionsdag" not in front and "Juleklip" in front
    assert titles(section(standing, "staaende-info")) == ["Skema"]
    assert 'id="tidligere"' not in standing and 'id="kommende"' not in standing and 'id="skal-goeres"' not in standing
    assert titles(section(earlier, "tidligere")) == ["Motionsdag"]
    assert 'id="staaende-info"' not in earlier and 'id="kommende"' not in earlier and 'id="skal-goeres"' not in earlier


def test_vis_aeldre_keeps_the_child_and_the_child_filter_on_an_earlier_page_keeps_before():
    with archive.open() as a:
        a.save_post(post(1, "Skolestart", publish="2026-08-01T08:00:00"), child_ids=[1])
        a.save_post(post(2, "Motionsdag", publish="2026-10-01T08:00:00"), child_ids=[1])
        classify(a, "post:1", "arrangement", start="2026-08-12")
        classify(a, "post:2", "arrangement", start="2026-10-09")
    two_children()

    earlier = section(past(child="1"), "tidligere")
    assert 'href="/past?child=1&amp;before=2026-09-07"' in earlier

    html = past(child="1", before="2026-09-07")
    chips = html.split('id="barn-filter"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/past?before=2026-09-07&amp;child=2"' in chips and 'href="/past?before=2026-09-07&amp;child=all"' in chips
    assert titles(section(html, "tidligere")) == ["Skolestart"]


def days(html):
    """The days in a day list, by their date, in order: date -> what is on the day (its heading first)."""
    parts = html.split(' data-day="')[1:]
    return {p[:10]: p for p in parts}


def test_today_and_tomorrow_have_an_event_today_and_a_todo_due_tomorrow_and_upcoming_has_neither():
    with archive.open(at=dt.datetime(2026, 11, 2, 9, tzinfo=dt.UTC)) as a:
        a.save_post(post(1, "Skolefoto", publish="2026-11-02T08:00:00"))
        a.create_topic("skole", "Institutionens hverdag")
        a.classify("post:1", archive.Classification(
            archive.Category.INFO, topics=("skole",), todo=archive.Todo("Bestil billeder", dt.date(2026, 11, 7))))
    synced("post:9#1", "Morgensang", "2026-11-06T08:15", end="2026-11-06T09:00", source="post:9")
    synced("post:9#2", "Fodboldstævne", "2026-11-07", source="post:9", child_ids=(1, 2))

    html = overview()

    near = days(section(html, "i-dag-og-i-morgen"))
    assert list(near) == ["2026-11-06", "2026-11-07"]
    assert "I dag" in near["2026-11-06"] and "Morgensang" in near["2026-11-06"] and "08:15" in near["2026-11-06"]
    tomorrow = near["2026-11-07"]
    assert "I morgen" in tomorrow and "Fodboldstævne" in tomorrow and "hele dagen" in tomorrow
    assert "Bestil billeder" in tomorrow and 'action="/todos/check"' in tomorrow
    upcoming = section(html, "kommende")
    for elsewhere in ("Morgensang", "Fodboldstævne", "Bestil billeder"):
        assert elsewhere not in upcoming


def test_an_event_today_on_its_items_day_stands_once_and_a_sensitive_thread_is_hidden_on_the_days():
    with archive.open(at=dt.datetime(2026, 11, 2, 9, tzinfo=dt.UTC)) as a:
        a.save_post(post(1, "Bagedag", publish="2026-11-02T08:00:00"))
        save_message(a, message("9", "m1", "2026-11-02T08:00:00", "Samtale om dagen", sensitive=True))
        a.create_topic("skole", "Institutionens hverdag")
        classify(a, "post:1", "arrangement", topics=["skole"], start="2026-11-06")
        a.classify("message:9:m1", archive.Classification(
            archive.Category.ORIENTERING, todo=archive.Todo("Ring til personalet", dt.date(2026, 11, 7))))
    synced("post:1#1", "Bagedag", "2026-11-06T09:00", source="post:1")

    near = section(overview(), "i-dag-og-i-morgen")

    assert near.count("Bagedag") == 1 and titles(near) == ["Bagedag"]
    tomorrow = days(near)["2026-11-07"]
    assert "🔒 Følsom tråd" in tomorrow
    assert "Ring til personalet" not in tomorrow and "Samtale om dagen" not in tomorrow


def test_the_child_filter_on_info_stays_on_info():
    with archive.open() as a:
        a.save_post(post(1, "Skema for Emma", publish="2026-08-10T08:00:00"), child_ids=[1])
        a.save_post(post(2, "Skema for Oscar", publish="2026-08-10T08:00:00"), child_ids=[2])
        classify(a, "post:1", "info")
        classify(a, "post:2", "info")
    two_children()

    html = info(child="2")

    chips = html.split('id="barn-filter"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/info?child=all"' in chips and 'href="/info?child=1"' in chips
    assert titles(section(html, "staaende-info")) == ["Skema for Oscar"]


def two_childrens_trips():
    with archive.open() as a:
        a.save_post(post(1, "Tur for Emma", publish="2026-11-02T08:00:00"), child_ids=[1])
        a.save_post(post(2, "Tur for Oscar", publish="2026-11-02T08:00:00"), child_ids=[2])
        classify(a, "post:1", "arrangement", start="2026-11-10")
        classify(a, "post:2", "arrangement", start="2026-11-11")
    two_children()
    configure()


def test_a_chosen_child_is_remembered_on_the_device_for_the_next_page_without_child():
    two_childrens_trips()
    with make_client(today=lambda: TODAY) as c:
        r = c.get("/", params={"child": "2"}, headers=auth())
        cookie = r.headers["set-cookie"]
        assert cookie.startswith("child=2;")
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" in cookie
        assert "Max-Age=31536000" in cookie  # a year

        for path, name in [("/", "kommende"), ("/past", None), ("/info", None)]:
            html = c.get(path, headers=auth()).text
            assert '<a class="chip on" href="' in html and 'aria-current="true">Oscar</a>' in html
        assert titles(section(c.get("/", headers=auth()).text, "kommende")) == ["Tur for Oscar"]
        assert '<input type="hidden" name="child" value="2">' in c.get("/", headers=auth()).text  # search keeps it


def test_alle_forgets_the_child_and_the_url_wins_over_the_remembered_one():
    two_childrens_trips()
    with make_client(today=lambda: TODAY) as c:
        c.get("/", params={"child": "2"}, headers=auth())

        assert titles(section(c.get("/", params={"child": "1"}, headers=auth()).text, "kommende")) == ["Tur for Emma"]

        alle = c.get("/info", params={"child": "all"}, headers=auth())
        assert alle.headers["set-cookie"].startswith('child="";') and "Max-Age=0" in alle.headers["set-cookie"]
        assert '<a class="chip on" href="/info?child=all" aria-current="true">Alle børn</a>' in alle.text
        html = c.get("/", headers=auth()).text
        assert titles(section(html, "kommende")) == ["Tur for Emma", "Tur for Oscar"]


def test_a_remembered_child_no_longer_in_the_config_is_ignored_and_forgotten():
    two_childrens_trips()
    with make_client(today=lambda: TODAY) as c:
        c.cookies.set("child", "7")
        r = c.get("/", headers=auth())
        assert titles(section(r.text, "kommende")) == ["Tur for Emma", "Tur for Oscar"]
        assert 'chip on" href="/?child=all"' in r.text and "Max-Age=0" in r.headers["set-cookie"]

        c.cookies.clear()
        r = c.get("/", params={"child": "7"}, headers=auth())  # nor is one in the URL remembered
        assert "set-cookie" not in r.headers


def test_one_child_has_no_chips_and_the_cookie_is_not_secure_without_https():
    configure(public_url="http://127.0.0.1:8790")
    with make_client(today=lambda: TODAY) as c:
        assert 'id="barn-filter"' not in c.get("/", headers=auth()).text
        two_children()
        cookie = c.get("/past", params={"child": "1"}, headers=auth()).headers["set-cookie"]
        assert cookie.startswith("child=1;") and "Secure" not in cookie
