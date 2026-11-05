from click.testing import CliRunner

from aula_agent import aula_source, cli, store


def test_web_setup_saves_and_shows_config():
    r = CliRunner().invoke(
        cli.main,
        ["web-setup", "--url", "https://aula.example.dk/", "--team-domain", "https://myteam.cloudflareaccess.com",
         "--aud", " tag ", "--email", "B@example.com", "--email", "a@example.com"],
    )
    assert r.exit_code == 0, r.output
    web = store.load_config()["web"]
    assert web == {
        "public_url": "https://aula.example.dk",
        "access_team_domain": "myteam.cloudflareaccess.com",
        "access_aud": "tag",
        "allowed_emails": ["a@example.com", "b@example.com"],
    }
    assert "afviser alle" not in r.output
    assert store.web_url("/google") == "https://aula.example.dk/google"


def test_web_setup_rejects_plain_http_and_reports_missing():
    r = CliRunner().invoke(cli.main, ["web-setup", "--url", "http://aula.example.dk"])
    assert r.exit_code != 0
    r = CliRunner().invoke(cli.main, ["web-setup"])
    assert "(mangler)" in r.output and "afviser alle" in r.output


def _fetch_needing_login(monkeypatch):
    async def needs_login(**kw):
        raise aula_source.LoginRequired("Den gemte Aula-session er udløbet.")

    monkeypatch.setattr(aula_source, "fetch", needs_login)
    return CliRunner().invoke(cli.main, ["fetch"])


def test_fetch_login_message_without_web_ui(monkeypatch):
    r = _fetch_needing_login(monkeypatch)
    assert r.exit_code == cli.EXIT_AULA_LOGIN
    assert "AULA_LOGIN_REQUIRED" in r.output and "uv run aula login" in r.output and "http" not in r.output
    assert store.load_state()["runs"][-1]["ok"] is False


def test_fetch_login_message_points_to_web_ui(monkeypatch):
    CliRunner().invoke(cli.main, ["web-setup", "--url", "https://aula.example.dk"])
    r = _fetch_needing_login(monkeypatch)
    assert "https://aula.example.dk/aula/login" in r.output
