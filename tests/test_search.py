"""Free-text search on /search: the archive's index (with text from PDF and docx files) and the results page."""

import asyncio
import datetime as dt
import re

from aula_agent import archive
from test_web import auth, classify, configure, make_client, post

TODAY = dt.date(2026, 11, 6)


def search(**params):
    configure()
    with make_client(today=lambda: TODAY) as c:
        r = c.get("/search", params=params, headers=auth())
    assert r.status_code == 200
    return r.text


def results(html):
    """The search results part of the page."""
    return html[html.index('<h2>Søgning'):]


def test_a_word_beginning_finds_the_item_and_links_to_it():
    with archive.open() as a:
        a.save_post(post(1, "Svømning i november", body="<p>Husk badetøj</p>"))
        a.save_post(post(2, "Juleklip"))

    html = results(search(q="svøm"))

    assert "Svømning i november" in html and 'href="/item/post:1"' in html
    assert "Juleklip" not in html


def pdf(text):
    """A one-page PDF with ``text`` on it (ASCII), built by hand."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (n, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def docx(*paragraphs):
    """A Word document with ``paragraphs``, as Word saves the text (zip + XML)."""
    import io
    import zipfile

    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def file_att(aid, name):
    return {"id": aid, "name": name, "file": {"id": aid, "name": name, "url": f"https://files/{aid}"}}


def save_with_files(a, raw, files):
    """Save a post with attachments whose contents are ``files`` (name -> bytes)."""
    from aula.models.post import Post

    a.save_post(raw)
    contents = {att["name"]: data for att, data in zip(raw["attachments"], files)}

    async def download(att):
        return contents[att.name]

    asyncio.run(a.save_files(f"post:{raw['id']}", Post.from_dict(raw).attachments, download))


def with_files(pid, title, files, **kw):
    raw = post(pid, title, **kw)
    raw["attachments"] = [file_att(10 * pid + i, name) for i, name in enumerate(files)]
    return raw, list(files.values())


def test_a_word_only_in_an_attached_pdf_or_docx_finds_the_item_and_shows_the_file():
    with archive.open() as a:
        save_with_files(a, *with_files(1, "Indbydelse", {"indbydelse.pdf": pdf("Medbring madpakke og regntoej")}))
        save_with_files(a, *with_files(2, "Program", {"program.docx": docx("Vi tager til Skovbakken", "Husk gummistøvler")}))
        save_with_files(a, *with_files(3, "Billeder", {"skov.jpg": b"\xff\xd8 madpakke"}))

    html = results(search(q="madpak"))
    assert "Indbydelse" in html and "indbydelse.pdf" in html and "<mark>madpakke</mark>" in html
    assert "Billeder" not in html  # other files are found only by their name

    html = results(search(q="gummistøv"))
    assert "Program" in html and "program.docx" in html and "Indbydelse" not in html


def test_the_words_may_be_spread_over_the_item_and_its_files_and_the_file_is_shown_when_a_word_is_only_there():
    with archive.open() as a:
        save_with_files(a, *with_files(1, "Juleklip", {"indbydelse.pdf": pdf("Medbring saks og lim")}))
        save_with_files(a, *with_files(2, "Juleklip i SFO", {"plan.pdf": pdf("Vi klipper stjerner")}))
        save_with_files(a, *with_files(3, "Skovtur", {"liste.pdf": pdf("Medbring saks")}))

    html = results(search(q="juleklip saks"))
    assert hits(html) == ["Juleklip"]  # post:2 has no "saks", post:3 no "juleklip"
    assert "indbydelse.pdf" in html and "<mark>Juleklip</mark>" in html

    html = results(search(q="juleklip"))  # every word in the item itself: no file to show
    assert "indbydelse.pdf" not in html and "plan.pdf" not in html


def hits(html):
    """The titles of the hits, in order."""
    import re

    return re.findall(r'<a href="/item/[^"]+">(.*?)</a>', results(html))


def message(thread, mid, sent, text, subject, *, sensitive=False):
    return dict(
        thread_id=thread,
        messages=[{"id": mid, "messageType": "Message", "sendDateTime": sent, "text": {"html": f"<p>{text}</p>"}}],
        thread={"id": thread, "subject": subject, "sensitive": sensitive},
    )


def save_message(a, spec, **kw):
    a.save_thread(spec["thread_id"], spec["messages"], thread=spec["thread"], **kw)


def test_the_filters_narrow_the_search_and_the_newest_comes_first():
    configure_children()
    with archive.open() as a:
        a.save_post(post(1, "Tur til skoven", publish="2026-10-01T08:00:00", body="<p>Vi skal i skoven</p>"), child_ids=[1])
        a.save_post(post(2, "Skoven og naturen", publish="2026-11-02T08:00:00"), child_ids=[2])
        save_with_files(a, *with_files(3, "Plan for skoven", {"plan.pdf": pdf("Plan")}, publish="2026-10-20T08:00:00"))
        save_message(a, message("7", "m1", "2026-11-03T09:00:00", "Tak for turen i skoven", "Skovtur"))
        a.create_topic("tur", "Ture ud af huset")
        a.create_topic("natur", "Natur og udeliv")
        classify(a, "post:1", "arrangement", topics=["tur"], start="2026-10-05")
        classify(a, "post:2", "info", topics=["natur"])
        classify(a, "post:3", "arrangement", topics=["tur"], start="2026-10-25")
        classify(a, "message:7:m1", "svar")

    assert hits(search(q="skov")) == ["Skovtur", "Skoven og naturen", "Plan for skoven", "Tur til skoven"]  # svar too
    assert hits(search(q="skov", category="arrangement")) == ["Plan for skoven", "Tur til skoven"]
    assert hits(search(q="skov", topic="natur")) == ["Skoven og naturen"]
    assert hits(search(q="skov", kind="besked")) == ["Skovtur"]
    assert hits(search(q="skov", kind="opslag")) == ["Skoven og naturen", "Plan for skoven", "Tur til skoven"]
    assert hits(search(q="skov", kind="dokument")) == ["Plan for skoven"]
    # an item for no child in particular is for every child
    assert hits(search(q="skov", child="1")) == ["Skovtur", "Plan for skoven", "Tur til skoven"]
    assert hits(search(category="info")) == ["Skoven og naturen"]  # a filter alone searches too


def test_past_items_are_dimmed_and_marked_but_found():
    with archive.open() as a:
        a.save_post(post(1, "Skovtur", publish="2026-10-01T08:00:00"))
        a.save_post(post(2, "Skovdag", publish="2026-10-01T08:00:00"))
        a.save_post(post(3, "Skovregler", publish="2026-01-01T08:00:00"))
        a.save_post(post(4, "Skovlus", publish="2026-10-01T08:00:00"))
        a.save_post(post(5, "Skovnyt", publish="2026-11-01T08:00:00"))
        a.create_topic("tur", "Ture ud af huset")
        classify(a, "post:1", "arrangement", topics=["tur"], start="2026-10-05")  # over
        classify(a, "post:2", "periode", topics=["tur"], start="2026-10-01", end="2026-11-30")  # still on
        classify(a, "post:3", "info", topics=["tur"])  # never past
        classify(a, "post:4", "orientering", topics=["tur"])  # more than 14 days ago
        classify(a, "post:5", "orientering", topics=["tur"])  # less

    html = results(search(q="skov"))
    lis = {title: li for li in html.split("<li")[1:] for title in ["Skovtur", "Skovdag", "Skovregler", "Skovlus", "Skovnyt"]
           if f">{title}</a>" in li}
    past = {title for title, li in lis.items() if li.startswith(' class="past"') and "overstået" in li}
    assert past == {"Skovtur", "Skovlus"} and len(lis) == 5


def configure_children():
    from aula_agent import store

    cfg = store.load_config()
    cfg["children"] = {"1": {"name": "Emma Test"}, "2": {"name": "Oscar Test"}}
    store.save_config(cfg)


def test_a_sensitive_thread_is_found_without_excerpt_subject_topics_or_file_names():
    with archive.open() as a:
        spec = message("9", "m1", "2026-11-03T08:00:00", "Om trivslen i frikvarteret", "Samtale om skoledagen", sensitive=True)
        spec["messages"][0]["attachments"] = [file_att(91, "referat-trivsel.docx")]
        save_message(a, spec)
        from aula.models.message import Message

        async def download(att):
            return docx("Trivsel og venskaber")

        asyncio.run(a.save_files("message:9:m1", Message.from_dict(spec["messages"][0]).attachments, download))
        a.create_topic("trivselssamtale", "Trivsel og samtaler")
        classify(a, "message:9:m1", "orientering", topics=["trivselssamtale"], summary="Et resumé der ikke må ses")

    for q in ("trivsl", "venskab", "resumé"):
        html = results(search(q=q))
        assert hits(html) == ["🔒 Følsom tråd"], q
        assert "besked" in html and "03.11.2026" in html and "Orientering" in html
        for hidden in ("Samtale om skoledagen", "frikvarteret", "Et resumé", "trivselssamtale", "referat-trivsel",
                       "Venskaber", "<mark>"):
            assert hidden not in html, (q, hidden)


def test_the_index_follows_the_newest_classification_and_reclassify():
    def found(a, q):
        return [h.item.key for h in a.search(q)]

    with archive.open() as a:
        a.save_post(post(1, "Uge 46"))
        a.create_topic("svømning", "Svømmeundervisning")
        a.create_topic("lus", "Lus i klassen")
        classify(a, "post:1", "periode", topics=["svømning"], summary="Husk badehætte")
        assert found(a, "badehæt") == ["post:1"] and found(a, "svøm") == ["post:1"]

        classify(a, "post:1", "orientering", topics=["lus"], summary="Tjek håret")
        assert found(a, "badehæt") == [] and found(a, "svøm") == [] and found(a, "håret lus") == ["post:1"]

        a.forget_classifications()
        assert found(a, "håret") == [] and found(a, "uge") == ["post:1"]


def test_an_archive_from_before_the_index_is_migrated_by_the_next_open_and_can_be_searched():
    import sqlite3

    from aula_agent import store

    with archive.open() as a:
        save_with_files(a, *with_files(1, "Indbydelse", {"indbydelse.pdf": pdf("Tag regntoej med")}))
        a.save_post(post(2, "Uge 46"))
        a.create_topic("svømning", "Svømmeundervisning")
        classify(a, "post:2", "periode", topics=["svømning"], summary="Husk badehætte")
    db = sqlite3.connect(store.ARCHIVE_FILE)
    with db:  # as schema 6 had it
        db.executescript("DROP TABLE search_items; DROP TABLE search_files;")
        db.execute("PRAGMA user_version = 6")
    db.close()

    assert "ikke klar" in search(q="regntoej")  # the web page does not migrate it
    db = sqlite3.connect(store.ARCHIVE_FILE)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 6
    db.close()

    with archive.open():  # what fetch, apply and reclassify do first
        pass

    html = results(search(q="regntoej"))
    assert hits(html) == ["Indbydelse"] and "indbydelse.pdf" in html
    assert hits(search(q="badehæt")) == ["Uge 46"] and hits(search(q="svøm")) == ["Uge 46"]


def test_the_search_field_is_at_the_top_of_the_overview_and_the_child_filter_only_with_two_children():
    with archive.open() as a:
        a.save_post(post(1, "Juleklip"))
    html = search()
    assert html.index('name="q"') < html.index("<h2>Skal gøres") and '<h2>Søgning' not in html
    assert 'name="child"' not in html and 'id="barn-filter"' not in html
    assert 'name="category"' in html and 'name="kind"' in html

    configure_children()
    html = search(q="jul", child="2")
    assert '<input type="hidden" name="child" value="2">' in html  # a search keeps the child chosen in the filter
    assert re.search(r'<a class="chip on[^"]*" href="/search\?q=jul&amp;child=2" aria-current="true">Oscar</a>', html)
    assert 'href="/search?q=jul&amp;child=all">Alle børn</a>' in html  # the filter keeps the search
    assert 'value="jul"' in html  # the query stays in the field


def test_a_query_without_words_finds_nothing_and_breaks_nothing():
    with archive.open() as a:
        a.save_post(post(1, 'Juleklip "AND" *'))
    for q in ('"', "*", "AND OR NOT", '" OR "'):
        assert hits(search(q=q)) == [], q
