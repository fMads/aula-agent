import asyncio
import datetime as dt
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from aula_agent import archive, backup, runs, store, web
from aula_agent.ledger import Ledger, SyncedEntry
from aula_agent.runs import Outcome

TEAM = "myteam.cloudflareaccess.com"
AUD = "aud-tag-123"
ORIGIN = "https://aula.example.dk"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(email="parent.one@example.com", aud=AUD, iss=f"https://{TEAM}", key=KEY, exp_in=300):
    now = int(time.time())
    return jwt.encode({"email": email, "aud": aud, "iss": iss, "iat": now, "exp": now + exp_in}, key, algorithm="RS256")


def configure(**web_cfg):
    cfg = store.load_config()
    cfg["web"] = {
        "public_url": ORIGIN,
        "allowed_emails": ["parent.one@example.com", "parent.two@example.com"],
        "access_team_domain": TEAM,
        "access_aud": AUD,
        **web_cfg,
    }
    store.save_config(cfg)


def verifier_factory():
    c = web.web_config()
    if not (c["access_team_domain"] and c["access_aud"]):
        return None
    return web.AccessVerifier(c["access_team_domain"], c["access_aud"], key_for_token=lambda t: KEY.public_key())


def make_client(**kw):
    return TestClient(web.create_app(verifier_factory=verifier_factory, **kw), base_url=ORIGIN)


def auth(tok=None):
    return {"Cf-Access-Jwt-Assertion": tok or token()}


# ------------------------------------------------------------------ access control


def test_refuses_everything_when_access_not_configured():
    with make_client() as c:
        assert c.get("/", headers=auth()).status_code == 503


@pytest.mark.parametrize(
    "headers",
    [
        {},
        auth(token(aud="other-app")),
        auth(token(iss="https://evil.cloudflareaccess.com")),
        auth(token(key=OTHER_KEY)),
        auth(token(exp_in=-60)),
        auth(token(email="stranger@example.com")),
        {"Cf-Access-Jwt-Assertion": "garbage"},
    ],
    ids=["no-token", "wrong-aud", "wrong-issuer", "wrong-key", "expired", "not-allowed", "garbage"],
)
def test_rejects_bad_tokens(headers):
    configure()
    with make_client() as c:
        assert c.get("/", headers=headers).status_code == 403


def test_both_parents_get_in_and_cookie_works():
    configure()
    with make_client() as c:
        assert c.get("/", headers=auth(token("parent.two@example.com"))).status_code == 200
        c.cookies.set("CF_Authorization", token("Parent.One@example.com"))
        r = c.get("/settings")
        assert r.status_code == 200 and "parent.one@example.com" in r.text


def test_post_needs_same_origin():
    configure()
    with make_client() as c:
        r = c.post("/aula/login", data={"username": "x"}, headers={**auth(), "Origin": "https://evil.example"})
        assert r.status_code == 403
        r = c.post("/aula/login", data={"username": "x"}, headers=auth())  # no Origin/Referer at all
        assert r.status_code == 403


# ------------------------------------------------------------------ pages


def test_home_shows_status_children_and_runs_and_escapes_html(monkeypatch, tmp_path):
    monkeypatch.setattr("aula.config.DEFAULT_TOKEN_FILE", tmp_path / "no-tokens.json")
    configure()
    cfg = store.load_config()
    cfg["children"]["1"] = {"name": "Emma Jensen", "institution": "<script>alert(1)</script>Skolen",
                            "calendar_id": "c1", "calendar_name": "Aula – Emma"}
    store.save_config(cfg)
    runs.record("fetch", Outcome.OK, "2 nye/ændrede, 0 venter")
    with make_client() as c:
        r = c.get("/settings", headers=auth())
    assert "&lt;script&gt;" in r.text and "<script>alert" not in r.text
    assert "Emma Jensen" in r.text and "Aula – Emma" in r.text
    assert "2 nye/ændrede" in r.text
    assert "Ikke logget ind" in r.text  # no Aula session in the test environment


def test_home_shows_how_much_the_archive_holds():
    configure()
    with make_client() as c:
        assert "Arkiv" in c.get("/settings", headers=auth()).text and "intet gemt endnu" in c.get("/settings", headers=auth()).text
    assert not store.ARCHIVE_FILE.exists()  # the web page only reads the archive

    before = dt.datetime.now()
    with archive.open() as a:
        for pid in (1, 2):
            a.save_post({"id": pid, "title": f"Opslag {pid}", "content": {"html": "<p>x</p>"}, "ownerProfile": {"id": 3, "profileId": 4}})
        a.save_thread(
            "7",
            [{"id": "m1", "messageType": "Message", "text": {"html": "<p>Hej</p>"}},
             {"id": "s1", "messageType": "RecipientsAdded"}],
            thread={"id": 7, "subject": "Tur"},
        )
    with make_client() as c:
        r = c.get("/settings", headers=auth())
    assert "2 opslag · 1 besked · sidst gemt" in r.text
    assert any(f"sidst gemt {t:%d.%m. kl. %H:%M}" in r.text for t in (before, dt.datetime.now()))


def synced_on_two_calendars(google):
    cfg = store.load_config()
    cfg["children"].update({"1": {"name": "Emma Hansen"}, "2": {"name": "Oscar Hansen"}})
    store.save_config(cfg)
    ledger = Ledger()
    for cid, cal in ((1, "c1"), (2, "c2")):
        google.events[(cal, f"e-{cal}")] = {"extendedProperties": {"private": {"aulaAgentKey": "k1"}}}
        ledger.record(SyncedEntry("k1", [cid], cal, f"e-{cal}", "Skovtur", "2099-01-01", "2099-01-01"))
    ledger.save()


def test_remove_event_for_one_child_is_remembered(google):
    configure()
    synced_on_two_calendars(google)
    with make_client(google=lambda: google) as c:
        r = c.post("/events/remove", data={"key": "k1", "child_id": "2"}, headers={**auth(), "Origin": ORIGIN}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/settings?msg=")  # where the button was
    assert "fjernet" in r.headers["location"].replace("%20", " ")
    assert list(google.events) == [("c1", "e-c1")]
    ledger = Ledger.load()
    assert [u.child_ids for u in ledger.upcoming()] == [[1]]
    assert ledger.is_removed("k1", 2) and not ledger.is_removed("k1", 1)


def test_remove_from_an_old_page_without_child_removes_everywhere(google):
    configure()
    synced_on_two_calendars(google)
    with make_client(google=lambda: google) as c:
        c.post("/events/remove", data={"key": "k1"}, headers={**auth(), "Origin": ORIGIN}, follow_redirects=False)
    ledger = Ledger.load()
    assert google.events == {} and ledger.upcoming() == []
    assert ledger.is_removed("k1", 1) and ledger.is_removed("k1", 2)


def test_remove_while_sync_running_says_busy(google):
    configure()
    synced_on_two_calendars(google)
    with store.lock(), make_client(google=lambda: google) as c:
        r = c.post("/events/remove", data={"key": "k1"}, headers={**auth(), "Origin": ORIGIN}, follow_redirects=False)
    assert "Pr%C3%B8v%20igen" in r.headers["location"]
    assert len(google.events) == 2 and Ledger.load().upcoming()


def test_home_says_login_expired_after_fetch_needed_login(monkeypatch, tmp_path):
    token = tmp_path / "tokens.json"
    token.write_text("{}")
    monkeypatch.setattr("aula.config.DEFAULT_TOKEN_FILE", token)
    monkeypatch.setattr("aula.config.load_config", lambda: {"mitid_username": "u"})
    configure()
    with make_client() as c:
        assert "Logget ind" in c.get("/settings", headers=auth()).text
        runs.record("fetch", Outcome.AULA_LOGIN, "Session udløbet")
        assert "Login udløbet" in c.get("/settings", headers=auth()).text
        runs.record("aula-login", Outcome.ERROR, "Login tog for lang tid")  # a failed attempt changes nothing
        assert "Login udløbet" in c.get("/settings", headers=auth()).text
        runs.record("aula-login", Outcome.OK, "Logget ind")
        assert "Login udløbet" not in c.get("/settings", headers=auth()).text


def test_home_says_login_expired_after_backfill_needed_login(monkeypatch, tmp_path):
    token = tmp_path / "tokens.json"
    token.write_text("{}")
    monkeypatch.setattr("aula.config.DEFAULT_TOKEN_FILE", token)
    monkeypatch.setattr("aula.config.load_config", lambda: {"mitid_username": "u"})
    configure()
    with make_client() as c:
        runs.record("backfill", Outcome.AULA_LOGIN, "Session udløbet")
        assert "Login udløbet" in c.get("/settings", headers=auth()).text
        runs.record("backfill-rescue", Outcome.OK, "2 opslag")  # a rescue copy says nothing about the session
        assert "Login udløbet" in c.get("/settings", headers=auth()).text
        runs.record("backfill", Outcome.OK, "2 opslag")
        assert "Login udløbet" not in c.get("/settings", headers=auth()).text


# ------------------------------------------------------------------ MitID login


class FakeQR:
    def __init__(self, bits):
        self.bits = bits

    def get_matrix(self):
        return [[b == "1" for b in row] for row in self.bits]


def test_mitid_login_flow_shows_qr_then_finishes(monkeypatch, tmp_path):
    configure()
    saved = {}
    monkeypatch.setattr("aula.config.load_config", lambda: {})
    monkeypatch.setattr("aula.config.save_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr("aula.config.DEFAULT_TOKEN_FILE", tmp_path / "tokens.json")
    proceed = asyncio.Event()

    async def fake_auth(username, storage, *, on_qr_codes, on_qr_done, on_otp_code, on_identity_selected, force_login):
        assert force_login
        on_qr_codes(FakeQR(["10", "01"]), FakeQR(["11", "00"]))
        await proceed.wait()
        on_qr_done()
        return {"tokens": {}}

    login = web.MitIDLogin(authenticate=fake_auth)
    with make_client(mitid=login) as c:
        r = c.post("/aula/login", data={"username": "mads123"}, headers={**auth(), "Origin": ORIGIN}, follow_redirects=False)
        assert r.headers["location"] == "/aula/login?watch=1"
        for _ in range(50):
            s = c.get("/aula/login/state", headers=auth()).json()
            if s["status"] == "scan":
                break
            time.sleep(0.01)
        assert s["status"] == "scan" and len(s["qr"]) == 2 and s["qr"][0].startswith("<svg")
        c.portal.call(proceed.set)
        for _ in range(50):
            s = c.get("/aula/login/state", headers=auth()).json()
            if s["status"] == "done":
                break
            time.sleep(0.01)
    assert s["status"] == "done" and s["qr"] == []
    assert saved == {"mitid_username": "mads123"}
    assert Ledger.load().last_run("aula-login")["ok"] is True


def test_mitid_login_is_rate_limited():
    configure()

    async def failing_auth(*a, **kw):
        raise RuntimeError("nej")

    login = web.MitIDLogin(authenticate=failing_auth)
    with make_client(mitid=login) as c:
        for _ in range(web.MitIDLogin.MAX_ATTEMPTS):
            c.post("/aula/login", data={"username": "u"}, headers={**auth(), "Origin": ORIGIN})
            for _ in range(50):
                if not login.running:
                    break
                time.sleep(0.01)
        r = c.post("/aula/login", data={"username": "u"}, headers={**auth(), "Origin": ORIGIN})
    assert "For mange login-forsøg" in r.text


# ------------------------------------------------------------------ Google


def test_google_client_secret_upload_validates_type():
    configure()
    with make_client() as c:
        h = {**auth(), "Origin": ORIGIN}
        r = c.post("/google/client-secret", files={"file": ("x.json", b'{"nope": 1}')}, headers=h)
        assert "ligner ikke" in r.text
        c.post("/google/client-secret", files={"file": ("x.json", b'{"installed": {"client_id": "a"}}')}, headers=h)
        assert "Web application" in c.get("/google", headers=auth()).text
        c.post("/google/client-secret", files={"file": ("x.json", b'{"web": {"client_id": "a"}}')}, headers=h)
        assert "Forbind Google" in c.get("/google", headers=auth()).text
    assert (store.GOOGLE_CLIENT_SECRET.stat().st_mode & 0o777) == 0o600


def test_google_callback_rejects_unknown_state():
    configure()
    with make_client() as c:
        r = c.get("/google/callback?state=forged&code=abc", headers=auth(), follow_redirects=False)
    assert "udl%C3%B8b" in r.headers["location"]
    assert not store.GOOGLE_TOKEN.exists()


def archive_card(c):
    text = c.get("/settings", headers=auth()).text
    return text[text.index("<b>Arkiv</b>"):].split("</div>")[0]


def test_archive_card_shows_the_last_backup_and_warns_when_it_is_missing_or_old(google):
    configure()
    with make_client() as c:
        card = archive_card(c)
    assert "ingen backup endnu" in card and 'class="bad"' in card

    taken = dt.datetime.now() - dt.timedelta(hours=49)
    backup.take(google, at=taken)
    with make_client() as c:
        card = archive_card(c)
    assert f"backup {taken:%d.%m. kl. %H:%M}" in card and "over 2 døgn gammel" in card and 'class="bad"' in card

    taken = dt.datetime.now() - dt.timedelta(hours=47)
    backup.take(google, at=taken)
    with make_client() as c:
        card = archive_card(c)
    assert f"backup {taken:%d.%m. kl. %H:%M}" in card and "over 2 døgn" not in card and 'class="bad"' not in card


def test_remove_for_one_child_in_a_joint_calendar_keeps_the_entry_until_no_child_is_left(google):
    configure()
    cfg = store.load_config()
    cfg["children"].update({"1": {"name": "Emma Hansen", "calendar_id": "joint"}, "2": {"name": "Oscar Hansen", "calendar_id": "joint"}})
    store.save_config(cfg)
    google.events[("joint", "e1")] = {"extendedProperties": {"private": {"aulaAgentKey": "k1"}}}
    ledger = Ledger()
    ledger.record(SyncedEntry("k1", [1, 2], "joint", "e1", "Skovtur", "2099-01-01", "2099-01-01"))
    ledger.save()
    with make_client(google=lambda: google) as c:
        c.post("/events/remove", data={"key": "k1", "child_id": "1"}, headers={**auth(), "Origin": ORIGIN}, follow_redirects=False)
        assert list(google.events) == [("joint", "e1")]
        ledger = Ledger.load()
        assert ledger.is_removed("k1", 1) and not ledger.is_removed("k1", 2)
        assert [u.child_ids for u in ledger.upcoming()] == [[2]]
        c.post("/events/remove", data={"key": "k1", "child_id": "2"}, headers={**auth(), "Origin": ORIGIN}, follow_redirects=False)
    assert google.events == {} and Ledger.load().upcoming() == []


# ------------------------------------------------------------------ overview (the timeline is in test_overview.py)


def post(pid, title, publish="2026-11-02T08:00:00", body="<p>x</p>"):
    return {"id": pid, "title": title, "content": {"html": body}, "publishAt": publish,
            "ownerProfile": {"id": 3, "profileId": 4}}


def classify(a, key, category, *, topics=(), summary=None, start=None, end=None):
    a.classify(key, archive.Classification(
        archive.Category(category), start and dt.date.fromisoformat(start), end and dt.date.fromisoformat(end),
        tuple(topics), summary,
    ))


def test_overview_without_an_archive_or_with_one_not_migrated_yet_says_so_and_changes_nothing():
    import sqlite3

    configure()
    with make_client() as c:
        r = c.get("/", headers=auth())
    assert r.status_code == 200 and "ikke klar" in r.text
    assert not store.ARCHIVE_FILE.exists()

    with archive.open() as a:
        a.save_post(post(1, "Juleklip"))
    db = sqlite3.connect(store.ARCHIVE_FILE)
    db.execute("PRAGMA user_version = 3")  # as before classifications
    db.commit()
    db.close()
    with make_client() as c:
        r = c.get("/", headers=auth())
    assert r.status_code == 200 and "ikke klar" in r.text
    db = sqlite3.connect(store.ARCHIVE_FILE)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    db.close()


def test_the_front_page_and_the_settings_link_to_each_other():
    configure()
    with make_client() as c:
        assert 'href="/"' in c.get("/settings", headers=auth()).text
        assert 'href="/settings"' in c.get("/", headers=auth()).text
