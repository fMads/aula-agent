"""The web page's addresses (#50): the overview on /, the status page on /settings, the search on /search, an item
on /item/<key>; every old /overblik… address answers 301 to its new one (calendar events link there, #33)."""

import datetime as dt
import re

import pytest

from aula_agent import archive, store
from test_web import auth, classify, configure, make_client, post

TODAY = dt.date(2026, 11, 6)


def archive_with_a_post():
    with archive.open() as a:
        a.save_post(post(1, "Skovtur", publish="2026-11-02T08:00:00"))
        classify(a, "post:1", "arrangement", summary="Tur i skoven.", start="2026-11-20")


def get(path, **kw):
    configure()
    with make_client(today=lambda: TODAY) as c:
        return c.get(path, headers=auth(), **kw)


def test_the_overview_is_on_the_front_page_and_the_status_page_on_settings():
    archive_with_a_post()
    front = get("/")
    assert front.status_code == 200
    assert 'id="skal-goeres"' in front.text and 'id="kommende"' in front.text and "Skovtur" in front.text
    settings = get("/settings")
    assert settings.status_code == 200
    assert "Seneste kørsler" in settings.text and "Børn og kalendere" in settings.text
    assert 'id="skal-goeres"' not in settings.text


def test_the_search_has_its_own_address_and_the_search_field_sends_there():
    archive_with_a_post()
    r = get("/search", params={"q": "skov"})
    assert r.status_code == 200
    assert 'id="soegning"' in r.text and "Skovtur" in r.text
    assert '<form id="search" class="search" method="get" action="/search">' in r.text
    assert 'action="/search"' in get("/").text


def test_a_search_without_anything_to_search_for_is_the_front_page_and_the_front_page_sends_a_search_to_search():
    archive_with_a_post()
    r = get("/search", params={"q": "", "category": "", "child": "1"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/?child=1"
    r = get("/", params={"q": "skov"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/search?q=skov"


def test_the_tabs_are_forside_and_indstillinger():
    archive_with_a_post()
    nav = re.search(r'<nav class="tabs" id="faner">(.*?)</nav>', get("/settings").text)[1]
    assert nav == '<a href="/">Forside</a><b aria-current="page">Indstillinger</b>'
    nav = re.search(r'<nav class="tabs" id="faner">(.*?)</nav>', get("/").text)[1]
    assert nav == '<b aria-current="page">Forside</b><a href="/settings">Indstillinger</a>'


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/overblik", "/"),
        ("/overblik?child=1", "/?child=1"),
        ("/overblik?before=2026-10-05&child=2", "/?before=2026-10-05&child=2"),
        ("/overblik?q=skov", "/search?q=skov"),
        ("/overblik?q=sv%C3%B8m&category=arrangement&topic=tur&kind=post&child=1",
         "/search?q=sv%C3%B8m&category=arrangement&topic=tur&kind=post&child=1"),
        ("/overblik?category=frist", "/search?category=frist"),
        ("/overblik/item/post:1", "/item/post:1"),
        ("/overblik/item/message:4:7?show=1", "/item/message:4:7?show=1"),
        ("/overblik/item/post:1/fil/5", "/item/post:1/file/5"),
    ],
)
def test_every_old_address_answers_301_to_the_new_one_with_its_query(old, new):
    r = get(old, follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == new


def test_the_old_addresses_are_behind_access_like_the_rest():
    configure()
    with make_client() as c:
        assert c.get("/overblik/item/post:1", follow_redirects=False).status_code == 403


def test_an_items_address_is_item_and_its_key():
    assert store.item_path("post:1") == "/item/post:1"
    assert store.item_path("message:4:7") == "/item/message:4:7"
