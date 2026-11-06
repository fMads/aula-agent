"""Minimal web UI: status, MitID re-login, Google connect, upcoming events, the overview, one item and its files.

Meant to sit behind a Cloudflare Tunnel with Cloudflare Access in front. The app
does not trust the tunnel alone: every request must carry a valid Cloudflare
Access JWT (signed by the team's keys, for this application's AUD) for an e-mail
on the allow-list. Without Access settings in ``data/config.json`` every request
is refused.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import html
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple
from urllib.parse import quote, urlencode, urlparse, urlsplit

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.middleware import Middleware
from starlette.datastructures import QueryParams
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from . import archive, credentials, runs, store, timeline
from .ledger import EventHistory, Ledger, UpcomingEvent
from .runs import Outcome

# ----------------------------------------------------------------------------- config


DEFAULT_APP_NAME = "Overblik"  # never "Aula": not to be mistaken for Aula's own app


def web_config() -> dict[str, Any]:
    web = store.load_config().get("web") or {}
    return {
        "public_url": (web.get("public_url") or "").rstrip("/"),  # e.g. "https://aula.example.dk"
        "allowed_emails": [e.lower() for e in web.get("allowed_emails") or []],
        "access_team_domain": web.get("access_team_domain"),  # e.g. "myteam.cloudflareaccess.com"
        "access_aud": web.get("access_aud"),  # Application Audience (AUD) tag
        "app_name": (web.get("app_name") or "").strip() or DEFAULT_APP_NAME,  # on the home screen and every page
    }


# ----------------------------------------------------------------------------- auth


class AccessVerifier:
    """Validates the ``Cf-Access-Jwt-Assertion`` header Cloudflare Access adds."""

    def __init__(self, team_domain: str, aud: str, key_for_token: Callable[[str], Any] | None = None):
        self.issuer = f"https://{team_domain}"
        self.aud = aud
        if key_for_token is None:
            import jwt

            client = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)
            key_for_token = lambda token: client.get_signing_key_from_jwt(token).key  # noqa: E731
        self._key_for_token = key_for_token

    def email(self, token: str) -> str:
        import jwt

        claims = jwt.decode(
            token,
            self._key_for_token(token),
            algorithms=["RS256"],
            audience=self.aud,
            issuer=self.issuer,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
        return (claims.get("email") or "").lower()


class AccessMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, verifier_factory: Callable[[], AccessVerifier | None]):
        super().__init__(app)
        self._verifier_factory = verifier_factory
        self._verifier: AccessVerifier | None = None
        self._verifier_key: tuple | None = None

    def _verifier_now(self) -> tuple[AccessVerifier | None, dict]:
        cfg = web_config()
        key = (cfg["access_team_domain"], cfg["access_aud"])
        if self._verifier is None or key != self._verifier_key:
            self._verifier = self._verifier_factory()
            self._verifier_key = key
        return self._verifier, cfg

    async def dispatch(self, request: Request, call_next):
        verifier, cfg = self._verifier_now()
        if verifier is None or not cfg["allowed_emails"] or not cfg["public_url"]:
            return PlainTextResponse(
                "Web-siden er ikke sat op endnu. Kør `uv run aula-agent web-setup` (se README).", status_code=503
            )
        token = request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization")
        if not token:
            return PlainTextResponse("Adgang nægtet.", status_code=403)
        try:
            email = await asyncio.to_thread(verifier.email, token)
        except Exception:
            return PlainTextResponse("Adgang nægtet.", status_code=403)
        if email not in cfg["allowed_emails"]:
            return PlainTextResponse("Adgang nægtet.", status_code=403)
        if request.method not in ("GET", "HEAD"):
            # Same-origin check against cross-site form posts.
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            expected = urlparse(cfg["public_url"])
            got = urlparse(origin)
            if (got.scheme, got.netloc) != (expected.scheme, expected.netloc):
                return PlainTextResponse("Forkert oprindelse.", status_code=403)
        request.state.email = email
        return await call_next(request)


def default_verifier_factory() -> AccessVerifier | None:
    cfg = web_config()
    if not (cfg["access_team_domain"] and cfg["access_aud"]):
        return None
    return AccessVerifier(cfg["access_team_domain"], cfg["access_aud"])


# ----------------------------------------------------------------------------- MitID login


def qr_svg(qr) -> str:
    matrix = qr.get_matrix()
    size = len(matrix)
    path = "".join(f"M{x},{y}h1v1h-1z" for y, row in enumerate(matrix) for x, on in enumerate(row) if on)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#fff"/><path d="{path}" fill="#000"/></svg>'
    )


class MitIDLogin:
    """One MitID login at a time, run in the background, observed by polling."""

    MAX_ATTEMPTS = 3
    WINDOW_SECONDS = 15 * 60
    TIMEOUT_SECONDS = 5 * 60

    def __init__(self, authenticate=None):
        self._authenticate = authenticate
        self.reset()
        self._attempts: list[float] = []
        self._task: asyncio.Task | None = None

    def reset(self) -> None:
        self.status = "idle"  # idle | starting | scan | approve | done | error
        self.qr: list[str] = []
        self.generation = 0
        self.otp: str | None = None
        self.message = ""

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def snapshot(self) -> dict[str, Any]:
        return {"status": self.status, "qr": self.qr, "generation": self.generation, "otp": self.otp, "message": self.message}

    def start(self, username: str) -> None:
        if self.running:
            return
        now = time.monotonic()
        self._attempts = [t for t in self._attempts if now - t < self.WINDOW_SECONDS]
        if len(self._attempts) >= self.MAX_ATTEMPTS:
            raise RuntimeError("For mange login-forsøg. Vent et kvarter, så MitID ikke låser kontoen.")
        self._attempts.append(now)
        self.reset()
        self.status = "starting"
        self._task = asyncio.create_task(self._run(username))

    def _on_qr(self, qr1, qr2) -> None:
        pair = [qr_svg(qr1), qr_svg(qr2)]
        if pair != self.qr:
            self.qr = pair
            self.generation += 1
        self.status = "scan"

    def _on_qr_done(self) -> None:
        self.qr = []
        self.status = "approve"

    def _on_otp(self, code: str) -> None:
        self.otp = code
        self.status = "scan"

    async def _run(self, username: str) -> None:
        from aula import FileTokenStorage
        from aula.auth_flow import authenticate, create_client

        auth = self._authenticate or authenticate

        async def first_identity(identities: list[str]) -> int:
            return 0

        try:
            storage = FileTokenStorage(credentials.aula_token_file())
            token_data = await asyncio.wait_for(
                auth(
                    username,
                    storage,
                    on_qr_codes=self._on_qr,
                    on_qr_done=self._on_qr_done,
                    on_otp_code=self._on_otp,
                    on_identity_selected=first_identity,
                    force_login=True,
                ),
                timeout=self.TIMEOUT_SECONDS,
            )
            if self._authenticate is None:  # real login: prove the session works
                async with await create_client(token_data, token_storage=storage) as client:
                    profile = await client.get_profile()
                self.message = f"Logget ind som {profile.display_name}."
            else:
                self.message = "Logget ind."
            credentials.remember_aula_username(username)
            self.status = "done"
        except TimeoutError:
            self.status, self.message = "error", "Login tog for lang tid og blev afbrudt."
        except Exception as exc:
            self.status, self.message = "error", f"Login fejlede: {exc}"
        finally:
            self.qr = []
        outcome = Outcome.OK if self.status == "done" else Outcome.ERROR
        await asyncio.to_thread(runs.record, "aula-login", outcome, self.message)  # the lock may be held by a sync


# ----------------------------------------------------------------------------- HTML

CSS = """
:root{--fg:#1d1d1f;--mut:#6e6e73;--line:#e5e5ea;--ok:#1a7f37;--bad:#c62828;--acc:#0b57d0;--bg:#fff;--soft:#f5f5f7}
@media (prefers-color-scheme:dark){:root{--fg:#f2f2f7;--mut:#a1a1a6;--line:#38383a;--ok:#3fb950;--bad:#ff6b6b;--acc:#8ab4f8;--bg:#111;--soft:#1c1c1e}body{background:#111}}
*{box-sizing:border-box}body{font:16px/1.5 system-ui,sans-serif;color:var(--fg);max-width:46rem;margin:0 auto;padding:1.25rem}
h1{font-size:1.4rem;margin:.2rem 0 1.2rem}h2{font-size:1.05rem;margin:2rem 0 .6rem}
a{color:var(--acc)}.mut{color:var(--mut);font-size:.9rem}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(10rem,1fr));gap:.6rem}
.card{border:1px solid var(--line);border-radius:10px;padding:.7rem .8rem}.card b{display:block;font-size:.8rem;color:var(--mut);font-weight:500}
.ok{color:var(--ok)}.bad{color:var(--bad)}
ul.list{list-style:none;padding:0;margin:0}ul.list li{border-top:1px solid var(--line);padding:.55rem 0;display:flex;gap:.8rem;align-items:baseline}
ul.list li .grow{flex:1;min-width:0}.date{white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--mut);min-width:6.5rem}
button,.btn{font:inherit;border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:8px;padding:.35rem .8rem;cursor:pointer;text-decoration:none;display:inline-block}
button.primary,.btn.primary{background:var(--acc);border-color:var(--acc);color:#fff}
button.small{font-size:.85rem;padding:.15rem .6rem}
input[type=text],input[type=search],select{font:inherit;padding:.35rem .5rem;border:1px solid var(--line);border-radius:8px;background:transparent;color:var(--fg)}
.qr{width:min(20rem,80vw);aspect-ratio:1;margin:1rem 0}.qr svg{width:100%;height:100%}
.otp{font-size:2rem;letter-spacing:.2em;font-weight:600}
.flash{padding:.6rem .8rem;border-radius:8px;border:1px solid var(--line);margin-bottom:1rem}
form.inline{display:inline;margin:0}
details>summary{cursor:pointer;margin:2rem 0 .6rem;font-weight:600}
.acts{display:flex;flex-direction:column;gap:.3rem;align-items:flex-end}
form.search{display:flex;flex-wrap:wrap;gap:.4rem;margin:0 0 1rem}form.search input[type=search]{flex:1 1 100%;font-size:1.05rem;padding:.55rem .7rem}
li.past{opacity:.6}.tag{font-size:.8rem;border:1px solid var(--line);border-radius:6px;padding:0 .35rem;color:var(--mut)}
mark{background:#fde68a;color:inherit;border-radius:2px}@media (prefers-color-scheme:dark){mark{background:#7a5c00}}
.text{white-space:pre-wrap;overflow-wrap:anywhere}
h3{font-size:1rem;margin:1.6rem 0 .5rem}details.version>summary{margin:.4rem 0;font-weight:400}
ul.thread li.sel{border-left:3px solid var(--acc);padding-left:.6rem}
.tabs{display:flex;gap:1.4rem;margin:-.6rem 0 1rem}.tabs>*{display:inline-block;padding:.45rem 0}.tabs b{border-bottom:2px solid var(--acc)}
.sticky{position:sticky;top:0;z-index:5;background:var(--bg);padding:.6rem 0 .5rem;border-bottom:1px solid var(--line);margin-bottom:.5rem}
.sticky form.search{margin:0}
.chips{display:flex;flex-wrap:wrap;gap:.4rem;margin:.5rem 0 0}
.chip{display:inline-flex;align-items:center;min-height:2.5rem;padding:0 1rem;border:1px solid var(--line);border-radius:999px;color:var(--fg);text-decoration:none}
.chip.on{background:var(--acc);border-color:var(--acc);color:#fff}
ul.list li.row{padding:.75rem 0}.row .grow>.item-link{font-weight:500;display:inline-block;padding:.1rem 0}
.sum{display:block;font-size:.92rem}.meta{display:block}
.cat-arrangement{background:#e6f4ea;color:#0d652d;border-color:#e6f4ea}.cat-frist{background:#fce8e6;color:#a50e0e;border-color:#fce8e6}
.cat-periode{background:#fef7e0;color:#8a5a00;border-color:#fef7e0}.cat-info{background:#e8f0fe;color:#174ea6;border-color:#e8f0fe}
@media (prefers-color-scheme:dark){.cat-arrangement,.cat-frist,.cat-periode,.cat-info{background:transparent}}
ul.now{border:1px solid var(--line);border-radius:12px;padding:0 .9rem;background:var(--soft)}ul.now li:first-child{border-top:0}
.week{margin-top:1.2rem}.week h3{display:flex;justify-content:space-between;gap:.5rem;border-bottom:2px solid var(--line);padding-bottom:.2rem;margin:0}
.week.cur h3{border-color:var(--acc)}.ev{font-weight:500}
.day{margin-top:1rem}.day h3{margin:0 0 .1rem}.day.today h3{color:var(--acc)}
.week-line{margin:1.4rem 0 0;padding-bottom:.15rem;border-bottom:1px solid var(--line);font-size:.8rem;color:var(--mut)}.topic h3{margin:1rem 0 .2rem}
#ikke-inddelt{margin-top:2.5rem}
.thumbs{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:.6rem}.thumb{width:7rem;height:7rem;object-fit:cover;border-radius:8px;border:1px solid var(--line)}
form.search select{flex:1 1 7rem;min-width:0;min-height:2.5rem}form.search button{min-height:2.5rem}
details.filtre{flex:1 1 auto}details.filtre>summary{margin:0;display:inline-flex;align-items:center;min-height:2.5rem;font-weight:400;color:var(--mut)}
details.filtre>summary{cursor:pointer;border:1px solid var(--line);border-radius:999px;padding:0 .9rem;color:inherit}details.filtre>summary::before{content:"▸";margin-right:.4rem}details.filtre[open]>summary::before{content:"▾"}
.filtre-felter{display:flex;flex-wrap:wrap;gap:.4rem}
@supports selector(::details-content){@media (min-width:40rem){details.filtre>summary{display:none}
details.filtre::details-content{content-visibility:visible;block-size:auto}}}
li.valgt{background:var(--soft);box-shadow:-.6rem 0 0 var(--soft),.6rem 0 0 var(--soft)}
#skuffe{position:fixed;top:0;right:0;bottom:0;width:min(42rem,48vw);z-index:20;background:var(--bg);
border-left:1px solid var(--line);box-shadow:-8px 0 24px rgba(0,0,0,.12);display:flex;flex-direction:column}
#skuffe[hidden]{display:none}
.skuffe-top{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:.6rem 1.25rem;border-bottom:1px solid var(--line)}
.skuffe-indhold{flex:1;overflow:auto;padding:0 1.25rem 2rem;outline:0}.skuffe-indhold h2{margin-top:1.2rem}
html.skuffe-aaben{padding-right:min(42rem,48vw)}
@media (max-width:40rem){body{padding:.9rem}h1{margin-bottom:.8rem}
button.small{min-height:2.5rem;padding:.3rem .9rem}ul.list li{flex-wrap:wrap;gap:.3rem .8rem}
ul.list li .grow{flex:1 1 12rem}.date{min-width:0}.acts{flex-direction:row}
.row .grow>.item-link{padding:.35rem 0}.tabs{margin-top:-.3rem}.tabs>*{padding:.6rem 0}}
"""


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


# The overview's pages run no script of their own: only the site's file, under this policy (Aula's text cannot run).
OVERVIEW_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
OVERVIEW_SCRIPT = '<script src="/static/overblik.js" defer></script>'
THEME_COLOR = "#2156c9"  # the browser's bar on a phone and the background of static/icon.svg


def page(
    title: str | None, body: str, flash: str | None = None, *, status_code: int = 200, enhanced: bool = False,
) -> HTMLResponse:
    """A page under the app's name (``web.app_name``), which is also its title when ``title`` is None; ``enhanced``
    (the overview and the detail): with :data:`OVERVIEW_SCRIPT` under :data:`OVERVIEW_CSP`."""
    app_name = web_config()["app_name"]
    flash_html = f'<div class="flash">{e(flash)}</div>' if flash else ""
    headers = {"Cache-Control": "no-store"}
    if enhanced:
        headers["Content-Security-Policy"] = OVERVIEW_CSP
    return HTMLResponse(
        status_code=status_code,
        content=
        f"""<!doctype html><html lang="da"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title or app_name)}</title>
<meta name="robots" content="noindex"><link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/static/icon.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/static/icon-192.png">
<meta name="theme-color" content="{THEME_COLOR}"><style>{CSS}</style>{OVERVIEW_SCRIPT if enhanced else ""}</head><body>
<h1><a href="/" style="color:inherit;text-decoration:none">{e(app_name)}</a></h1>{flash_html}{body}</body></html>""",
        headers=headers,
    )


def overview_main(content: str) -> str:
    """What the overview's script swaps: everything under the tabs and the search field."""
    return f'<main id="overblik-main">{content}</main>'


TABS = {
    "forside": ("/", "Forside"), "info": ("/info", "Info"), "past": ("/past", "Tidligere"),
    "settings": ("/settings", "Indstillinger"),
}


def tabs(active: str | None) -> str:
    """The tabs shared by the pages (``active``: the page it is on; None: a link to each)."""
    links = "".join(
        f'<b aria-current="page">{e(label)}</b>' if name == active else f'<a href="{href}">{e(label)}</a>'
        for name, (href, label) in TABS.items()
    )
    return f'<nav class="tabs" id="faner">{links}</nav>'


def fmt_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        if "T" in value:
            d = dt.datetime.fromisoformat(value)
            return d.strftime("%d.%m.%Y %H:%M")
        return dt.date.fromisoformat(value).strftime("%d.%m.%Y")
    except ValueError:
        return value


def fmt_when(value: str | None) -> str:
    if not value:
        return "aldrig"
    try:
        return dt.datetime.fromisoformat(value).strftime("%d.%m. kl. %H:%M")
    except ValueError:
        return value


BACKUP_STALE_AFTER = dt.timedelta(days=2)


def backup_ok(status: archive.Status) -> bool:
    if not status.last_backup:
        return False
    return dt.datetime.now().astimezone() - dt.datetime.fromisoformat(status.last_backup) <= BACKUP_STALE_AFTER


def archive_text(status: archive.Status) -> str:
    saved = "intet gemt endnu"
    if status.last_saved:
        messages = f"{status.messages} {'besked' if status.messages == 1 else 'beskeder'}"
        saved = f"{status.posts} opslag · {messages} · sidst gemt {fmt_when(status.last_saved)}"
    if not status.last_backup:
        return f"{saved} · ingen backup endnu"
    stale = "" if backup_ok(status) else " – over 2 døgn gammel"
    return f"{saved} · backup {fmt_when(status.last_backup)}{stale}"


# ----------------------------------------------------------------------------- routes


async def home(request: Request) -> Response:
    cfg, ledger = store.load_config(), Ledger.load()
    aula, google = credentials.aula_status(ledger), credentials.google_status()
    last_runs = ledger.runs(8)
    last_run = ledger.last_run("fetch", "apply")
    archive_status = archive.status()

    def card(label: str, ok: bool | None, text: str, action: str = "") -> str:
        cls = "" if ok is None else ("ok" if ok else "bad")
        return f'<div class="card"><b>{e(label)}</b><span class="{cls}">{e(text)}</span>{action}</div>'

    cards = "".join(
        [
            card("Aula", aula.ok, aula.text, "" if aula.ok else ' · <a href="/aula/login">Log ind</a>'),
            card("Google", google.ok, google.text, "" if google.ok else ' · <a href="/google">Forbind</a>'),
            card(
                "Sidste kørsel",
                None if not last_run else last_run["ok"],
                fmt_when(last_run["at"]) if last_run else "aldrig",
            ),
            card("Venter", None, f"{len(ledger.pending_keys())} beskeder/opslag"),
            card("Arkiv", None if backup_ok(archive_status) else False, archive_text(archive_status)),
        ]
    )

    def first_name(child_id: int) -> str:
        return child_first_name(cfg["children"].get(str(child_id)))

    events = ledger.upcoming()
    sensitive = sensitive_items({ev.source for ev in events if ev.source})
    if events:
        items = []
        for ev in events:
            src_txt = ev.source_item.get("title") or ev.source_item.get("subject") or ""
            if ev.source in sensitive:
                src_txt = SENSITIVE
            link = f' <a href="{e(ev.link)}" target="_blank" rel="noopener">åbn</a>' if ev.link else ""
            names = ", ".join(first_name(c) for c in ev.child_ids)
            several = len(ev.child_ids) > 1
            buttons = "".join(
                f'<form class="inline" method="post" action="/events/remove" '
                f'data-title="{e(ev.title)}" data-who="{e(first_name(c))}" '
                f'onsubmit="return confirm(\'Fjern »\'+this.dataset.title+\'« fra kalenderen for \'+this.dataset.who+\'? '
                f'Den bliver ikke oprettet igen.\')">'
                f'<input type="hidden" name="key" value="{e(ev.key)}"><input type="hidden" name="child_id" value="{c}">'
                f'<button class="small">{"Fjern for " + e(first_name(c)) if several else "Fjern"}</button></form>'
                for c in ev.child_ids
            )
            src = e(src_txt or "Aula")
            if ev.source:
                src = f'<a href="{e(store.item_path(ev.source))}">{src}</a>'
            items.append(
                f'<li><span class="date">{e(fmt_date(ev.start))}</span><span class="grow">{e(ev.title)}{link}'
                f'<br><span class="mut">{e(names)}{" · fra " + src if src_txt or ev.source else ""}</span></span>'
                f'<span class="acts">{buttons}</span></li>'
            )
        events_html = f'<ul class="list">{"".join(items)}</ul>'
    else:
        events_html = '<p class="mut">Ingen kommende begivenheder endnu.</p>'

    children_html = "".join(
        f'<li><span class="grow">{e(c.get("name"))} <span class="mut">· {e(c.get("institution"))}</span></span>'
        f'<span class="mut">{e(c.get("calendar_name") or "kalender oprettes ved første begivenhed")}</span></li>'
        for c in cfg["children"].values()
    ) or '<li class="mut">Ingen endnu – vises efter første hentning.</li>'

    runs_html = "".join(
        f'<li><span class="date">{e(fmt_when(r["at"]))}</span><span class="grow">{e(r["command"])}: '
        f'<span class="{"ok" if r["ok"] else "bad"}">{e(r["summary"])}</span></span></li>'
        for r in last_runs
    ) or '<li class="mut">Ingen kørsler endnu.</li>'

    body = f"""{tabs("settings")}<div class="cards">{cards}</div>
<h2>Kommende begivenheder</h2>{events_html}
<h2>Børn og kalendere</h2><ul class="list">{children_html}</ul>
<h2>Seneste kørsler</h2><ul class="list">{runs_html}</ul>
<p class="mut" style="margin-top:2rem">Logget ind som {e(getattr(request.state, "email", ""))}</p>"""
    return page(None, body, request.query_params.get("msg"))


SENSITIVE = "🔒 Følsom tråd"  # what a sensitive thread is called on a page that may stand open on a screen


def child_first_name(child: dict[str, Any] | None) -> str:
    """A child's first name, as the pages show it ("?" when the config does not know the child or its name)."""
    return (((child or {}).get("name") or "").split() or ["?"])[0]


def kind_name(item: archive.Item) -> str:
    """What an item is, as the pages say it."""
    return "opslag" if item.kind == "post" else "besked"


def sensitive_items(keys: set[str]) -> set[str]:
    """Which of these items are sensitive threads, by the archive (none, when it is missing or not migrated yet)."""
    with archive.read() as a:
        if a is None:
            return set()
        return {k for k in keys if (item := a.item(k)) is not None and item.sensitive}


CATEGORY_NAMES = {
    archive.Category.ARRANGEMENT: "Arrangement",
    archive.Category.FRIST: "Frist",
    archive.Category.PERIODE: "Periode",
    archive.Category.INFO: "Stående info",
    archive.Category.ORIENTERING: "Orientering",
    archive.Category.SVAR: "Svar",
}


def todo_lists(todos: list[archive.ItemTodo], ledger: Ledger) -> tuple[list[archive.ItemTodo], list[archive.ItemTodo]]:
    """The open todos, by due (passed ones come first, none last), and the ticked off ones, last ticked first.

    A todo that was not current when it was classified counts as ticked off until the user undoes that.
    """
    open_, checked = [], []
    for t in todos:
        is_checked = ledger.todo_checked(t.item.key, t.version.id, by_default=not t.was_current)
        (checked if is_checked else open_).append(t)
    open_.sort(key=lambda t: (t.due is None, t.due or dt.date.max, t.item.key))
    checked.sort(key=lambda t: ledger.todo_checked_at(t.item.key, t.version.id) or "", reverse=True)
    return open_, checked


# The buttons on a todo, by the action they post to (``/todos/<action>``): its label and what it does in Ledger.
TODO_ACTIONS: dict[str, tuple[str, Callable[[Ledger, str, int], None]]] = {
    "check": ("Klaret", Ledger.check_todo),
    "uncheck": ("Fortryd", Ledger.uncheck_todo),
}


def todo_button(t: archive.ItemTodo, *, checked: bool, back: str | None = None) -> str:
    """ "Klaret" or "Fortryd"; ``back``: the page to return to (:func:`safe_back`; else the overview)."""
    action = "uncheck" if checked else "check"
    back_field = f'<input type="hidden" name="back" value="{e(back)}">' if back else ""
    return (
        f'<form class="inline" method="post" action="/todos/{action}">'
        f'<input type="hidden" name="item" value="{e(t.item.key)}">'
        f'<input type="hidden" name="version" value="{t.version.id}">{back_field}'
        f'<button class="small">{TODO_ACTIONS[action][0]}</button></form>'
    )


def todo_item(
    t: archive.ItemTodo, *, today: dt.date, checked: bool, day: dt.date | None = None, back: str | None = None
) -> str:
    button = todo_button(t, checked=checked, back=back)
    sensitive = t.item.sensitive  # the page may stand open on a screen: not what to do, nor the subject
    overdue = not checked and t.due is not None and t.due < today
    meta = [m for m in (
        t.due and f"senest {fmt_date(t.due.isoformat())}",
        None if sensitive else t.item.latest.title or "(uden titel)",
    ) if m]
    flag = ' <b class="bad">overskredet</b>' if overdue else ""
    text = SENSITIVE if sensitive else t.text
    what = f'<span class="todo">{e(text)}</span>{flag}<br><span class="mut">{e(" · ".join(meta))}</span>'
    return f'<li class="row todo-row">{day_cell(day)}<span class="grow">{what}</span><span class="acts">{button}</span></li>'


def todos_html(
    open_: list[archive.ItemTodo], checked: list[archive.ItemTodo], ledger: Ledger, today: dt.date, back: str
) -> str:
    """Skal gøres and the folded Klaret (:func:`todo_lists`); ``back``: the overview as it is filtered,
    for the buttons to return to."""
    open_html = "".join(todo_item(t, today=today, checked=False, back=back) for t in open_)
    checked_html = "".join(todo_item(t, today=today, checked=True, back=back) for t in checked)
    open_html = open_html or '<li class="mut">Intet at gøre.</li>'
    checked_html = checked_html or '<li class="mut">Intet klaret endnu.</li>'
    return (
        f'<h2>Skal gøres <span class="mut">({len(open_)})</span></h2><ul class="list">{open_html}</ul>'
        f'<details><summary>Klaret <span class="mut">({len(checked)})</span></summary>'
        f'<ul class="list">{checked_html}</ul></details>'
    )


def item_row(item: archive.Item, extra: str = "", *, day: dt.date | None = None, acts: str = "") -> str:
    """One item on the timeline: its category, title and summary, who sent it and when, linked to its detail page;
    ``acts``: buttons at its side.

    A sensitive thread shows only its date, source and category (the page may stand open on a screen).
    """
    c, latest = item.classification, item.latest
    who = None if item.sensitive else latest.author
    meta = " · ".join(m for m in (kind_name(item), who, fmt_date((latest.date or "")[:10])) if m)
    summary = "" if item.sensitive or not c.summary else f'<span class="sum">{e(c.summary)}</span>'
    return (
        f'<li class="row" data-key="{e(item.key)}">{day_cell(day)}<span class="grow">'
        f'<span class="tag cat-{c.category}">{e(CATEGORY_NAMES[c.category])}</span> '
        f'<a class="item-link" href="{e(store.item_path(item.key))}">{e(item_title(item))}</a>'
        f'{summary}<span class="mut meta">{e(meta)}</span>{extra}</span>'
        f'{f"<span class=\"acts\">{acts}</span>" if acts else ""}</li>'
    )


WEEKDAYS = ("man", "tir", "ons", "tor", "fre", "lør", "søn")


def fmt_day(day: dt.date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day:%d.%m}"


def day_cell(day: dt.date | None) -> str:
    return f'<span class="date">{e(fmt_day(day))}</span>' if day else ""


def day_param(value: str | None) -> dt.date | None:
    """A date from the URL (``YYYY-MM-DD``), or None."""
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def event_row(line: timeline.EventLine, *, dated: bool = True) -> str:
    """An event in the calendar, with a link to the item it came from; ``dated``: with the day it begins (in the
    weeks), else on a day of its own (under its heading) with its time or "hele dagen" instead."""
    ev, source, start = line.event, line.source, line.start
    end = dt.date.fromisoformat((ev.end or ev.start)[:10])
    time = f"kl. {ev.start[11:16]}" if len(ev.start) > 10 else ""
    when = [time] if time and dated else []
    if not dated and start < line.day:
        when.append(f"fra {fmt_day(start)}")
    if end != start:
        when.append(f"til {fmt_day(end)}")
    src = ""
    if ev.source and source is not None:
        src = f' · fra <a class="item-link event-src" href="{e(store.item_path(ev.source))}">{e(item_title(source))}</a>'
    cell = day_cell(start) if dated else f'<span class="date time">{e(time or "hele dagen")}</span>'
    return (
        f'<li class="row event" data-event="{e(ev.key)}">{cell}<span class="grow">'
        f'<span class="ev">📅 {e(ev.title)}</span>{" " + e(" ".join(when)) if when else ""}'
        f'<br><span class="mut">i kalenderen{src}</span></span></li>'
    )


def week_html(first: dt.date, rows: list[str], today: dt.date) -> str:
    """One week (Monday ``first``) with its lines."""
    this = first == timeline.monday(today)
    label = f"Uge {first.isocalendar().week}" + (" · denne uge" if this else "")
    return (
        f'<div class="week{" cur" if this else ""}" data-week="{first.isoformat()}"><h3><span>{e(label)}</span>'
        f' <span class="mut">{first:%d.%m}–{first + dt.timedelta(days=6):%d.%m}</span></h3>'
        f'<ul class="list">{"".join(rows)}</ul></div>'
    )


def line_html(line: timeline.Line, today: dt.date, back: str, *, dated: bool = True) -> str:
    """One line on the timeline; ``dated``: with its day (in the weeks), else under its day's heading."""
    day = line.day if dated else None
    match line:
        case timeline.ItemLine(todo=None):
            return item_row(line.item, day=day)
        case timeline.ItemLine(todo=t):
            what = "" if t.item.sensitive else f'<span class="meta">✓ <span class="todo">{e(t.text)}</span></span>'
            return item_row(line.item, what, day=day, acts=todo_button(t, checked=False, back=back))
        case timeline.EventLine():
            return event_row(line, dated=dated)
        case timeline.TodoLine():
            return todo_item(line.todo, today=today, checked=False, day=day, back=back)


def weeks_html(weeks: list[timeline.Week], today: dt.date, back: str) -> str:
    return "".join(week_html(w.monday, [line_html(line, today, back) for line in w.lines], today) for w in weeks)


WEEKDAY_NAMES = ("Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Lørdag", "Søndag")


def day_html(d: timeline.Day, today: dt.date, back: str) -> str:
    """One day with its lines under a heading ("Mandag 9/11"; "I dag · fredag 6/11"), or "Intet i dag/i morgen"."""
    date = f"{WEEKDAY_NAMES[d.day.weekday()]} {d.day.day}/{d.day.month}"
    near = {0: "i dag", 1: "i morgen"}.get((d.day - today).days)
    heading = f"{near.capitalize()} · {date.lower()}" if near else date
    rows = "".join(line_html(line, today, back, dated=False) for line in d.lines) or f'<li class="mut">Intet {near}.</li>'
    return (
        f'<div class="day{" today" if d.day == today else ""}" data-day="{d.day.isoformat()}"><h3>{e(heading)}</h3>'
        f'<ul class="list">{rows}</ul></div>'
    )


def near_html(coming: timeline.Upcoming, today: dt.date, back: str) -> str:
    """I dag og i morgen (:func:`timeline.upcoming`), also when there is nothing on them."""
    days = "".join(day_html(d, today, back) for d in coming.near)
    return f'<section id="i-dag-og-i-morgen"><h2>I dag og i morgen</h2>{days}</section>'


def upcoming_html(coming: timeline.Upcoming, today: dt.date, back: str) -> str:
    """Kommende (:func:`timeline.upcoming`): the days with something on them, and a line when the week changes
    (from the week of tomorrow, the last day above)."""
    parts, week = [], timeline.monday(coming.near[-1].day)
    for d in coming.later:
        if (first := timeline.monday(d.day)) != week:
            parts.append(f'<p class="week-line" data-week="{first.isoformat()}">Uge {first.isocalendar().week}</p>')
            week = first
        parts.append(day_html(d, today, back))
    return f'<section id="kommende"><h2>Kommende</h2>{"".join(parts) or "<p class=\"mut\">Intet kommende.</p>"}</section>'


def earlier_html(items: list[archive.Item], before: dt.date, today: dt.date, params) -> str:
    """Tidligere (:func:`timeline.earlier`), shown as usual (past or not), and "Vis ældre" when there is more
    (filtered as the overview is, by ``params``)."""
    got = timeline.earlier(items, before, today)
    weeks = weeks_html(got.weeks, today, overview_url(params, page="/past"))
    older = ""
    if got.older:
        href = overview_url(params, page="/past", drop=("before",), add=[("before", got.older.isoformat())])
        older = f'<p><a class="btn older" href="{e(href)}">Vis ældre</a></p>'
    return f'<section id="tidligere"><h2>Tidligere</h2>{weeks or "<p class=\"mut\">Intet.</p>"}{older}</section>'


NO_TOPIC = "Uden emne"  # also where a sensitive thread stands: its topic is not shown


def standing_html(items: list[archive.Item]) -> str:
    """Stående info, grouped by each item's first topic (the topics by name), newest first under each."""
    groups: dict[str, list[archive.Item]] = {}
    for item in items:
        topics = item.classification.topics
        groups.setdefault(topics[0] if topics and not item.sensitive else NO_TOPIC, []).append(item)
    parts = []
    for topic in sorted(groups, key=lambda t: (t == NO_TOPIC, t)):
        members = sorted(groups[topic], key=lambda i: (i.latest.date or "", i.key), reverse=True)
        parts.append(f'<div class="topic"><h3>{e(topic)}</h3><ul class="list">{"".join(map(item_row, members))}</ul></div>')
    return f'<section id="staaende-info"><h2>Stående info</h2>{"".join(parts) or "<p class=\"mut\">Ingen endnu.</p>"}</section>'


def why_current(item: archive.Item, why: archive.Current) -> str:
    """What "Gælder nu" says under an item about why it is there ("" for nothing)."""
    match why:
        case archive.Current.SPAN:
            return f"gælder {item.classification.span_text('%d.%m.%Y')}"
        case archive.Current.IMPORTANT:
            return "⭐ markeret vigtig i Aula"
    return ""


KIND_FILTER_NAMES = {
    archive.KindFilter.POST: "Opslag",
    archive.KindFilter.MESSAGE: "Besked",
    archive.KindFilter.DOCUMENT: "Med dokument",
}


def excerpt_html(parts: tuple[tuple[str, bool], ...]) -> str:
    return "".join(f"<mark>{e(text)}</mark>" if marked else e(text) for text, marked in parts)


def search_hit(hit: archive.Hit, today: dt.date) -> str:
    item, c, latest = hit.item, hit.item.classification, hit.item.latest
    past = item.past(today)
    meta = [m for m in (kind_name(item), fmt_date((latest.date or "")[:10])) if m]
    if c:
        meta.append(CATEGORY_NAMES[c.category])
        if span := c.span_text("%d.%m.%Y"):
            meta.append(f"gælder {span}")
    lines = []
    title = item_title(item)
    if not item.sensitive:  # found, but no subject, excerpt, topics or file names (the page may stand open on a screen)
        if c and c.topics:
            meta.append(f"emner: {', '.join(c.topics)}")
        if hit.excerpt:
            lines.append(f'<span class="excerpt">{excerpt_html(hit.excerpt)}</span>')
        if hit.files:
            lines.append(f'<span class="mut">i fil: {e(", ".join(f.name for f in hit.files))}</span>')
    flag = ' <span class="tag">overstået</span>' if past else ""
    lines.append(f'<span class="mut">{e(" · ".join(meta))}</span>')
    return (
        f'<li{' class="past"' if past else ""}><span class="grow"><a href="{e(store.item_path(item.key))}">{e(title)}</a>{flag}'
        f'{"".join("<br>" + line for line in lines)}</span></li>'
    )


def child_param(params) -> int | None:
    """The child the overview is filtered on (``?child=``), or None for every child."""
    raw = params.get("child") or ""
    return int(raw) if raw.isdigit() else None


CHILD_COOKIE = "child"  # the child chosen in the child filter, remembered on the device for pages without ?child=
ALL_CHILDREN = "all"  # ?child= of "Alle børn": every child, and the device forgets the chosen one
CHILD_COOKIE_AGE = 365 * 24 * 3600  # a year


def known_child(raw: str | None) -> bool:
    """Whether ``raw`` is a child in the config (one no longer there is ignored)."""
    return bool(raw) and raw.isdigit() and raw in store.load_config()["children"]


def view_params(request: Request) -> QueryParams:
    """What the overview's page is asked for: the URL, with the child remembered on the device (:data:`CHILD_COOKIE`)
    when the URL has none (``child`` in the URL wins)."""
    params = request.query_params
    remembered = request.cookies.get(CHILD_COOKIE)
    if "child" in params or not known_child(remembered):
        return params
    return QueryParams([*params.multi_items(), ("child", remembered)])


def remember_child(request: Request, response: Response) -> Response:
    """Remember the child chosen in the URL on the device for a year, forget it for "Alle børn" (or when it is no
    longer in the config). ``Secure`` when the site is on https, as it is behind Access (the demo is not)."""
    chosen, remembered = request.query_params.get("child"), request.cookies.get(CHILD_COOKIE)
    secure = web_config()["public_url"].startswith("https://")
    if known_child(chosen):
        response.set_cookie(
            CHILD_COOKIE, chosen, max_age=CHILD_COOKIE_AGE, httponly=True, samesite="lax", secure=secure
        )
    elif remembered is not None and (chosen == ALL_CHILDREN or not known_child(remembered)):
        response.delete_cookie(CHILD_COOKIE, httponly=True, samesite="lax", secure=secure)
    return response


def for_child(child_ids: list[int], child: int | None) -> bool:
    """Whether something for these children is shown for ``child``; for no child in particular is for every child."""
    return child is None or not child_ids or child in child_ids


def overview_url(
    params, *, page: str = "/", drop: tuple[str, ...] = (), add: list[tuple[str, str]] = ()
) -> str:
    """The overview's ``page`` as ``params`` filter it, without the message (``msg``) and the ``drop`` ones, with
    ``add`` (where :func:`list_path` has it)."""
    keep = [(k, v) for k, v in params.multi_items() if k not in ("msg", *drop) and v] + list(add)
    return list_path(dict(keep), page) + (f"?{urlencode(keep)}" if keep else "")


def child_filter(params, children: dict[str, Any], page: str = "/") -> str:
    """The child filter: one link per child and one for every child, keeping the search and the rest of the view
    on ``page`` (only with more than one child)."""
    if len(children) < 2:
        return ""
    chosen = child_param(params)

    def chip(cid: int | None, name: str) -> str:
        on = " on" if cid == chosen else ""
        current = ' aria-current="true"' if cid == chosen else ""
        href = overview_url(params, page=page, drop=("child",), add=[("child", str(cid) if cid is not None else ALL_CHILDREN)])
        return f'<a class="chip{on}" href="{e(href)}"{current}>{e(name)}</a>'

    chips = [chip(None, "Alle børn")] + [chip(int(cid), child_first_name(c)) for cid, c in children.items() if cid.isdigit()]
    return f'<nav id="barn-filter" class="chips" aria-label="Barn">{"".join(chips)}</nav>'


def search_form(params, topics: list[archive.Topic], page: str = "/") -> str:
    """The search field with its filters folded under "Filtre" (open when one is chosen), a GET form on /search,
    and the child filter (for ``page``) under it, sticky at the top. Its button is only there without script (Enter
    sends it)."""
    def select(name: str, label: str, options: list[tuple[str, str]]) -> str:
        chosen = params.get(name) or ""
        opts = "".join(
            f'<option value="{e(value)}"{" selected" if value == chosen else ""}>{e(text)}</option>'
            for value, text in [("", label), *options]
        )
        return f'<select name="{name}" aria-label="{e(label)}">{opts}</select>'

    children = store.load_config()["children"]
    child = child_param(params)
    hidden = f'<input type="hidden" name="child" value="{child}">' if child is not None and len(children) > 1 else ""
    selects = [
        select("category", "Alle slags", [(str(c), name) for c, name in CATEGORY_NAMES.items()]),
        select("topic", "Alle emner", [(t.name, t.name) for t in topics]),
        select("kind", "Alle kilder", [(str(k), name) for k, name in KIND_FILTER_NAMES.items()]),
    ]
    chosen = sum(1 for name in ("category", "topic", "kind") if params.get(name))
    filters = (
        f'<details class="filtre"{" open" if chosen else ""}><summary>Filtre{f" ({chosen})" if chosen else ""}</summary>'
        f'<div class="filtre-felter">{"".join(selects)}</div></details>'
    )
    return (
        f'<div id="overblik-top" class="sticky"><form id="search" class="search" method="get" action="/search">'
        f'<input type="search" id="q" name="q" value="{e(params.get("q"))}"'
        f' placeholder="Søg i alt fra Aula" aria-label="Søg">{hidden}{filters}'
        f'<noscript><button>Søg</button></noscript></form>{child_filter(params, children, page)}</div>'
    )


def searching(params) -> bool:
    """Whether the overview is asked for search results (a query, or a filter other than the child)."""
    return any((params.get(name) or "").strip() for name in ("q", "category", "topic", "kind"))


def list_path(params, page: str = "/") -> str:
    """Where the overview's ``page`` as ``params`` ask for it is: the search results on /search, an earlier page
    (``before``) on /past, the rest on ``page`` (the front page for /search without anything to search for)."""
    if searching(params):
        return "/search"
    if page in ("/", "/search") and params.get("before"):
        return "/past"
    return "/" if page == "/search" else page


class SearchPart(NamedTuple):
    """The top of the overview, and the search results when it is asked for them."""

    form: str  # the search field with its filters and the child filter (:func:`search_form`)
    results: str | None  # the search results; None when it is not asked for them (:func:`searching`)


def search_part(params, today: dt.date, page: str = "/") -> SearchPart | None:
    """The search field for the top of the overview's ``page`` and the search results; None without an archive ready
    for it."""
    def choice(name, kind):
        try:
            return kind(params.get(name)) if params.get(name) else None
        except ValueError:
            return None

    with archive.read() as a:
        if a is None:
            return None
        form = search_form(params, a.topics(), page)
        if not searching(params):
            return SearchPart(form, None)
        hits = a.search(
            params.get("q") or "", child=child_param(params), category=choice("category", archive.Category),
            topic=params.get("topic") or None, kind=choice("kind", archive.KindFilter),
        )
    found = "".join(search_hit(h, today) for h in hits) or '<li class="mut">Intet fundet.</li>'
    results = f'<section id="soegning"><h2>Søgning <span class="mut">({len(hits)})</span></h2><ul class="list">{found}</ul></section>'
    return SearchPart(form, results)


OVERVIEW_PAGES = {"/": "forside", "/info": "info", "/past": "past", "/search": "forside"}  # path -> its tab


async def overview(request: Request) -> Response:
    """The overview's pages, each under the tabs and the search field: the front page (Skal gøres, I dag og i
    morgen, Gælder nu, Kommende day by day), Stående info on /info, Tidligere on /past, and the search results on
    /search (each sends on to the one :func:`list_path` has for what it is asked for), for the child in the URL or
    else the one remembered on the device (:func:`view_params`, :func:`remember_child`)."""
    return remember_child(request, await overview_page(request, view_params(request)))


async def overview_page(request: Request, params: QueryParams) -> Response:
    path = request.url.path
    if path != list_path(params, path):
        return RedirectResponse(overview_url(params, page=path), status_code=302)
    ledger = Ledger.load()
    pending = ledger.pending_keys()
    today = request.app.state.today()

    child = child_param(params)
    events = [ev for ev in ledger.upcoming(today.isoformat()) if child is None or child in ev.child_ids]

    def read() -> tuple[list[archive.Item], int, list[archive.ItemTodo], dict[str, archive.Item]] | None:
        with archive.read() as a:
            if a is None:
                return None
            sources = {k: item for k in {ev.source for ev in events if ev.source} if (item := a.item(k))}
            return a.items(), len(a.unclassified(exclude=pending)), a.todos(), sources

    nav = tabs(OVERVIEW_PAGES[path])
    search = await asyncio.to_thread(search_part, params, today, path)
    if search and search.results is not None:
        return page("Søgning", nav + search.form + overview_main(search.results), enhanced=True)
    got = await asyncio.to_thread(read)
    if got is None or search is None:
        body = overview_main('<p class="mut">Arkivet er ikke klar til overblikket endnu. Det bliver det ved næste hentning fra Aula.</p>')
        return page(None, nav + body, enhanced=True)
    items, unclassified, todos, sources = got
    todos = [t for t in todos if for_child(t.item.child_ids, child)]
    shown = [
        i for i in items
        if i.classification and i.classification.category != archive.Category.SVAR and for_child(i.child_ids, child)
    ]
    msg = params.get("msg")
    if path == "/info":
        info = standing_html([i for i in shown if i.classification.category == archive.Category.INFO])
        return page("Info", nav + search.form + overview_main(info), msg, enhanced=True)
    if path == "/past":
        earlier = earlier_html(shown, day_param(params.get("before")) or today, today, params)
        return page("Tidligere", nav + search.form + overview_main(earlier), msg, enhanced=True)
    back = overview_url(params)  # where "Klaret" returns to: this view
    open_, checked = todo_lists(todos, ledger)
    coming = timeline.upcoming(shown, events, open_, today, sources)
    now_html = "".join(
        item_row(i, f'<span class="mut meta">{e(why)}</span>' if (why := why_current(i, reason)) else "")
        for i, reason in timeline.now(shown, today)
    )
    body = f"""
<section id="skal-goeres">{todos_html(open_, checked, ledger, today, back)}</section>
{near_html(coming, today, back)}
<section id="gaelder-nu"><h2>Gælder nu</h2><ul class="list now">{now_html or '<li class="mut">Intet lige nu.</li>'}</ul></section>
{upcoming_html(coming, today, back)}
<p class="mut" id="ikke-inddelt">{unclassified} endnu ikke inddelt</p>"""
    return page(None, nav + search.form + overview_main(body), msg, enhanced=True)


async def tick_todo(request: Request) -> Response:
    """A button in :data:`TODO_ACTIONS` ("Klaret", "Fortryd") on a todo of one item version."""
    if (known := TODO_ACTIONS.get(request.path_params["action"])) is None:
        return PlainTextResponse("Findes ikke.", status_code=404)
    act = known[1]
    form = await request.form()
    item = str(form.get("item") or "")
    raw_version = str(form.get("version") or "")
    version = int(raw_version) if raw_version.isdigit() else None
    back = safe_back(str(form.get("back") or ""))

    def work() -> str | None:
        with archive.read() as a:
            if a is None or not any(t.item.key == item and t.version.id == version for t in a.todos()):
                return "Opgaven findes ikke længere."
        with store.lock(wait=False):
            ledger = Ledger.load()
            act(ledger, item, version)
            ledger.save()
        return None

    try:
        msg = await asyncio.to_thread(work)
    except store.Busy as exc:
        msg = f"{exc} Prøv igen om lidt."
    return RedirectResponse(back + (f"{'&' if '?' in back else '?'}msg={quote(msg)}" if msg else ""), status_code=303)


# An item key in a path back to its detail, as store.item_path writes it unquoted (an id needing %-quoting falls
# back to the overview): never "." or "..", nor anything encoded.
ITEM_KEY = re.compile(r"(?!\.+$)[A-Za-z0-9_.~:-]+")


LIST_PATHS = ("/", "/info", "/past", "/search")  # the overview's pages a button may return to (besides an item's detail)


def safe_back(back: str) -> str:
    """Where a button may return to: one of :data:`LIST_PATHS` or an item's detail, as a path on this site (no
    scheme, no host, nothing unprintable anywhere), with any query; anything else is the front page."""
    parts = urlsplit(back)
    key = parts.path.removeprefix("/item/")
    on_site = (
        back.isprintable() and not parts.scheme and not parts.netloc and not parts.fragment
        and (parts.path in LIST_PATHS or (key != parts.path and ITEM_KEY.fullmatch(key) is not None))
    )
    return back if on_site else "/"


# ----------------------------------------------------------------------------- the detail of one item


def child_names(child_ids: list[int], cfg: dict[str, Any]) -> str:
    """The children's first names; no children means every child (Aula set none, nor did the agent)."""
    if not child_ids:
        return "alle børn"
    return ", ".join(child_first_name(cfg["children"].get(str(c))) for c in child_ids)


def item_text(body: str) -> str:
    return f'<div class="text">{e(body)}</div>'


def file_url(item_key: str, attachment_id: str) -> str:
    """Where the file route hands out one of an item's files: by item key and attachment id, never a path."""
    return f"{store.item_path(item_key)}/file/{quote(attachment_id, safe=':')}"


# What the browser may show itself, by the file name's suffix (never by the kind Aula gave it): an SVG or HTML
# file shown inline would run script on this site's own address. Everything else is downloaded.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
INLINE_TYPES = {".pdf": "application/pdf", **IMAGE_TYPES}


def suffix(name: str) -> str:
    return PurePosixPath(name.lower()).suffix


def files_html(item: archive.Item) -> str:
    """The item's files: images as thumbnails, PDFs to open, other files to download, the rest as what they are."""
    if not item.files:
        return ""
    thumbs, rows = [], []
    for f in item.files:
        url = file_url(item.key, f.attachment_id)
        if f.kind == archive.Kind.IMAGE and f.path and suffix(f.name) in IMAGE_TYPES:
            thumbs.append(
                f'<a href="{e(url)}" target="_blank" rel="noopener">'
                f'<img src="{e(url)}" alt="{e(f.name)}" loading="lazy" class="thumb"></a>'
            )
            continue
        if f.kind == archive.Kind.SECURE_DOCUMENT:
            what = f'{e(f.name)} <span class="mut">· sikkert dokument, kan kun ses i Aula</span>'
        elif f.kind == archive.Kind.LINK:
            ok = (f.detail or "").startswith(("https://", "http://"))
            what = (f'<a href="{e(f.detail)}" target="_blank" rel="noopener">{e(f.name)}</a>' if ok else e(f.name))
            what += ' <span class="mut">· link</span>'
        elif not f.path:
            what = f'{e(f.name)} <span class="mut">· ikke hentet</span>'
        elif suffix(f.name) in INLINE_TYPES:
            what = f'<a href="{e(url)}" target="_blank" rel="noopener">{e(f.name)}</a>'
        else:
            what = f'<a href="{e(url)}" download>{e(f.name)}</a>'
        rows.append(f'<li><span class="grow">{what}</span></li>')
    gallery = f'<div class="thumbs">{"".join(thumbs)}</div>' if thumbs else ""
    return f'<h3>Filer</h3>{gallery}<ul class="list">{"".join(rows)}</ul>'


def versions_html(item: archive.Item) -> str:
    """The earlier versions of an item edited in Aula, newest first, each folded with its date and whole text."""
    if len(item.versions) < 2:
        return ""
    earlier = "".join(
        f'<details class="version"><summary>{e(fmt_date(item.came_out(v)) or "ukendt dato")}'
        f'{" · " + e(v.title) if v.title != item.latest.title else ""}</summary>{item_text(v.body)}</details>'
        for v in reversed(item.versions[:-1])
    )
    return f'<h3>Rettet i Aula · {len(item.versions)} versioner</h3><p class="mut">Tidligere versioner:</p>{earlier}'


HISTORY_ACTIONS = {"created": "oprettet", "updated": "rettet", "cancelled": "aflyst"}


def item_title(item: archive.Item | None) -> str:
    """What an item is called in a list: its title, but never a sensitive thread's subject."""
    if item is None:
        return "et item, der ikke er i arkivet"
    return SENSITIVE if item.sensitive else item.latest.title or "(uden titel)"


@dataclass
class Detail:
    """What the detail page shows about one item, read from the archive and Ledger."""

    item: archive.Item
    events: list[EventHistory]  # what it created, updated or cancelled in the calendar
    others: dict[str, archive.Item | None]  # the other items in those events' history
    ledger: Ledger
    cfg: dict[str, Any]
    today: dt.date
    thread: list[archive.Item]  # a message's whole thread, oldest first (itself too); none for a post

    @property
    def this(self) -> str:
        return "dette opslag" if self.item.kind == "post" else "denne besked"


def detail_html(d: Detail, *, hide: bool) -> str:
    """The item; ``hide`` (a sensitive thread before "Vis teksten"): only its date, source, category and span,
    and its todo's status without its text."""
    item, latest, c = d.item, d.item.latest, d.item.classification
    meta = [
        kind_name(item),
        None if hide else latest.author,
        fmt_date(latest.date),
        child_names(item.child_ids, d.cfg),
    ]
    rows = [f'<p class="mut">{e(" · ".join(m for m in meta if m))}</p>']
    if c:
        what = [CATEGORY_NAMES[c.category]]
        if span := c.span_text("%d.%m.%Y"):
            what.append(f"gælder {span}")
        if c.topics and not hide:
            what.append(f"emner: {', '.join(c.topics)}")
        rows.append(f'<p>{e(" · ".join(what))}</p>')
        if c.summary and not hide:
            rows.append(f"<p><b>{e(c.summary)}</b></p>")
    else:
        rows.append('<p class="mut">Endnu ikke inddelt.</p>')
    rows.append(todo_detail(d, hide=hide))
    if hide:
        rows.append('<p><a href="?show=1" class="btn">Vis teksten</a></p>')
        return f"<h2>{e(item_title(item))}</h2>{''.join(rows)}"
    rows += [item_text(latest.body), files_html(item), versions_html(item), calendar_html(d), thread_html(d)]
    return f"<h2>{e(latest.title or '(uden titel)')}</h2>{''.join(rows)}"


def todo_detail(d: Detail, *, hide: bool) -> str:
    """The item's todo with its status (open, overdue, ticked off and when) and its button."""
    c = d.item.classification
    if c is None or c.todo is None:
        return ""
    t = archive.ItemTodo(d.item, d.item.classified_version)
    checked = d.ledger.todo_checked(t.item.key, t.version.id, by_default=not t.was_current)
    if checked:
        at = d.ledger.todo_checked_at(t.item.key, t.version.id)
        status = '<span class="ok">klaret</span>' + (f" {e(fmt_when(at))}" if at else "")
    elif t.due is not None and t.due < d.today:
        status = '<b class="bad">overskredet</b>'
    else:
        status = "åben"
    due = f" · senest {e(fmt_date(t.due.isoformat()))}" if t.due else ""
    text = "Skal gøres" if hide else f'<span class="todo">{e(t.text)}</span>'
    return (
        f'<ul class="list"><li><span class="grow">✓ {text}<br><span class="mut">{status}{due}</span></span>'
        f'<span class="acts">{todo_button(t, checked=checked, back=store.item_path(t.item.key))}</span></li></ul>'
    )


def calendar_html(d: Detail) -> str:
    """ "Blev til i kalenderen": each event the item changed, with its whole history, whom the user removed it for,
    and a button for each child who still has it coming up."""
    if not d.events:
        return '<h3>Blev til i kalenderen</h3><p class="mut">Intet i kalenderen.</p>'
    upcoming = {u.key: u.child_ids for u in d.ledger.upcoming(d.today.isoformat())}  # who can still have it removed
    rows = []
    for h in d.events:
        when = fmt_date(h.start) + (f"–{fmt_date(h.end)}" if h.end and h.end != h.start else "")
        flag = ' <b class="bad">aflyst</b>' if h.cancelled else ""
        lines = []
        for line in h.lines:
            if line.item == d.item.key:
                who = d.this
            else:
                other = d.others.get(line.item)
                who = f'<a href="{e(store.item_path(line.item))}">{e(item_title(other))}</a>' if other else e(item_title(None))
            lines.append(f"{HISTORY_ACTIONS[line.action]} af {who}" + (f" {e(fmt_when(line.at))}" if line.at else ""))
        removed = [c for c in map(int, d.cfg["children"]) if d.ledger.is_removed(h.key, c)]
        if removed:
            lines.append(f"fjernet af dig for {e(child_names(removed, d.cfg))}")
        buttons = "".join(
            remove_button(h.key, h.title, c, d.cfg, back=store.item_path(d.item.key)) for c in upcoming.get(h.key, [])
        )
        rows.append(
            f'<li><span class="date">{e(when)}</span><span class="grow">{e(h.title)}{flag}'
            f'<br><span class="mut">{e(child_names(h.child_ids, d.cfg))}</span>'
            f'<br><span class="mut">{"<br>".join(lines)}</span></span><span class="acts">{buttons}</span></li>'
        )
    return f'<h3>Blev til i kalenderen</h3><ul class="list">{"".join(rows)}</ul>'


def remove_button(key: str, title: str, child_id: int, cfg: dict[str, Any], *, back: str) -> str:
    """ "Fjern for <fornavn>" ("Fjern" with one child): to the confirmation (:func:`remove_confirm_page`), which
    the overview's script asks in a dialog instead (``data-confirm``) and then posts the form itself."""
    name = child_first_name(cfg["children"].get(str(child_id)))
    label = f"Fjern for {name}" if len(cfg["children"]) > 1 else "Fjern"
    question = f"Fjern »{title}« fra kalenderen for {name}? Den bliver ikke oprettet igen."
    return (
        f'<form class="inline" method="get" action="/events/remove" data-confirm="{e(question)}">'
        f'<input type="hidden" name="key" value="{e(key)}"><input type="hidden" name="child_id" value="{child_id}">'
        f'<input type="hidden" name="back" value="{e(back)}"><button class="small">{e(label)}</button></form>'
    )


def thread_html(d: Detail) -> str:
    """A message's whole thread, replies too, with this message marked."""
    if len(d.thread) < 2:
        return ""
    rows = []
    for m in d.thread:
        v = m.latest
        sel = m.key == d.item.key
        who = " · ".join(x for x in (v.author, fmt_date(v.date)) if x)
        head = e(who) if sel else f'<a href="{e(store.item_path(m.key))}">{e(who or "besked")}</a>'
        rows.append(
            f'<li{' class="sel" aria-current="true"' if sel else ""}><span class="grow">'
            f'<span class="mut">{head}</span>{item_text(v.body)}</span></li>'
        )
    return f'<h3>Tråden · {len(d.thread)} beskeder</h3><ul class="list thread">{"".join(rows)}</ul>'


async def item_detail(request: Request) -> Response:
    """One item with everything the archive and Ledger know about it (``?show=1``: a sensitive thread's text too)."""
    key = request.path_params["key"]
    cfg, ledger = store.load_config(), Ledger.load()
    events = ledger.events_by_item(key)
    today = request.app.state.today()

    def read() -> Detail | None:
        with archive.read() as a:
            item = a.item(key) if a is not None else None
            if item is None:
                return None
            others = {line.item: a.item(line.item) for h in events for line in h.lines if line.item and line.item != key}
            thread = [i for i in a.items() if item.thread_id and i.thread_id == item.thread_id]
        thread.sort(key=lambda i: (i.latest.date or "", i.key))
        return Detail(item, events, others, ledger, cfg, today, thread)

    d = await asyncio.to_thread(read)
    nav = tabs(None)
    if d is None:
        body = overview_main('<p class="mut">Det findes ikke i arkivet.</p>')
        return page("Findes ikke", nav + body, status_code=404, enhanced=True)
    hide = d.item.sensitive and request.query_params.get("show") != "1"
    title = None if hide else d.item.latest.title or None
    return page(title, nav + overview_main(detail_html(d, hide=hide)), request.query_params.get("msg"), enhanced=True)


STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {  # what /static/ hands out, by name; nothing else
    "overblik.js": "text/javascript; charset=utf-8",
    # The app's icon. The PNGs are made from the SVG: uvx cairosvg icon.svg -W 192 -H 192 -o icon-192.png (and 512)
    "icon.svg": "image/svg+xml", "icon-192.png": "image/png", "icon-512.png": "image/png",
}


async def static_file(request: Request) -> Response:
    """One of :data:`STATIC_FILES` (looked up by name, never a path from the URL)."""
    name = request.path_params["name"]
    if name not in STATIC_FILES:
        return PlainTextResponse("Findes ikke.", status_code=404)
    return FileResponse(str(STATIC_DIR / name), media_type=STATIC_FILES[name], headers={"Cache-Control": "no-cache"})


async def manifest(request: Request) -> Response:
    """What a phone needs to put the page on the home screen as an app (no service worker: it is online only)."""
    name = web_config()["app_name"]
    icons = [
        {"src": "/static/icon.svg", "type": "image/svg+xml", "sizes": "any"},
        {"src": "/static/icon-192.png", "type": "image/png", "sizes": "192x192"},
        {"src": "/static/icon-512.png", "type": "image/png", "sizes": "512x512"},
    ]
    body = {
        "name": name, "short_name": name, "lang": "da", "start_url": "/", "scope": "/", "display": "standalone",
        "theme_color": THEME_COLOR, "background_color": "#ffffff", "icons": icons,
    }
    return JSONResponse(body, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


async def item_file(request: Request) -> Response:
    """One file the archive registered on the item, looked up by item key and attachment id.

    Nothing from the URL is used as a path: an unknown id, another item's file, a secure
    document, a link or a file that was never downloaded is not found.
    """
    key, attachment = request.path_params["key"], request.path_params["attachment"]

    def find() -> archive.File | None:
        with archive.read() as a:
            item = a.item(key) if a is not None else None
        return next((f for f in item.files if f.attachment_id == attachment), None) if item else None

    file = await asyncio.to_thread(find)
    if file is None or not file.downloadable or not file.path:
        return PlainTextResponse("Findes ikke.", status_code=404)
    data_dir = store.DATA_DIR.resolve()
    path = (data_dir / file.path).resolve()
    if not path.is_relative_to(data_dir) or not path.is_file():
        return PlainTextResponse("Findes ikke.", status_code=404)
    media_type = INLINE_TYPES.get(suffix(file.name))
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if media_type != "application/pdf":  # Chrome's PDF viewer refuses to open under a CSP sandbox
        headers["Content-Security-Policy"] = "sandbox"
    return FileResponse(
        path, media_type=media_type or "application/octet-stream", filename=file.name,
        content_disposition_type="inline" if media_type else "attachment", headers=headers,
    )


def remove_fields(fields) -> tuple[str, int | None, str | None]:
    """What a "Fjern" button sends: the event's key, the child (None: from a page before removal was per child:
    every child) and the page to return to (None: the settings)."""
    raw_child = str(fields.get("child_id") or "")
    back = str(fields.get("back") or "")
    return str(fields.get("key") or ""), int(raw_child) if raw_child.isdigit() else None, safe_back(back) if back else None


async def remove_confirm_page(request: Request) -> Response:
    """The confirmation before a "Fjern" without the overview's script: what is removed for whom, and the button."""
    key, child_id, back = remove_fields(request.query_params)
    cfg, ledger = store.load_config(), Ledger.load()
    entries = [x for x in ledger.entries(key) if child_id is None or child_id in x.child_ids]
    back_url = back or "/settings"
    if not entries:
        body = f'<p>Begivenheden findes ikke længere.</p><p><a href="{e(back_url)}" class="btn">Tilbage</a></p>'
        return page("Fjern fra kalenderen", tabs(None) + overview_main(body), enhanced=True)
    first = entries[0]
    children = [child_id] if child_id is not None else sorted({c for x in entries for c in x.child_ids})
    when = fmt_date(first.start) + (f"–{fmt_date(first.end)}" if first.end and first.end != first.start else "")
    fields = f'<input type="hidden" name="key" value="{e(key)}">'
    if child_id is not None:
        fields += f'<input type="hidden" name="child_id" value="{child_id}">'
    if back:
        fields += f'<input type="hidden" name="back" value="{e(back)}">'
    body = (
        f"<h2>Fjern fra kalenderen</h2><p>Fjern »{e(first.title)}« ({e(when)}) fra kalenderen for "
        f"{e(child_names(children, cfg))}? Den bliver ikke oprettet igen.</p>"
        f'<form method="post" action="/events/remove">{fields}<button class="primary">Fjern</button> '
        f'<a href="{e(back_url)}" class="btn">Fortryd</a></form>'
    )
    return page("Fjern fra kalenderen", tabs(None) + overview_main(body), enhanced=True)


async def remove_event(request: Request) -> Response:
    """Remove an event for a child; back to the page the button was on (``back``), else the settings."""
    from .credentials import GoogleLoginRequired
    from .gcal import remove_event as do_remove

    key, child_id, back = remove_fields(await request.form())

    def work() -> int:
        with store.lock(wait=False):
            return do_remove(key, child_id, google=request.app.state.google)

    try:
        n = await asyncio.to_thread(work)
        msg = "Begivenheden er fjernet og bliver ikke lagt i kalenderen igen." if n else "Intet at fjerne."
    except store.Busy as exc:
        msg = f"{exc} Prøv igen om lidt."
    except KeyError:
        msg = "Begivenheden findes ikke længere."
    except GoogleLoginRequired:
        msg = "Google er ikke forbundet."
    if back is None:
        return go_home(msg)
    return RedirectResponse(f"{back}{'&' if '?' in back else '?'}msg={quote(msg)}", status_code=303)


LOGIN_JS = """
<script>
let gen=-1, frames=[], idx=0, timer=null;
function show(){const el=document.getElementById('qr'); if(!frames.length){el.innerHTML='';return}
  el.innerHTML=frames[idx%frames.length]; document.getElementById('part').textContent=(idx%frames.length+1)+' af 2';}
function cycle(){clearTimeout(timer); timer=setTimeout(()=>{idx++;show();cycle()}, idx===0?2000:1000)}
async function poll(){
  const r=await fetch('/aula/login/state',{cache:'no-store'}); const s=await r.json();
  const st=document.getElementById('status');
  if(s.generation!==gen){gen=s.generation; frames=s.qr; idx=0; show(); if(frames.length) cycle();}
  if(!s.qr.length){frames=[]; show(); document.getElementById('part').textContent='';}
  document.getElementById('otp').textContent=s.otp?('Eller indtast koden i MitID-appen: '+s.otp):'';
  const texts={starting:'Starter MitID…',scan:'Scan koden med MitID-appen. Hold telefonen stille – koden består af to halvdele, der skifter.',
    approve:'Koden er læst. Godkend login i MitID-appen.',done:s.message,error:s.message};
  st.textContent=texts[s.status]||''; st.className=s.status==='error'?'bad':(s.status==='done'?'ok':'');
  if(s.status==='done'){setTimeout(()=>location.href='/settings?msg='+encodeURIComponent(s.message),1500);return}
  if(s.status!=='error' && s.status!=='idle') setTimeout(poll,1000);
}
poll();
</script>"""


async def aula_login_page(request: Request) -> Response:
    from aula.config import load_config as load_aula_config

    login: MitIDLogin = request.app.state.mitid
    if login.running or (login.status in ("done", "error") and request.query_params.get("watch")):
        body = """<p id="status"></p><div class="qr" id="qr"></div><p class="mut" id="part"></p>
<p class="otp" id="otp"></p><p><a href="/settings">Tilbage</a></p>""" + LOGIN_JS
        return page("MitID-login", body)
    username = load_aula_config().get("mitid_username") or ""
    body = f"""<p>Log ind i Aula med MitID-appen. Åbn siden på en computer eller tablet,
så du kan scanne QR-koden med telefonen.</p>
<form method="post" action="/aula/login"><p><label>MitID-brugernavn<br>
<input type="text" name="username" value="{e(username)}" required autocomplete="username"></label></p>
<p><button class="primary">Start MitID-login</button></p></form>
<p class="mut">For at beskytte MitID-kontoen kan du højst starte {MitIDLogin.MAX_ATTEMPTS} logins pr. kvarter.</p>"""
    return page("MitID-login", body)


async def aula_login_start(request: Request) -> Response:
    username = str((await request.form()).get("username") or "").strip()
    if not username:
        return RedirectResponse("/aula/login", status_code=303)
    try:
        request.app.state.mitid.start(username)
    except RuntimeError as exc:
        return page("MitID-login", f'<p class="bad">{e(exc)}</p><p><a href="/settings">Tilbage</a></p>')
    return RedirectResponse("/aula/login?watch=1", status_code=303)


async def aula_login_state(request: Request) -> Response:
    return JSONResponse(request.app.state.mitid.snapshot(), headers={"Cache-Control": "no-store"})


def go_home(msg: str) -> RedirectResponse:
    """Back to the settings (the logins, the events) with ``msg``."""
    return RedirectResponse(f"/settings?msg={quote(msg)}", status_code=303)


def moved(request: Request, path: str) -> RedirectResponse:
    """An old address (before #50) sent on for good to ``path``, with its query."""
    query = request.url.query
    return RedirectResponse(path + (f"?{query}" if query else ""), status_code=301)


async def old_overview(request: Request) -> Response:
    return moved(request, list_path(request.query_params))


async def old_item(request: Request) -> Response:
    return moved(request, store.item_path(request.path_params["key"]))


async def old_file(request: Request) -> Response:
    return moved(request, file_url(request.path_params["key"], request.path_params["attachment"]))


def redirect_uri() -> str:
    return f"{web_config()['public_url']}/google/callback"


async def google_page(request: Request) -> Response:
    status = credentials.google_status()
    ok, txt = status.ok, status.text
    kind = credentials.client_secret_kind()
    upload = f"""<form method="post" action="/google/client-secret" enctype="multipart/form-data">
<p>Upload OAuth-nøglen (JSON) fra Google Cloud Console:<br><input type="file" name="file" accept=".json,application/json" required></p>
<p><button>Upload</button></p></form>
<p class="mut">Klienttypen skal være <b>Web application</b> med denne redirect-URI:<br><code>{e(redirect_uri())}</code></p>"""
    if kind is None:
        body = f"<p>Status: <b class='bad'>{e(txt)}</b></p>{upload}"
    elif kind != "web":
        body = f"<p class='bad'>Den uploadede nøgle er af typen »{e(kind)}«, men web-siden kræver »Web application«.</p>{upload}"
    else:
        body = f"""<p>Status: <b class="{'ok' if ok else 'bad'}">{e(txt)}</b></p>
<p><a class="btn primary" href="/google/connect">{'Forbind igen' if ok else 'Forbind Google'}</a></p>
<p class="mut">aula-agent får kun adgang til de kalendere og filer, den selv opretter.</p>
<details><summary class="mut">Udskift OAuth-nøgle</summary>{upload}</details>"""
    return page("Google", body + '<p><a href="/settings">Tilbage</a></p>')


async def google_client_secret(request: Request) -> Response:
    form = await request.form()
    upload = form.get("file")
    raw = await upload.read() if upload else b""
    try:
        credentials.save_client_secret(raw)
    except ValueError:
        return page("Google", '<p class="bad">Det ligner ikke en OAuth-nøgle fra Google.</p><p><a href="/google">Tilbage</a></p>')
    return RedirectResponse("/google", status_code=303)


async def google_connect(request: Request) -> Response:
    from .credentials import web_flow_start

    url, state, verifier = await asyncio.to_thread(web_flow_start, redirect_uri())
    request.app.state.oauth_pending[state] = (verifier, time.monotonic())
    return RedirectResponse(url, status_code=303)


async def google_callback(request: Request) -> Response:
    from .credentials import web_flow_finish

    state = request.query_params.get("state") or ""
    code = request.query_params.get("code")
    pending = request.app.state.oauth_pending.pop(state, None)
    if request.query_params.get("error"):
        return go_home(f"Google-adgang blev ikke givet ({request.query_params['error']}).")
    if not code or not pending or time.monotonic() - pending[1] > 600:
        return go_home("Google-login udløb. Prøv igen.")
    try:
        await asyncio.to_thread(web_flow_finish, redirect_uri(), state, pending[0], code)
    except Exception as exc:
        return go_home(f"Google-login fejlede: {exc}")
    response = go_home("Google er forbundet.")
    # Logged after the response is sent: a running sync may hold the lock for a while.
    response.background = BackgroundTask(runs.record, "google-login", Outcome.OK, "Google forbundet")
    return response


def create_app(
    *, verifier_factory=default_verifier_factory, mitid: MitIDLogin | None = None, google=None,
    today: Callable[[], dt.date] = dt.date.today,
) -> Starlette:
    """``google`` makes the Calendar/Drive adapter (default: connect with the saved token);
    ``today`` says which day it is (when a todo is overdue)."""
    from . import gcal

    app = Starlette(
        routes=[
            Route("/", overview),
            Route("/search", overview),
            Route("/info", overview),
            Route("/past", overview),
            Route("/settings", home),
            Route("/item/{key}", item_detail),
            Route("/item/{key}/file/{attachment:path}", item_file),
            # Calendar events from before #50 link to /overblik/item/…, and their descriptions stay: keep these.
            Route("/overblik", old_overview),
            Route("/overblik/item/{key}", old_item),
            Route("/overblik/item/{key}/fil/{attachment:path}", old_file),
            Route("/static/{name}", static_file),
            Route("/manifest.webmanifest", manifest),
            Route("/events/remove", remove_confirm_page),
            Route("/events/remove", remove_event, methods=["POST"]),
            Route("/todos/{action}", tick_todo, methods=["POST"]),
            Route("/aula/login", aula_login_page),
            Route("/aula/login", aula_login_start, methods=["POST"]),
            Route("/aula/login/state", aula_login_state),
            Route("/google", google_page),
            Route("/google/client-secret", google_client_secret, methods=["POST"]),
            Route("/google/connect", google_connect),
            Route("/google/callback", google_callback),
        ],
        middleware=[Middleware(AccessMiddleware, verifier_factory=verifier_factory)],
    )
    app.state.mitid = mitid or MitIDLogin()
    app.state.oauth_pending = {}
    app.state.google = google or gcal.Google.connect
    app.state.today = today
    return app
