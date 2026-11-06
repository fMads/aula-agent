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
    for path, params in (("/overblik", {}), ("/overblik", {"q": "skov"}), ("/overblik/item/post:1", {})):
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
