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
    for path, params in (("/", {}), ("/search", {"q": "skov"}), ("/item/post:1", {})):
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
    html = get("/").text
    script = (web.STATIC_DIR / "overblik.js").read_text(encoding="utf-8")
    for element, id_ in (("main", "overblik-main"), ("div", "overblik-top"), ("nav", "barn-filter"),
                         ("form", "search"), ("input", "q")):
        assert re.search(rf'<{element}\b[^>]*\sid="{id_}"', html), id_
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
    assert '"/search"' in script and '"/#q"' in script  # the lists it swaps, and "/" from a detail as a whole page
    assert "/overblik" not in script.replace("/static/overblik.js", "")
