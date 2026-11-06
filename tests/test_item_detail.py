"""The overview's detail page (``/item/<item-key>``) and its file route, built through fetch and apply."""

import datetime as dt
import json

from click.testing import CliRunner

from aula_agent import cli, store
from test_aula_source import FakeAula, aula, child, file_att, iso, message, post, run_fetch  # noqa: F401  (aula is a fixture)
from test_web import auth, configure, make_client

TODAY = dt.date(2026, 11, 6)
TOPICS = {"tur": "Ture ud af huset", "svømning": "Svømmeundervisning"}


def apply(doc):
    path = store.DATA_DIR / "events-test.json"
    path.write_text(json.dumps(doc))
    r = CliRunner().invoke(cli.main, ["apply", str(path)])
    assert r.exit_code == 0, r.output


def classify(item, category="arrangement", **c):
    return {"item": item, "category": category, "topics": ["tur"], "summary": "Tur i skoven, husk madpakke.", **c}


def get(path, today=TODAY):
    configure()
    with make_client(today=lambda: today) as c:
        return c.get(path, headers=auth())


def test_the_detail_shows_the_items_meta_classification_summary_and_whole_text(aula):
    p = post(1, "Skovtur", 1, html="<p>Vi går i skoven fredag. Husk <b>madpakke</b>.</p>")
    aula["client"] = FakeAula([child(1, "Emma Jensen")], [p], [], {})
    run_fetch()
    apply({"processed": ["post:1"], "events": [], "new_topics": TOPICS,
           "classifications": [classify("post:1", span={"start": "2099-12-04"}, topics=["tur", "svømning"])]})

    r = get("/item/post:1")
    assert r.status_code == 200
    html = r.text
    assert "Skovtur" in html and "Vi går i skoven fredag." in html and "madpakke" in html
    assert "Tur i skoven, husk madpakke." in html
    assert "opslag" in html and "Lærer Hansen" in html and "Emma" in html
    assert "Arrangement" in html and "04.12.2099" in html and "tur, svømning" in html


def test_an_unknown_item_is_not_found(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 1)], [], {})
    run_fetch()
    assert get("/item/post:2").status_code == 404


def test_an_item_edited_in_aula_shows_how_many_versions_and_the_earlier_ones_with_date_and_text(aula):
    client = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 3, html="<p>Vi går i skoven fredag.</p>")], [], {})
    aula["client"] = client
    run_fetch()
    client.posts[0] = post(1, "Skovtur", 3, html="<p>Vi går i skoven torsdag.</p>", edited=iso(1))
    run_fetch()

    html = get("/item/post:1").text
    assert "Rettet i Aula · 2 versioner" in html
    current, earlier = html.split("Rettet i Aula")
    assert "torsdag" in current and "fredag" not in current
    assert "<details" in earlier and "Vi går i skoven fredag." in earlier


def test_an_item_never_edited_says_nothing_about_versions(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 1)], [], {})
    run_fetch()
    assert "Rettet i Aula" not in get("/item/post:1").text


def image_att(aid, name):
    return {"id": aid, "media": {"mediaType": "image", "file": {"id": aid, "name": name, "url": f"https://files/i{aid}"}}}


SECURE = {"id": 90, "document": {"id": 9, "title": "Samtykke", "documentType": "Generic"}}


def fetch_post_with_files(aula):
    atts = [file_att(5, "program.pdf"), file_att(6, "seddel.docx"), image_att(7, "klassen.jpg"), SECURE,
            file_att(8, "kort.pdf", url="https://files/broken")]
    client = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 1, attachments=atts)], [], {})
    client.broken_urls.add("https://files/broken")
    aula["client"] = client
    run_fetch()


def test_the_files_are_listed_images_as_thumbnails_secure_documents_and_files_not_downloaded_as_such(aula):
    fetch_post_with_files(aula)
    html = get("/item/post:1").text
    files = html[html.index("<h3>Filer"):]
    assert '<a href="/item/post:1/file/5" target="_blank" rel="noopener">program.pdf</a>' in files
    assert '<a href="/item/post:1/file/6" download>seddel.docx</a>' in files
    assert '<img src="/item/post:1/file/7"' in files and 'alt="klassen.jpg"' in files
    secure = next(li for li in files.split("<li") if "Samtykke" in li)
    assert "kan kun ses i Aula" in secure and "<a " not in secure
    broken = next(li for li in files.split("<li") if "kort.pdf" in li)
    assert "ikke hentet" in broken and "<a " not in broken


def test_the_file_route_hands_out_a_registered_file_pdfs_and_images_inline_others_as_download(aula):
    fetch_post_with_files(aula)
    configure()
    with make_client() as c:
        pdf = c.get("/item/post:1/file/5", headers=auth())
        docx = c.get("/item/post:1/file/6", headers=auth())
        jpg = c.get("/item/post:1/file/7", headers=auth())
        assert c.get("/item/post:1/file/5").status_code == 403  # behind Access like the rest
    assert pdf.status_code == 200 and pdf.content == b"%PDF-1.4"
    assert pdf.headers["content-type"] == "application/pdf" and pdf.headers["content-disposition"].startswith("inline")
    assert docx.status_code == 200 and docx.headers["content-disposition"].startswith("attachment")
    assert jpg.status_code == 200 and jpg.headers["content-type"] == "image/jpeg"
    assert jpg.headers["content-disposition"].startswith("inline")


def test_only_pdfs_and_plain_image_types_are_shown_inline_anything_else_registered_as_an_image_is_downloaded(aula):
    atts = [image_att(21, "tegning.svg"), image_att(22, "side.html"), image_att(23, "klassen.png"),
            file_att(24, "program.pdf")]
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 1, attachments=atts)], [], {})
    run_fetch()
    configure()
    with make_client() as c:
        svg, page_, png, pdf = (c.get(f"/item/post:1/file/{i}", headers=auth()) for i in (21, 22, 23, 24))
        detail = c.get("/item/post:1", headers=auth()).text
    for r in (svg, page_):  # would run script on the site's own address
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/octet-stream"
        assert r.headers["content-disposition"].startswith("attachment")
    assert png.headers["content-type"] == "image/png" and png.headers["content-disposition"].startswith("inline")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.headers["content-disposition"].startswith("inline")
    for r in (svg, page_, png, pdf):
        assert r.headers["x-content-type-options"] == "nosniff"
    for r in (svg, page_, png):
        assert r.headers["content-security-policy"] == "sandbox"
    assert "content-security-policy" not in pdf.headers  # Chrome's PDF viewer won't open under a sandbox
    files = detail[detail.index("<h3>Filer"):]
    assert '<img src="/item/post:1/file/23"' in files
    assert '<img src="/item/post:1/file/21"' not in files and '<img src="/item/post:1/file/22"' not in files
    assert '<a href="/item/post:1/file/21" download>tegning.svg</a>' in files


def test_the_file_route_gives_404_for_an_unknown_id_another_items_file_a_path_and_what_it_has_no_file_for(aula):
    fetch_post_with_files(aula)
    aula["client"].posts.append(post(2, "Svømning", 1, attachments=[file_att(11, "badetøj.pdf")]))
    run_fetch()
    configure()
    with make_client() as c:
        assert c.get("/item/post:2/file/11", headers=auth()).status_code == 200
        for path in (
            "/item/post:1/file/99",  # unknown id
            "/item/post:1/file/11",  # post:2's file
            "/item/post:3/file/5",  # unknown item
            "/item/post:1/file/90",  # the secure document: only in Aula
            "/item/post:1/file/8",  # not downloaded
            "/item/post:1/file/attachments/post_1/5-program.pdf",
            "/item/post:1/file/..%2F..%2Fconfig.json",
            "/item/post:1/file/%2E%2E/%2E%2E/config.json",
        ):
            assert c.get(path, headers=auth()).status_code == 404, path


def calendar(html):
    """The part of the detail under "Blev til i kalenderen", up to the next heading."""
    part = html[html.index("<h3>Blev til i kalenderen"):]
    return part[:part.index("<h3>", 4)] if "<h3>" in part[4:] else part


def trip_event(source, start="2099-12-04", **kw):
    return {"key": "post:1#1", "source": source, "child_ids": [1, 2], "title": "Skovtur", "start": start, "end": start, **kw}


def test_an_event_created_by_one_item_and_corrected_by_another_shows_its_history_on_both(aula):
    aula["client"] = client = FakeAula([child(1, "Emma"), child(2, "Oscar")], [post(1, "Skovtur", 2)], [], {})
    run_fetch()
    apply({"processed": ["post:1"], "events": [trip_event("post:1")], "new_topics": TOPICS,
           "classifications": [classify("post:1")]})
    client.threads, client.messages = [{"id": 7, "subject": "Ny dato for turen", "lastMessageDate": iso(1)}], {"7": [message("a")]}
    run_fetch()
    apply({"processed": ["message:7:a"], "events": [trip_event("message:7:a", start="2099-12-05")],
           "classifications": [classify("message:7:a")]})
    from aula_agent import gcal
    gcal.remove_event("post:1#1", 2, google=aula["google"])

    first = calendar(get("/item/post:1").text)
    assert "05.12.2099" in first and "Skovtur" in first and "Emma" in first
    assert "oprettet af dette opslag" in first
    assert 'rettet af <a href="/item/message:7:a">Ny dato for turen</a>' in first
    assert "fjernet af dig for Oscar" in first

    second = calendar(get("/item/message:7:a").text)
    assert 'oprettet af <a href="/item/post:1">Skovtur</a>' in second
    assert "rettet af denne besked" in second


def test_a_cancelled_event_stays_as_cancelled(aula):
    aula["client"] = client = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 2)], [], {})
    run_fetch()
    apply({"processed": ["post:1"], "events": [{**trip_event("post:1"), "child_ids": [1]}], "new_topics": TOPICS,
           "classifications": [classify("post:1")]})
    client.threads, client.messages = [{"id": 7, "subject": "Turen er aflyst", "lastMessageDate": iso(1)}], {"7": [message("a")]}
    run_fetch()
    apply({"processed": ["message:7:a"], "events": [{"key": "post:1#1", "cancelled": True, "source": "message:7:a"}],
           "classifications": [classify("message:7:a", "orientering", summary="Turen er aflyst.")]})

    first = calendar(get("/item/post:1").text)
    assert "aflyst" in first and "Skovtur" in first and "04.12.2099" in first
    assert 'aflyst af <a href="/item/message:7:a">Turen er aflyst</a>' in first


def test_an_item_that_became_nothing_in_the_calendar_says_so(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 1)], [], {})
    run_fetch()
    assert "Intet i kalenderen." in calendar(get("/item/post:1").text)


def test_a_sensitive_thread_shows_date_source_and_category_but_the_rest_only_after_vis_teksten(aula):
    thread = {"id": 5, "subject": "Samtale om skoledagen", "lastMessageDate": iso(1), "sensitive": True}
    msg = message("a", text={"html": "<p>Vi taler om trivsel.</p>"}, sender={"fullName": "Lærer Hansen"},
                  attachments=[file_att(6, "referat.pdf")])
    aula["client"] = FakeAula([child(1, "Emma")], [], [thread], {"5": [msg]})
    run_fetch()
    apply({"processed": ["message:5:a"], "new_topics": {"trivselssamtale": "Samtaler om trivsel"},
           "events": [{"key": "message:5:a#1", "source": "message:5:a", "child_ids": [1], "title": "Trivselssamtale",
                       "start": "2099-12-01T08:00"}],
           "classifications": [{"item": "message:5:a", "category": "arrangement", "topics": ["trivselssamtale"],
                                "span": {"start": "2099-12-01"},
                                "todo": {"text": "Ring til skolen om samtalen", "due": "2099-11-20"}}]})

    hidden = get("/item/message:5:a").text
    assert "🔒 Følsom tråd" in hidden and "besked" in hidden and "Arrangement" in hidden
    assert '<a href="?show=1"' in hidden and "Vis teksten" in hidden
    assert "senest 20.11.2099" in hidden and "Klaret" in hidden  # the todo's status and button, not its text
    secret = ("Samtale om skoledagen", "Vi taler om trivsel", "trivselssamtale", "Ring til skolen", "referat.pdf",
              "Lærer Hansen", "Trivselssamtale")
    for text in secret:
        assert text not in hidden, text

    shown = get("/item/message:5:a?show=1").text
    for text in secret:
        assert text in shown, text
    assert "Vis teksten" not in shown


def press_on_detail(html):
    """Press the todo's button on the detail page; returns where it leads back to."""
    import re

    form = html[html.index('<form class="inline" method="post" action="/todos/'):]
    form = form[:form.index("</form>")]
    data = dict(re.findall(r'name="(\w+)" value="([^"]*)"', form))
    configure()
    with make_client(today=lambda: TODAY) as c:
        r = c.post(re.search(r'action="([^"]+)"', form)[1], data=data,
                   headers={**auth(), "Origin": "https://aula.example.dk"}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]


def test_the_todo_shows_its_status_and_klaret_and_fortryd_lead_back_to_the_detail(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 1)], [], {})
    run_fetch()
    apply({"processed": ["post:1"], "events": [], "new_topics": TOPICS,
           "classifications": [classify("post:1", todo={"text": "Betal 50 kr. til turen", "due": "2099-11-20"})]})

    html = get("/item/post:1").text
    assert '<span class="todo">Betal 50 kr. til turen</span>' in html and "åben" in html and "senest 20.11.2099" in html
    assert "overskredet" in get("/item/post:1", today=dt.date(2099, 12, 1)).text

    assert press_on_detail(html) == "/item/post:1"
    html = get("/item/post:1").text
    assert "klaret" in html and "Fortryd" in html and "åben" not in html
    assert press_on_detail(html) == "/item/post:1"
    assert "åben" in get("/item/post:1").text


def test_klaret_only_leads_back_to_a_detail_page(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 1)], [], {})
    run_fetch()
    apply({"processed": ["post:1"], "events": [], "new_topics": TOPICS,
           "classifications": [classify("post:1", todo={"text": "Betal turen"})]})
    html = get("/item/post:1").text
    html = html.replace('name="back" value="/item/post:1"', 'name="back" value="https://evil.example/"')
    assert press_on_detail(html) == "/"


def test_a_message_shows_the_whole_thread_with_the_chosen_message_marked_replies_too(aula):
    thread = {"id": 7, "subject": "Tur i skoven", "lastMessageDate": iso(1)}
    msgs = [message("a", 3, text={"html": "<p>Vi går i skoven fredag.</p>"}, sender={"fullName": "Lærer Hansen"}),
            message("b", 2, text={"html": "<p>Tak</p>"}, sender={"fullName": "Forælder Et"}),
            message("c", 1, text={"html": "<p>Husk regntøj.</p>"}, sender={"fullName": "Lærer Hansen"})]
    aula["client"] = FakeAula([child(1, "Emma")], [], [thread], {"7": msgs})
    run_fetch()
    apply({"processed": ["message:7:a", "message:7:b", "message:7:c"], "events": [], "new_topics": TOPICS,
           "classifications": [classify("message:7:a"), {"item": "message:7:b", "category": "svar"},
                               classify("message:7:c", "orientering")]})

    html = get("/item/message:7:b").text
    part = html[html.index("<h3>Tråden"):]
    assert part.index("Vi går i skoven fredag.") < part.index("Tak") < part.index("Husk regntøj.")
    chosen = next(li for li in part.split("<li") if "Tak" in li)
    assert 'class="sel"' in chosen and "Forælder Et" in chosen
    others = [li for li in part.split("<li") if "skoven fredag" in li or "regntøj" in li]
    assert len(others) == 2 and all('class="sel"' not in li for li in others)
    assert 'href="/item/message:7:a"' in part and 'href="/item/message:7:c"' in part


def test_a_post_has_no_thread(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 1)], [], {})
    run_fetch()
    assert "Tråden" not in get("/item/post:1").text


def test_upcoming_events_on_the_status_page_and_the_overview_link_to_the_item(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(1, "Skovtur", 1)], [], {})
    run_fetch()
    apply({"processed": ["post:1"], "events": [{**trip_event("post:1"), "child_ids": [1]}], "new_topics": TOPICS,
           "classifications": [classify("post:1")]})

    status = get("/settings").text
    upcoming = status[status.index("<h2>Kommende begivenheder"):status.index("<h2>Børn")]
    assert 'href="/item/post:1"' in upcoming
    assert '<a class="item-link" href="/item/post:1">Skovtur</a>' in get("/").text


def test_an_upcoming_event_from_a_sensitive_thread_shows_its_source_only_as_sensitive_on_the_status_page(aula):
    thread = {"id": 5, "subject": "Samtale om skoledagen", "lastMessageDate": iso(1), "sensitive": True}
    aula["client"] = FakeAula([child(1, "Emma")], [], [thread], {"5": [message("a")]})
    run_fetch()
    apply({"processed": ["message:5:a"], "events": [{**trip_event("message:5:a"), "child_ids": [1]}],
           "new_topics": TOPICS, "classifications": [{"item": "message:5:a", "category": "arrangement", "topics": ["tur"]}]})

    def upcoming():
        status = get("/settings").text
        return status[status.index("<h2>Kommende begivenheder"):status.index("<h2>Børn")]

    shown = upcoming()
    assert '<a href="/item/message:5:a">🔒 Følsom tråd</a>' in shown
    assert "Samtale om skoledagen" not in shown

    store.path("ARCHIVE_FILE").unlink()  # no archive to look it up in: as before
    assert '<a href="/item/message:5:a">Samtale om skoledagen</a>' in upcoming()


# ------------------------------------------------------------------ "Fjern for <barn>" under "Blev til i kalenderen"


def remove_forms(html):
    """The forms under "Blev til i kalenderen" that remove an event: (the button's text, the form's fields)."""
    import re

    found = []
    for form in re.findall(r'<form\b[^>]*action="/events/remove"[^>]*>.*?</form>', calendar(html), re.S):
        button = re.search(r"<button[^>]*>(.*?)</button>", form, re.S)[1]
        found.append((button, dict(re.findall(r'name="(\w+)" value="([^"]*)"', form))))
    return found


def remove_without_script(html, button):
    """Press ``button`` on the detail as a browser without script does: its confirmation, then "Fjern" there.
    Returns the confirmation page and where the removal led back to."""
    import re

    fields = dict(remove_forms(html))[button]
    configure()
    with make_client(today=lambda: TODAY) as c:  # Google is the aula fixture's
        confirm = c.get("/events/remove", params=fields, headers=auth())
        assert confirm.status_code == 200
        form = re.search(r'<form\b[^>]*method="post"[^>]*action="/events/remove"[^>]*>.*?</form>', confirm.text, re.S)[0]
        data = dict(re.findall(r'name="(\w+)" value="([^"]*)"', form))
        r = c.post("/events/remove", data=data, headers={**auth(), "Origin": "https://aula.example.dk"}, follow_redirects=False)
    assert r.status_code == 303
    return confirm, r.headers["location"]



def two_children_on_a_trip(aula, start="2099-12-04"):
    aula["client"] = FakeAula([child(1, "Emma Test"), child(2, "Oscar Test")], [post(1, "Skovtur", 2)], [], {})
    run_fetch()
    apply({"processed": ["post:1"], "events": [trip_event("post:1", start=start)], "new_topics": TOPICS,
           "classifications": [classify("post:1")]})


def test_an_upcoming_event_for_two_children_has_a_button_per_child_which_after_a_confirmation_removes_it_for_that_child(aula):
    two_children_on_a_trip(aula)

    html = get("/item/post:1").text
    assert [b for b, _ in remove_forms(html)] == ["Fjern for Emma", "Fjern for Oscar"]

    confirm, back = remove_without_script(html, "Fjern for Oscar")
    assert "Skovtur" in confirm.text and "Oscar" in confirm.text and "Emma" not in confirm.text
    assert back.startswith("/item/post:1?msg=")

    html = get("/item/post:1").text
    assert "fjernet af dig for Oscar" in calendar(html)
    assert [b for b, _ in remove_forms(html)] == ["Fjern for Emma"]


def test_an_event_that_is_over_has_no_button(aula):
    two_children_on_a_trip(aula, start="2026-11-05")
    html = get("/item/post:1").text
    assert "Skovtur" in calendar(html) and remove_forms(html) == []
    assert len(remove_forms(get("/item/post:1", today=dt.date(2026, 11, 4)).text)) == 2


def test_with_one_child_the_button_just_says_fjern(aula):
    aula["client"] = FakeAula([child(1, "Emma Test")], [post(1, "Skovtur", 1)], [], {})
    run_fetch()
    apply({"processed": ["post:1"], "events": [{**trip_event("post:1"), "child_ids": [1]}], "new_topics": TOPICS,
           "classifications": [classify("post:1")]})
    html = get("/item/post:1").text
    assert [b for b, _ in remove_forms(html)] == ["Fjern"]
    confirm, back = remove_without_script(html, "Fjern")
    assert "Emma" in confirm.text and back.startswith("/item/post:1?msg=")
    assert "fjernet af dig for Emma" in calendar(get("/item/post:1").text)


def test_fjern_only_leads_back_to_a_page_on_the_site_and_without_back_to_the_settings(aula):
    two_children_on_a_trip(aula)
    configure()
    origin = {**auth(), "Origin": "https://aula.example.dk"}
    with make_client(today=lambda: TODAY) as c:
        evil = c.post("/events/remove", data={"key": "post:1#1", "child_id": "1", "back": "https://evil.example/"},
                      headers=origin, follow_redirects=False)
        none = c.post("/events/remove", data={"key": "post:1#1", "child_id": "2"}, headers=origin, follow_redirects=False)
        page = c.get("/events/remove", params={"key": "post:1#1", "child_id": "1", "back": "https://evil.example/"},
                     headers=auth())
    assert evil.headers["location"].startswith("/?msg=")
    assert none.headers["location"].startswith("/settings?msg=")
    assert "Begivenheden findes ikke længere." in page.text and "evil.example" not in page.text
