"""The overview's JS layer (#32): what the pages give the script, and what works without it.

The script itself is not tested automatically (spec #23); these tests cover what it relies on.
"""

import datetime as dt
import re

from aula_agent import archive
from test_web import ORIGIN, auth, classify, configure, make_client, post

TODAY = dt.date(2026, 11, 6)


def get(path, **params):
    configure()
    with make_client(today=lambda: TODAY) as c:
        return c.get(path, params=params, headers=auth())


def archive_with_a_post():
    with archive.open() as a:
        a.save_post(post(1, "Skovtur", publish="2026-11-02T08:00:00"))
        classify(a, "post:1", "arrangement", summary="Tur i skoven.", start="2026-11-20")


def test_the_overview_and_the_detail_load_the_script_from_the_site_under_a_csp_without_inline_script():
    archive_with_a_post()
    for path, params in (("/", {}), ("/info", {}), ("/past", {}), ("/search", {"q": "skov"}), ("/item/post:1", {})):
        r = get(path, **params)
        assert r.status_code == 200
        assert '<script src="/static/overblik.js" defer></script>' in r.text
        assert re.findall(r"<script[^>]*>", r.text) == ['<script src="/static/overblik.js" defer>']
        assert not re.search(r"\son[a-z]+=", r.text)  # no inline event handlers either
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "default-src 'self'" in csp
        assert '<main id="overblik-main">' in r.text  # what the script swaps


def test_the_script_is_served_as_javascript_behind_access():
    configure()
    with make_client() as c:
        assert c.get("/static/overblik.js").status_code == 403
        r = c.get("/static/overblik.js", headers=auth())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert "overblik-main" in r.text
    with make_client() as c:
        assert c.get("/static/andet.js", headers=auth()).status_code == 404


def test_the_pages_and_the_script_agree_on_the_ids_the_script_swaps_and_listens_on():
    from aula_agent import store, web

    archive_with_a_post()
    cfg = store.load_config()
    cfg["children"] = {"1": {"name": "Emma Test"}, "2": {"name": "Oscar Test"}}
    store.save_config(cfg)
    script = (web.STATIC_DIR / "overblik.js").read_text(encoding="utf-8")
    for path in ("/", "/info", "/past"):
        html = get(path).text
        for element, id_ in (("main", "overblik-main"), ("div", "overblik-top"), ("nav", "barn-filter"),
                             ("form", "search"), ("input", "q"), ("nav", "faner")):  # the tabs: swapped with the list
            assert re.search(rf'<{element}\b[^>]*\sid="{id_}"', html), (path, id_)
            assert f'"{id_}"' in script or f"#{id_}" in script, id_


def search_form(html):
    return html.split('<form id="search"', 1)[1].split("</form>", 1)[0]


def test_the_filters_are_folded_under_filtre_open_when_one_is_chosen_and_the_button_is_only_without_script():
    archive_with_a_post()

    form = search_form(get("/").text)
    filters = re.search(r'<details class="filtre">\s*<summary>Filtre</summary>(.*?)</details>', form, re.S)
    assert filters and filters[1].count("<select") == 3
    assert re.search(r"<noscript><button>Søg</button></noscript>", form)
    assert form.index('id="q"') < form.index("<details")  # the search field is not folded

    chosen = search_form(get("/search", category="arrangement").text)
    assert '<details class="filtre" open>' in chosen and "Filtre (1)" in chosen


def test_the_script_opens_the_addresses_the_pages_link_to():
    from aula_agent import web

    archive_with_a_post()
    html = get("/").text
    script = (web.STATIC_DIR / "overblik.js").read_text(encoding="utf-8")
    assert 'class="item-link" href="/item/post:1"' in html  # a detail, which the script opens in the drawer
    assert 'startsWith("/item/")' in script and '"/file/"' in script
    assert 'action="/search"' in html and 'href="/settings"' in html  # the search field; the tab is a plain link
    assert all(f'"{path}"' in script for path in ("/search", "/info", "/past"))  # the lists it swaps
    assert '"/#q"' in script  # "/" from a detail as a whole page
    assert 'href="/info"' in html and 'href="/past"' in html
    assert "/overblik" not in script.replace("/static/overblik.js", "")


def no_inline_script(r):
    assert re.findall(r"<script[^>]*>", r.text) == ['<script src="/static/overblik.js" defer>']
    assert not re.search(r"\son[a-z]+=", r.text)
    assert "script-src 'self'" in r.headers["content-security-policy"]


def test_fjern_on_the_detail_asks_through_the_script_or_a_confirmation_page_both_without_inline_script():
    from aula_agent import store, web
    from aula_agent.ledger import Ledger, SyncedEntry

    archive_with_a_post()
    cfg = store.load_config()
    cfg["children"] = {"1": {"name": "Emma Test"}, "2": {"name": "Oscar Test"}}
    store.save_config(cfg)
    ledger = Ledger()
    ledger.record(SyncedEntry("post:1#1", [1, 2], "joint", "e1", "Skovtur", "2026-11-20", "2026-11-20", source="post:1"))
    ledger.add_history("post:1#1", "created", "post:1", title="Skovtur", start="2026-11-20", end="2026-11-20")
    ledger.save()

    detail = get("/item/post:1")
    no_inline_script(detail)
    forms = re.findall(r'<form\b[^>]*action="/events/remove"[^>]*>', detail.text)
    assert len(forms) == 2 and all('method="get"' in f and "data-confirm=" in f for f in forms)
    assert "Fjern »Skovtur« fra kalenderen for Emma?" in forms[0]

    confirmation = get("/events/remove", key="post:1#1", child_id="1", back="/item/post:1")
    no_inline_script(confirmation)
    assert re.search(r'<form method="post" action="/events/remove">', confirmation.text)

    script = (web.STATIC_DIR / "overblik.js").read_text(encoding="utf-8")
    assert "data-confirm" in script and "confirm(" in script


def todo_forms(html):
    """Each todo button's form: (its action, the item and version it posts)."""
    forms = re.findall(r'<form\b[^>]*action="(/todos/[a-z]+)"[^>]*>(.*?)</form>', html, re.S)
    return [(action, re.search(r'name="item" value="([^"]+)"', f)[1], re.search(r'name="version" value="(\d+)"', f)[1])
            for action, f in forms]


def todo_parts(html, ref):
    """The elements marked as the todo ``ref`` ("<item>@<version>"), each from its start tag to its form's end."""
    return re.findall(rf'<[a-z]+\b[^>]*\sdata-todo="{re.escape(ref)}"[^>]*>.*?</form>', html, re.S)


def test_every_todo_button_sits_in_the_element_the_script_marks_as_klaret_for_that_todo_on_every_list_and_the_detail():
    from aula_agent import web
    from aula_agent.ledger import Ledger

    with archive.open(at=dt.datetime(2026, 11, 2, 9, tzinfo=dt.UTC)) as a:
        a.save_post(post(1, "Skolefoto", publish="2026-11-02T08:00:00"))
        a.save_post(post(2, "Skovtur", publish="2026-11-02T08:00:00"))
        a.save_post(post(3, "Madpakke", publish="2026-11-02T08:00:00"))
        a.classify("post:1", archive.Classification(  # in Skal gøres and on tomorrow
            archive.Category.INFO, todo=archive.Todo("Bestil billeder", dt.date(2026, 11, 7))))
        a.classify("post:2", archive.Classification(  # on its item's line in Kommende
            archive.Category.ARRANGEMENT, dt.date(2026, 11, 20), todo=archive.Todo("Medbring madpakke", dt.date(2026, 11, 20))))
        a.classify("post:3", archive.Classification(archive.Category.INFO, todo=archive.Todo("Aflever sedlen")))
        versions = {t.item.key: t.version.id for t in a.todos()}
    ledger = Ledger()
    ledger.check_todo("post:3", versions["post:3"])
    ledger.save()
    ref = {key: f"{key}@{version}" for key, version in versions.items()}

    html = get("/").text
    forms = todo_forms(html)
    assert sorted(forms) == sorted([("/todos/check", "post:1", str(versions["post:1"]))] * 2  # Skal gøres and ...
                                   + [("/todos/check", "post:2", str(versions["post:2"]))] * 2
                                   + [("/todos/uncheck", "post:3", str(versions["post:3"]))])
    open_on_tomorrow = todo_parts(html, ref["post:1"])
    assert len(open_on_tomorrow) == 2  # Skal gøres and I dag og i morgen: the script marks both
    assert all("done" not in part.split(">", 1)[0] and 'title="Klaret"' in part for part in open_on_tomorrow)
    assert len(todo_parts(html, ref["post:2"])) == 2  # Skal gøres and its item's line in Kommende
    assert len(todo_parts(html.split('<section id="kommende"', 1)[1], ref["post:2"])) == 1
    [ticked] = todo_parts(html, ref["post:3"])
    assert re.search(r'class="[^"]*\bdone\b', ticked.split(">", 1)[0]) and 'title="Fortryd"' in ticked
    assert re.search(r'<section id="skal-goeres"><h2>Skal gøres<span class="count">2</span>', html)

    for key, label in (("post:1", "Klaret"), ("post:3", "Fortryd")):
        [part] = todo_parts(get(f"/item/{key}").text, ref[key])  # on the detail, as a page or in the drawer
        assert f">{label}</button>" in part

    script = (web.STATIC_DIR / "overblik.js").read_text(encoding="utf-8")
    assert "data-todo" in script and '"/todos/check"' in script and '"/todos/uncheck"' in script
    assert all(f'"{label}"' in script for label, _ in web.TODO_ACTIONS.values())
    assert "#skal-goeres" in script and ".count" in script  # its number of open todos
    assert "form.submit()" in script  # an error: sent the ordinary way
