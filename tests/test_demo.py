"""The demo (``python -m aula_agent.demo <dir>``): made-up data for trying the web page, never the real data/."""

import datetime as dt

import pytest
from starlette.testclient import TestClient

from aula_agent import demo


def test_the_demo_fills_its_directory_with_two_children_todos_and_events_and_lets_the_page_in(data_dir):
    demo.fill(dt.date(2026, 11, 6), 8000)
    with TestClient(demo.demo_app(), base_url="http://127.0.0.1:8000") as c:
        html = c.get("/overblik").text
        assert 'id="barn-filter"' in html and "Emma" in html and "Oscar" in html
        assert 'class="row todo-row"' in html and 'class="row event"' in html
        assert c.get("/aula/login").status_code == 403 and c.get("/google").status_code == 403


@pytest.mark.parametrize("target", ["data", "data/sub", "."])
def test_the_demo_refuses_the_real_data_directory(tmp_path, target):
    real = tmp_path / "data"
    real.mkdir()
    with pytest.raises(SystemExit):
        demo.refuse_real_data(tmp_path / target, real)
    demo.refuse_real_data(tmp_path / "demo", real)  # elsewhere is fine
