from click.testing import CliRunner

import json

from aula_agent import aula_source, cli, gcal, store
from aula_agent.ledger import Ledger


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
    assert Ledger.load().last_run("fetch")["outcome"] == "aula_login"


def test_fetch_login_message_points_to_web_ui(monkeypatch):
    CliRunner().invoke(cli.main, ["web-setup", "--url", "https://aula.example.dk"])
    r = _fetch_needing_login(monkeypatch)
    assert "https://aula.example.dk/aula/login" in r.output


def test_apply_rejects_invalid_json_with_exit_5(tmp_path):
    bad = tmp_path / "events.json"
    bad.write_text('{"events": [')
    r = CliRunner().invoke(cli.main, ["apply", str(bad)])
    assert r.exit_code == cli.EXIT_BAD_EVENTS and "ikke gyldig JSON" in r.output


def test_apply_dry_run_reports_unprocessed_inbox_items(tmp_path):
    ledger = Ledger()
    ledger.add_pending({"key": "post:1", "child_ids": []})
    ledger.save()
    doc = tmp_path / "events.json"
    doc.write_text(json.dumps({"processed": [], "events": []}))
    r = CliRunner().invoke(cli.main, ["apply", str(doc), "--dry-run"])
    assert r.exit_code == cli.EXIT_BAD_EVENTS and "post:1" in r.output
    assert Ledger.load().last_run("apply") is None  # dry runs are not logged


def test_apply_without_google_exits_4(tmp_path):
    doc = tmp_path / "events.json"
    doc.write_text(json.dumps({"processed": [], "events": []}))
    r = CliRunner().invoke(cli.main, ["apply", str(doc)])
    assert r.exit_code == cli.EXIT_GOOGLE_LOGIN and "GOOGLE_LOGIN_REQUIRED" in r.output
    assert Ledger.load().last_run("apply")["outcome"] == "google_login"


def test_status_runs_without_any_setup(monkeypatch, tmp_path):
    monkeypatch.setattr("aula.config.DEFAULT_TOKEN_FILE", tmp_path / "none.json")
    r = CliRunner().invoke(cli.main, ["status"])
    assert r.exit_code == 0, r.output
    assert "Ikke logget ind" in r.output and "Mangler OAuth-nøgle" in r.output


def test_backup_without_google_exits_4():
    r = CliRunner().invoke(cli.main, ["backup"])
    assert r.exit_code == cli.EXIT_GOOGLE_LOGIN and "GOOGLE_LOGIN_REQUIRED" in r.output
    assert Ledger.load().last_run("backup")["outcome"] == "google_login"


def test_backup_takes_a_backup_now(monkeypatch, google):
    monkeypatch.setattr(gcal.Google, "connect", staticmethod(lambda: google))
    r = CliRunner().invoke(cli.main, ["backup"])
    assert r.exit_code == 0, r.output
    assert google.in_folder("Aula-arkiv") == ["archive.sqlite"]
    assert Ledger.load().last_run("backup")["outcome"] == "ok"
