"""Logins: is Aula / Google usable (and if not, why), and the Google OAuth flows.

Owns the token files: Aula's session lives where the ``aula`` library keeps it
(``~/.config/aula``), Google's in ``data/google_token.json``. Google scopes are
kept narrow:

- ``calendar.app.created``: only calendars this tool creates itself.
- ``drive.file``: only files this tool uploads itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import aula.config as aula_config

from . import store
from .ledger import Ledger
from .runs import Outcome, Problem

SCOPES = [
    "https://www.googleapis.com/auth/calendar.app.created",
    "https://www.googleapis.com/auth/drive.file",
]
DESKTOP_REDIRECT_URI = "http://localhost:8765/"


class GoogleLoginRequired(Problem):
    outcome = Outcome.GOOGLE_LOGIN
    label = "Google-login mangler"


@dataclass(frozen=True)
class Status:
    ok: bool
    text: str  # short Danish status for people


# ----------------------------------------------------------------------------- Aula


def aula_username() -> str | None:
    return aula_config.load_config().get("mitid_username") or None


def aula_token_file() -> Path:
    return Path(aula_config.DEFAULT_TOKEN_FILE)


def aula_session_saved() -> bool:
    return bool(aula_username()) and aula_token_file().exists()


def remember_aula_username(username: str) -> None:
    cfg = aula_config.load_config()
    if cfg.get("mitid_username") != username:
        cfg["mitid_username"] = username
        aula_config.save_config(cfg)


def aula_status(ledger: Ledger | None = None) -> Status:
    if not aula_session_saved():
        return Status(False, "Ikke logget ind")
    # The newest fetch or successful login tells whether the session works; a
    # failed login attempt changes nothing about the session.
    last = next(
        (r for r in (ledger or Ledger.load()).runs() if r["command"] == "fetch" or (r["command"] == "aula-login" and r["ok"])),
        None,
    )
    if last and last["outcome"] == Outcome.AULA_LOGIN:
        return Status(False, "Login udløbet")
    return Status(True, "Logget ind")


# ----------------------------------------------------------------------------- Google


def google_status() -> Status:
    if store.path("GOOGLE_TOKEN").exists():
        return Status(True, "Forbundet")
    if not store.path("GOOGLE_CLIENT_SECRET").exists():
        return Status(False, "Mangler OAuth-nøgle")
    return Status(False, "Ikke forbundet")


def client_secret_kind() -> str | None:
    """``"web"`` or ``"installed"`` (desktop) for the uploaded OAuth key, None if there is none."""
    try:
        return next(iter(json.loads(store.path("GOOGLE_CLIENT_SECRET").read_text())))
    except Exception:
        return None


def save_client_secret(raw: bytes) -> None:
    """Store an OAuth client key from Google Cloud. Raises ValueError if it isn't one."""
    try:
        data = json.loads(raw)
        kind = next(iter(data))
    except Exception as exc:
        raise ValueError("ikke en OAuth-nøgle") from exc
    if kind not in ("web", "installed") or "client_id" not in data[kind]:
        raise ValueError("ikke en OAuth-nøgle")
    store.write_json(store.path("GOOGLE_CLIENT_SECRET"), data, private=True)


def _save_token(creds) -> None:
    store.write_json(store.path("GOOGLE_TOKEN"), json.loads(creds.to_json()), private=True)


def google_credentials():
    """Valid Google credentials, refreshed (and saved) when needed."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token = store.path("GOOGLE_TOKEN")
    if not token.exists():
        raise GoogleLoginRequired("Ingen Google-adgang endnu.")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds.valid:
        return creds
    if creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            raise GoogleLoginRequired(f"Google-adgangen kunne ikke fornyes: {exc}") from exc
        _save_token(creds)
        return creds
    raise GoogleLoginRequired("Google-adgangen er udløbet.")


def _client_secret_file() -> str:
    secret = store.path("GOOGLE_CLIENT_SECRET")
    if not secret.exists():
        raise FileNotFoundError(f"Læg OAuth-nøglen fra Google Cloud her: {secret}")
    return str(secret)


def interactive_login(prompt=input, echo=print) -> None:
    """OAuth for a desktop client, by pasting the redirect URL back.

    Works on a headless server too: the browser can be on any machine, and the
    "page cannot be reached" after approving is expected - its URL holds the code.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(_client_secret_file(), SCOPES, redirect_uri=DESKTOP_REDIRECT_URI)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    echo("1. Åbn denne adresse i en browser og godkend adgangen:\n")
    echo(url + "\n")
    echo("2. Bagefter viser browseren en fejlside (localhost kan ikke nås) – det er forventet.")
    pasted = prompt("3. Kopiér hele adressen fra browserens adresselinje og indsæt den her: ").strip()
    code = parse_qs(urlparse(pasted).query).get("code", [pasted])[0]
    flow.fetch_token(code=code)
    _save_token(flow.credentials)


def web_flow_start(redirect_uri: str) -> tuple[str, str, str]:
    """Start OAuth for the web UI. Returns (authorization_url, state, code_verifier)."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(_client_secret_file(), SCOPES, redirect_uri=redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    return url, state, flow.code_verifier


def web_flow_finish(redirect_uri: str, state: str, code_verifier: str, code: str) -> None:
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        _client_secret_file(), SCOPES, redirect_uri=redirect_uri, state=state, code_verifier=code_verifier
    )
    flow.fetch_token(code=code)
    _save_token(flow.credentials)


def google_services() -> tuple[Any, Any]:
    """Calendar v3 and Drive v3 clients."""
    from googleapiclient.discovery import build

    creds = google_credentials()
    return (
        build("calendar", "v3", credentials=creds, cache_discovery=False),
        build("drive", "v3", credentials=creds, cache_discovery=False),
    )
