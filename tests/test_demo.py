"""The demo (``python -m aula_agent.demo <dir>``): made-up data for trying the web page, never the real data/."""

import datetime as dt
import re
import sys

import pytest
from starlette.testclient import TestClient

from aula_agent import demo


def test_the_demo_fills_its_directory_with_two_children_todos_and_events_and_lets_the_page_in(data_dir):
    today = dt.date.today()  # the demo's page has the real today
    demo.fill(today, 8000)
    with TestClient(demo.demo_app(), base_url="http://127.0.0.1:8000") as c:
        html = c.get("/").text
        assert 'id="barn-filter"' in html and "Emma" in html and "Oscar" in html
        assert 'class="row todo-row"' in html and 'class="row event"' in html
        near = html.split('<section id="i-dag-og-i-morgen"', 1)[1].split("</section>", 1)[0]
        on_today, on_tomorrow = near.split(f'data-day="{today + dt.timedelta(days=1)}"')
        assert re.search(r'<span class="date time">\d\d:\d\d</span>', on_today)
        assert '<span class="date time">hele dagen' in on_tomorrow
        assert c.get("/aula/login").status_code == 403 and c.get("/google").status_code == 403
        assert "ikke hentet fra Aula" not in html  # the demo has had a good night


@pytest.mark.parametrize("target", ["data", "data/sub", "."])
def test_the_demo_refuses_the_real_data_directory(tmp_path, target):
    real = tmp_path / "data"
    real.mkdir()
    with pytest.raises(SystemExit):
        demo.refuse_real_data(tmp_path / target, real)
    demo.refuse_real_data(tmp_path / "demo", real)  # elsewhere is fine


def test_the_demo_will_not_start_after_store_is_imported(tmp_path):
    with pytest.raises(SystemExit, match="for sig"):
        demo.main([str(tmp_path / "demo")])  # the tests have imported store
    assert not (tmp_path / "demo").exists()


def test_the_demo_will_not_start_when_the_data_directory_is_not_its_own(tmp_path, monkeypatch):
    # As if store were not imported yet; ``from . import store`` still finds it on the package, with DATA_DIR
    # set by the tests' data_dir: then the demo would not use its own directory.
    monkeypatch.delitem(sys.modules, "aula_agent.store")
    monkeypatch.delenv("AULA_AGENT_DATA", raising=False)
    with pytest.raises(SystemExit, match="ville bruge"):
        demo.main([str(tmp_path / "demo")])
    assert not (tmp_path / "demo").exists()
