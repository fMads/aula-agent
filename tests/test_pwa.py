"""The page as an app on the home screen (#58): a manifest with the app's name (``web.app_name``, default "Overblik"),
its icons, and the pages linking to them. There is no service worker."""

import re

import pytest

from test_web import auth, configure, make_client


def get(path, **web_cfg):
    configure(**web_cfg)
    with make_client() as c:
        return c.get(path, headers=auth())


def test_the_manifest_is_named_overblik_by_default_and_opens_the_front_page_as_an_app():
    r = get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["name"] == "Overblik" and m["short_name"] == "Overblik"
    assert m["start_url"] == "/" and m["display"] == "standalone"
    assert m["theme_color"]


def test_the_manifest_uses_the_app_name_from_the_config():
    m = get("/manifest.webmanifest", app_name="Hjemme").json()
    assert m["name"] == "Hjemme" and m["short_name"] == "Hjemme"


def test_the_manifest_is_behind_access_like_everything_else():
    configure()
    with make_client() as c:
        assert c.get("/manifest.webmanifest").status_code == 403


def test_every_icon_in_the_manifest_is_handed_out_with_its_type_and_nothing_else_under_static_is():
    configure()
    with make_client() as c:
        icons = c.get("/manifest.webmanifest", headers=auth()).json()["icons"]
        assert {i["sizes"] for i in icons} >= {"192x192", "512x512"}
        assert {i["type"] for i in icons} == {"image/svg+xml", "image/png"}
        for icon in icons:
            r = c.get(icon["src"], headers=auth())
            assert r.status_code == 200 and r.headers["content-type"] == icon["type"]
            if icon["type"] == "image/png":
                assert r.content.startswith(b"\x89PNG")
                size = icon["sizes"].split("x")[0]
                assert int.from_bytes(r.content[16:20]) == int(size) == int.from_bytes(r.content[20:24])
            else:
                assert r.text.lstrip().startswith("<svg")
        for other in ("icon.png", "icon-256.png", "../web.py", "andet.svg"):
            assert c.get(f"/static/{other}", headers=auth()).status_code == 404


@pytest.mark.parametrize("path", ["/", "/search?q=tur", "/settings", "/item/post:1", "/google"])
def test_every_page_links_to_the_manifest_and_the_icon(path):
    r = get(path)
    assert '<link rel="manifest" href="/manifest.webmanifest">' in r.text
    assert '<link rel="apple-touch-icon" href="/static/icon-192.png">' in r.text
    assert '<meta name="theme-color" content="' in r.text


def test_the_app_name_is_the_heading_and_the_front_page_title_and_aula_to_calendar_is_gone():
    for path in ("/", "/settings"):
        r = get(path, app_name="Hjemme")
        assert re.search(r"<h1>.*Hjemme.*</h1>", r.text)
        assert "Aula → Kalender" not in r.text
    assert "<title>Hjemme</title>" in get("/", app_name="Hjemme").text
    assert re.search(r"<h1>.*Overblik.*</h1>", get("/settings").text)
