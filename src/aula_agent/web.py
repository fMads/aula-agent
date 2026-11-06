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
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from . import archive, credentials, runs, store
from .ledger import EventHistory, Ledger
from .runs import Outcome

# ----------------------------------------------------------------------------- config


def web_config() -> dict[str, Any]:
    web = store.load_config().get("web") or {}
    return {
        "public_url": (web.get("public_url") or "").rstrip("/"),  # e.g. "https://aula.example.dk"
        "allowed_emails": [e.lower() for e in web.get("allowed_emails") or []],
        "access_team_domain": web.get("access_team_domain"),  # e.g. "myteam.cloudflareaccess.com"
        "access_aud": web.get("access_aud"),  # Application Audience (AUD) tag
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
:root{--fg:#1d1d1f;--mut:#6e6e73;--line:#e5e5ea;--ok:#1a7f37;--bad:#c62828;--acc:#0b57d0}
@media (prefers-color-scheme:dark){:root{--fg:#f2f2f7;--mut:#a1a1a6;--line:#38383a;--ok:#3fb950;--bad:#ff6b6b;--acc:#8ab4f8}body{background:#111}}
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
.thumbs{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:.6rem}.thumb{width:7rem;height:7rem;object-fit:cover;border-radius:8px;border:1px solid var(--line)}
"""


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def page(title: str, body: str, flash: str | None = None, *, status_code: int = 200) -> HTMLResponse:
    flash_html = f'<div class="flash">{e(flash)}</div>' if flash else ""
    return HTMLResponse(
        status_code=status_code,
        content=
        f"""<!doctype html><html lang="da"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<meta name="robots" content="noindex"><style>{CSS}</style></head><body>
<h1><a href="/" style="color:inherit;text-decoration:none">Aula → Kalender</a></h1>{flash_html}{body}</body></html>""",
        headers={"Cache-Control": "no-store"},
    )


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
        return ((cfg["children"].get(str(child_id)) or {}).get("name") or "?").split()[0]

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

    body = f"""<p><b>Status</b> · <a href="/overblik">Overblik</a></p><div class="cards">{cards}</div>
<h2>Kommende begivenheder</h2>{events_html}
<h2>Børn og kalendere</h2><ul class="list">{children_html}</ul>
<h2>Seneste kørsler</h2><ul class="list">{runs_html}</ul>
<p class="mut" style="margin-top:2rem">Logget ind som {e(getattr(request.state, "email", ""))}</p>"""
    return page("Aula → Kalender", body, request.query_params.get("msg"))


SENSITIVE = "🔒 Følsom tråd"  # what a sensitive thread is called on a page that may stand open on a screen


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
    """ "Klaret" or "Fortryd"; ``back``: the detail page to return to (else the overview)."""
    action = "uncheck" if checked else "check"
    back_field = f'<input type="hidden" name="back" value="{e(back)}">' if back else ""
    return (
        f'<form class="inline" method="post" action="/todos/{action}">'
        f'<input type="hidden" name="item" value="{e(t.item.key)}">'
        f'<input type="hidden" name="version" value="{t.version.id}">{back_field}'
        f'<button class="small">{TODO_ACTIONS[action][0]}</button></form>'
    )


def todo_item(t: archive.ItemTodo, *, today: dt.date, checked: bool) -> str:
    button = todo_button(t, checked=checked)
    sensitive = t.item.sensitive  # the page may stand open on a screen: not what to do, nor the subject
    overdue = not checked and t.due is not None and t.due < today
    meta = [m for m in (
        t.due and f"senest {fmt_date(t.due.isoformat())}",
        None if sensitive else t.item.latest.title or "(uden titel)",
    ) if m]
    flag = ' <b class="bad">overskredet</b>' if overdue else ""
    text = SENSITIVE if sensitive else t.text
    what = f'<span class="todo">{e(text)}</span>{flag}<br><span class="mut">{e(" · ".join(meta))}</span>'
    return f'<li><span class="grow">{what}</span><span class="acts">{button}</span></li>'


def todos_html(todos: list[archive.ItemTodo], ledger: Ledger, today: dt.date) -> str:
    open_, checked = todo_lists(todos, ledger)
    open_html = "".join(todo_item(t, today=today, checked=False) for t in open_) or '<li class="mut">Intet at gøre.</li>'
    checked_html = "".join(todo_item(t, today=today, checked=True) for t in checked) or '<li class="mut">Intet klaret endnu.</li>'
    return (
        f'<h2>Skal gøres <span class="mut">({len(open_)})</span></h2><ul class="list">{open_html}</ul>'
        f'<details><summary>Klaret <span class="mut">({len(checked)})</span></summary>'
        f'<ul class="list">{checked_html}</ul></details>'
    )


def overview_item(item: archive.Item) -> str:
    c, latest = item.classification, item.latest
    meta = [m for m in ("opslag" if item.kind == "post" else "besked", fmt_date((latest.date or "")[:10])) if m]
    if span := c.span_text("%d.%m.%Y"):
        meta.append(f"gælder {span}")
    if item.sensitive:  # the page may stand open on a screen: no subject, summary or topics
        title, summary = SENSITIVE, ""
    else:
        title, summary = latest.title or "(uden titel)", c.summary or ""
        if c.topics:
            meta.append(f"emner: {', '.join(c.topics)}")
    return (
        f'<li><span class="grow"><a href="{e(store.item_path(item.key))}">{e(title)}</a>'
        f'{"<br>" + e(summary) if summary else ""}'
        f'<br><span class="mut">{e(" · ".join(meta))}</span></span></li>'
    )


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
    meta = [m for m in ("opslag" if item.kind == "post" else "besked", fmt_date((latest.date or "")[:10])) if m]
    if c:
        meta.append(CATEGORY_NAMES[c.category])
        if span := c.span_text("%d.%m.%Y"):
            meta.append(f"gælder {span}")
    lines = []
    if item.sensitive:  # found, but no subject, excerpt, topics or file names (the page may stand open on a screen)
        title = "🔒 Følsom tråd"
    else:
        title = latest.title or "(uden titel)"
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


def search_form(params, topics: list[archive.Topic]) -> str:
    """The search field with its filters, a GET form on /overblik (the child filter only with more than one child)."""
    def select(name: str, label: str, options: list[tuple[str, str]]) -> str:
        chosen = params.get(name) or ""
        opts = "".join(
            f'<option value="{e(value)}"{" selected" if value == chosen else ""}>{e(text)}</option>'
            for value, text in [("", label), *options]
        )
        return f'<select name="{name}" aria-label="{e(label)}">{opts}</select>'

    children = store.load_config()["children"]
    filters = [
        select("child", "Alle børn", [(cid, (c.get("name") or "?").split()[0]) for cid, c in children.items()])
        if len(children) > 1 else "",
        select("category", "Alle slags", [(str(c), name) for c, name in CATEGORY_NAMES.items()]),
        select("topic", "Alle emner", [(t.name, t.name) for t in topics]),
        select("kind", "Alle kilder", [(str(k), name) for k, name in KIND_FILTER_NAMES.items()]),
    ]
    return (
        f'<form class="search" method="get" action="/overblik"><input type="search" name="q" value="{e(params.get("q"))}"'
        f' placeholder="Søg i alt fra Aula" aria-label="Søg">{"".join(filters)}<button>Søg</button></form>'
    )


def searching(params) -> bool:
    """Whether the overview is asked for search results (a query, or a filter other than the child)."""
    return any((params.get(name) or "").strip() for name in ("q", "category", "topic", "kind"))


def search_part(params, today: dt.date) -> tuple[str, bool] | None:
    """The search field for the top of the overview, with the results when it is asked for them
    (:func:`searching`), and whether it is; None without an archive ready for it."""
    def choice(name, kind):
        try:
            return kind(params.get(name)) if params.get(name) else None
        except ValueError:
            return None

    with archive.read() as a:
        if a is None:
            return None
        form = search_form(params, a.topics())
        if not searching(params):
            return form, False
        hits = a.search(
            params.get("q") or "", child=choice("child", int), category=choice("category", archive.Category),
            topic=params.get("topic") or None, kind=choice("kind", archive.KindFilter),
        )
    found = "".join(search_hit(h, today) for h in hits) or '<li class="mut">Intet fundet.</li>'
    return form + f'<h2>Søgning <span class="mut">({len(hits)})</span></h2><ul class="list">{found}</ul>', True


async def overview(request: Request) -> Response:
    """The raw list of the agent's classifications (trin 1 of the overview), to judge them by, under the search."""
    ledger = Ledger.load()
    pending = ledger.pending_keys()

    def read() -> tuple[list[archive.Item], list[archive.Topic], int, list[archive.ItemTodo]] | None:
        with archive.read() as a:
            if a is None:
                return None
            return a.items(), a.topics(), len(a.unclassified(exclude=pending)), a.todos()

    nav = '<p><a href="/">Status</a> · <b>Overblik</b></p>'
    search = await asyncio.to_thread(search_part, request.query_params, request.app.state.today())
    if search and search[1]:
        return page("Søgning", nav + search[0])
    got = await asyncio.to_thread(read)
    if got is None or search is None:
        body = '<p class="mut">Arkivet er ikke klar til overblikket endnu. Det bliver det ved næste hentning fra Aula.</p>'
        return page("Overblik", nav + body)
    nav += search[0]
    items, topics, unclassified, todos = got
    groups: dict[archive.Category, list[archive.Item]] = {c: [] for c in archive.Category}
    for item in items:
        if item.classification:
            groups[item.classification.category].append(item)
    sections = []
    for category, members in groups.items():
        if not members:
            continue
        members.sort(key=lambda i: i.latest.date or "", reverse=True)
        sections.append(
            f'<h2>{e(CATEGORY_NAMES[category])} <span class="mut">({len(members)})</span></h2>'
            f'<ul class="list">{"".join(overview_item(i) for i in members)}</ul>'
        )
    topics_html = "".join(
        f'<li><span class="grow">{e(t.name)}<br><span class="mut">{e(t.description)}</span></span>'
        f'<span class="mut">{t.items}</span></li>'
        for t in topics
    ) or '<li class="mut">Ingen emner endnu.</li>'
    body = f"""{nav}<p>{unclassified} endnu ikke inddelt · {len(topics)} {"emne" if len(topics) == 1 else "emner"}</p>
{todos_html(todos, ledger, request.app.state.today())}
{"".join(sections) or '<p class="mut">Intet er inddelt endnu.</p>'}
<h2>Emner</h2><ul class="list">{topics_html}</ul>"""
    return page("Overblik", body, request.query_params.get("msg"))


async def tick_todo(request: Request) -> Response:
    """A button in :data:`TODO_ACTIONS` ("Klaret", "Fortryd") on a todo of one item version."""
    if (known := TODO_ACTIONS.get(request.path_params["action"])) is None:
        return PlainTextResponse("Findes ikke.", status_code=404)
    act = known[1]
    form = await request.form()
    item = str(form.get("item") or "")
    raw_version = str(form.get("version") or "")
    version = int(raw_version) if raw_version.isdigit() else None
    back = str(form.get("back") or "")
    back = back if back.startswith("/overblik/item/") and "?" not in back else "/overblik"  # only back to this site

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
    return RedirectResponse(back + (f"?msg={quote(msg)}" if msg else ""), status_code=303)


# ----------------------------------------------------------------------------- the detail of one item


def child_names(child_ids: list[int], cfg: dict[str, Any]) -> str:
    """The children's first names; no children means every child (Aula set none, nor did the agent)."""
    if not child_ids:
        return "alle børn"
    return ", ".join(((cfg["children"].get(str(c)) or {}).get("name") or "?").split()[0] for c in child_ids)


def item_text(body: str) -> str:
    return f'<div class="text">{e(body)}</div>'


def file_url(item_key: str, attachment_id: str) -> str:
    """Where the file route hands out one of an item's files: by item key and attachment id, never a path."""
    return f"{store.item_path(item_key)}/fil/{quote(attachment_id, safe=':')}"


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
        "opslag" if item.kind == "post" else "besked",
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
    """ "Blev til i kalenderen": each event the item changed, with its whole history and whom the user removed it for."""
    if not d.events:
        return '<h3>Blev til i kalenderen</h3><p class="mut">Intet i kalenderen.</p>'
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
        rows.append(
            f'<li><span class="date">{e(when)}</span><span class="grow">{e(h.title)}{flag}'
            f'<br><span class="mut">{e(child_names(h.child_ids, d.cfg))}</span>'
            f'<br><span class="mut">{"<br>".join(lines)}</span></span></li>'
        )
    return f'<h3>Blev til i kalenderen</h3><ul class="list">{"".join(rows)}</ul>'


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
    nav = '<p><a href="/">Status</a> · <a href="/overblik">Overblik</a></p>'
    if d is None:
        return page("Findes ikke", nav + '<p class="mut">Det findes ikke i arkivet.</p>', status_code=404)
    hide = d.item.sensitive and request.query_params.get("show") != "1"
    title = "Overblik" if hide else d.item.latest.title or "Overblik"
    return page(title, nav + detail_html(d, hide=hide), request.query_params.get("msg"))


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
    return FileResponse(
        path, media_type=media_type or "application/octet-stream", filename=file.name,
        content_disposition_type="inline" if media_type else "attachment",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "sandbox"},
    )


async def remove_event(request: Request) -> Response:
    from .credentials import GoogleLoginRequired
    from .gcal import remove_event as do_remove

    form = await request.form()
    key = str(form.get("key") or "")
    raw_child = str(form.get("child_id") or "")
    child_id = int(raw_child) if raw_child.isdigit() else None  # a page from before removal was per child

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
    return go_home(msg)


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
  if(s.status==='done'){setTimeout(()=>location.href='/?msg='+encodeURIComponent(s.message),1500);return}
  if(s.status!=='error' && s.status!=='idle') setTimeout(poll,1000);
}
poll();
</script>"""


async def aula_login_page(request: Request) -> Response:
    from aula.config import load_config as load_aula_config

    login: MitIDLogin = request.app.state.mitid
    if login.running or (login.status in ("done", "error") and request.query_params.get("watch")):
        body = """<p id="status"></p><div class="qr" id="qr"></div><p class="mut" id="part"></p>
<p class="otp" id="otp"></p><p><a href="/">Tilbage</a></p>""" + LOGIN_JS
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
        return page("MitID-login", f'<p class="bad">{e(exc)}</p><p><a href="/">Tilbage</a></p>')
    return RedirectResponse("/aula/login?watch=1", status_code=303)


async def aula_login_state(request: Request) -> Response:
    return JSONResponse(request.app.state.mitid.snapshot(), headers={"Cache-Control": "no-store"})


def go_home(msg: str) -> RedirectResponse:
    return RedirectResponse(f"/?msg={quote(msg)}", status_code=303)


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
    return page("Google", body + '<p><a href="/">Tilbage</a></p>')


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
            Route("/", home),
            Route("/overblik", overview),
            Route("/overblik/item/{key}", item_detail),
            Route("/overblik/item/{key}/fil/{attachment:path}", item_file),
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
