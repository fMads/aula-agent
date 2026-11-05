import asyncio
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from aula_agent import store, web

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
        r = c.get("/")
        assert r.status_code == 200 and "parent.one@example.com" in r.text


def test_post_needs_same_origin():
    configure()
    with make_client() as c:
        r = c.post("/aula/login", data={"username": "x"}, headers={**auth(), "Origin": "https://evil.example"})
        assert r.status_code == 403
        r = c.post("/aula/login", data={"username": "x"}, headers=auth())  # no Origin/Referer at all
        assert r.status_code == 403


# ------------------------------------------------------------------ pages


def test_home_shows_status_events_and_escapes_html(monkeypatch, tmp_path):
    monkeypatch.setattr("aula.config.DEFAULT_TOKEN_FILE", tmp_path / "no-tokens.json")
    configure()
    cfg = store.load_config()
    cfg["children"]["1"] = {"name": "Emma Jensen", "institution": "Skolen", "calendar_id": "c1", "calendar_name": "Skole – Emma"}
    store.save_config(cfg)
    st = store.load_state()
    st["events"]["k1|c1"] = {
        "key": "k1", "child_id": 1, "calendar_id": "c1", "event_id": "e1",
        "title": "<script>alert(1)</script>Juleklip", "start": "2099-12-04", "end": "2099-12-04",
        "source_item": {"title": "Opslag om jul"}, "link": "https://cal/e1",
    }
    st["events"]["old|c1"] = {"key": "old", "child_id": 1, "calendar_id": "c1", "event_id": "e0", "title": "Gammel", "start": "2001-01-01"}
    store.save_state(st)
    store.log_run("fetch", True, "2 nye/ændrede, 0 venter")
    with make_client() as c:
        r = c.get("/", headers=auth())
    assert "&lt;script&gt;" in r.text and "<script>alert" not in r.text
    assert "04.12.2099" in r.text and "Emma" in r.text and "Opslag om jul" in r.text
    assert "Gammel" not in r.text
    assert "2 nye/ændrede" in r.text
    assert "Ikke logget ind" in r.text  # no Aula session in the test environment


class FakeEvents:
    def __init__(self):
        self.deleted = []

    def events(self):
        return self

    def delete(self, calendarId, eventId):
        self.deleted.append((calendarId, eventId))
        return type("C", (), {"execute": lambda self: {}})()


def test_remove_event_deletes_everywhere_and_is_remembered():
    configure()
    st = store.load_state()
    for cal in ("c1", "c2"):
        st["events"][f"k1|{cal}"] = {"key": "k1", "child_id": 1, "calendar_id": cal, "event_id": f"e-{cal}", "title": "Skovtur", "start": "2099-01-01"}
    store.save_state(st)
    fake = FakeEvents()
    with make_client(google_services=lambda: (fake, None)) as c:
        r = c.post("/events/remove", data={"key": "k1"}, headers={**auth(), "Origin": ORIGIN}, follow_redirects=False)
    assert r.status_code == 303 and "fjernet" in r.headers["location"].replace("%20", " ")
    assert sorted(fake.deleted) == [("c1", "e-c1"), ("c2", "e-c2")]
    st = store.load_state()
    assert st["events"] == {} and "k1" in st["ignored"]


def test_remove_while_sync_running_says_busy():
    configure()
    st = store.load_state()
    st["events"]["k1|c1"] = {"key": "k1", "child_id": 1, "calendar_id": "c1", "event_id": "e1", "title": "x", "start": "2099-01-01"}
    store.save_state(st)
    with store.lock(), make_client(google_services=lambda: (FakeEvents(), None)) as c:
        r = c.post("/events/remove", data={"key": "k1"}, headers={**auth(), "Origin": ORIGIN}, follow_redirects=False)
    assert "Pr%C3%B8v%20igen" in r.headers["location"]
    assert store.load_state()["events"]


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
    assert store.load_state()["runs"][-1]["command"] == "aula-login"


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
