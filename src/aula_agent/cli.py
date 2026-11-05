"""aula-agent: Aula posts/messages -> Google Calendar (one calendar per child)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import NoReturn

import click

from . import store
from .runs import Outcome, Problem, run

EXIT_AULA_LOGIN = 3
EXIT_GOOGLE_LOGIN = 4
EXIT_BAD_EVENTS = 5
EXIT_CODES = {Outcome.AULA_LOGIN: EXIT_AULA_LOGIN, Outcome.GOOGLE_LOGIN: EXIT_GOOGLE_LOGIN, Outcome.BAD_EVENTS: EXIT_BAD_EVENTS}


def _fail(exc: Problem) -> NoReturn:
    """Tell the user (or the agent) what to do, and exit with the outcome's code."""
    if exc.outcome == Outcome.AULA_LOGIN:
        where = store.web_url("/aula/login")
        how = f"Log ind på {where}, eller kør" if where else "Kør"
        click.echo(f"AULA_LOGIN_REQUIRED: {exc} {how} `uv run aula login` i en terminal i projektmappen.", err=True)
    elif exc.outcome == Outcome.GOOGLE_LOGIN:
        where = store.web_url("/google")
        how = f"Forbind Google på {where}, eller kør" if where else "Kør"
        click.echo(f"GOOGLE_LOGIN_REQUIRED: {exc} {how} `uv run aula-agent google-login`.", err=True)
    else:
        click.echo(str(exc), err=True)
    sys.exit(EXIT_CODES.get(exc.outcome, 1))


@click.group()
def main() -> None:
    """Finder arrangementer i Aula og lægger dem i Google Calendar."""


@main.command()
@click.option("--first-run-days", default=30, show_default=True, help="Hvor langt tilbage første kørsel kigger.")
def fetch(first_run_days: int) -> None:
    """Hent nye opslag og beskeder fra Aula til data/inbox.json."""
    from .aula_source import fetch as do_fetch

    try:
        with run("fetch") as r:
            summary = asyncio.run(do_fetch(first_run_days=first_run_days))
            r.summary = f"{len(summary['new'])} nye/ændrede, {summary['pending']} venter" + (
                f", {len(summary['warnings'])} advarsler" if summary["warnings"] else ""
            )
    except Problem as exc:
        _fail(exc)
    click.echo(f"Nye/ændrede: {len(summary['new'])}  Venter på behandling: {summary['pending']}")
    for w in summary["warnings"]:
        click.echo(f"ADVARSEL: {w}", err=True)
    click.echo(f"Indbakke: {store.INBOX_FILE}")


@main.command()
@click.argument("rescue", required=False, type=click.Path(exists=True, file_okay=False, path_type=Path))
def backfill(rescue: Path | None) -> None:
    """Hent alt, hvad Aula stadig har, til arkivet (rører ikke inbox). Kan køres igen.

    Med RESCUE (en redningskopi, fx data/rescue-2026-11-05) læses kopien i stedet for Aula:
    filerne kopieres ind, og versionerne får datoen i mappens navn.
    """
    from .aula_source import backfill as do_backfill

    try:
        with run("backfill" if rescue is None else "backfill-rescue") as r:  # only Aula says if the login works
            result = asyncio.run(do_backfill(rescue))
            r.summary = (
                f"{result.posts} opslag, {result.threads} tråde med {result.messages} beskeder,"
                f" {result.new_versions} nye versioner"
            ) + (f", {len(result.warnings)} advarsler" if result.warnings else "")
    except Problem as exc:
        _fail(exc)
    click.echo(f"Arkiveret: {r.summary}")
    for w in result.warnings:
        click.echo(f"ADVARSEL: {w}", err=True)


RESULT_LABELS = (("created", "oprettet"), ("updated", "opdateret"), ("deleted", "slettet"), ("skipped", "sprunget over"))


@main.command()
@click.argument("events_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Tjek filen og vis hvad der ville ske, uden at ændre noget.")
def apply(events_file: Path, dry_run: bool) -> None:
    """Opret/opdatér begivenheder i Google Calendar ud fra en events-fil."""
    from .gcal import sync

    raw = events_file.read_text(encoding="utf-8")
    try:
        with run("apply", log=not dry_run) as r:
            result = sync(raw, dry_run=dry_run, echo=click.echo)
            r.summary = ", ".join(f"{len(result[k])} {label}" for k, label in RESULT_LABELS)
    except Problem as exc:
        _fail(exc)
    if dry_run:
        return
    for kind, label in RESULT_LABELS:
        for line in result[kind]:
            click.echo(f"{label.capitalize()}: {line}")
    click.echo(f"Markeret som behandlet: {len(result['processed'])}")


@main.command()
def backup() -> None:
    """Kopiér arkivet og dets filer til den private Drive-mappe nu (fetch gør det også hver nat)."""
    from .backup import take

    try:
        with run("backup") as r:
            result = take()
            r.summary = f"backup taget, {result.files} nye filer"
    except Problem as exc:
        _fail(exc)
    click.echo(f"Backup taget {result.at} ({result.files} nye filer lagt op).")


@main.command("google-login")
def google_login() -> None:
    """Giv adgang til Google Calendar og Drive (én gang)."""
    from .credentials import interactive_login
    from .runs import record

    interactive_login(prompt=lambda text: click.prompt(text, prompt_suffix=""), echo=click.echo)
    record("google-login", Outcome.OK, "Google forbundet")
    click.echo("Google-adgang gemt.")


@main.command("pdf-text")
@click.argument("path", type=click.Path(dir_okay=False, path_type=Path))
def pdf_text(path: Path) -> None:
    """Udskriv teksten i en PDF (til agenter der ikke selv kan læse PDF'er). Stier relativt til data/ virker også."""
    from pypdf import PdfReader

    if not path.is_file() and (store.DATA_DIR / path).is_file():
        path = store.DATA_DIR / path
    if not path.is_file():
        raise click.BadParameter(f"filen findes ikke: {path}", param_hint="PATH")

    reader = PdfReader(str(path))
    for number, page in enumerate(reader.pages, 1):
        click.echo(f"--- side {number} ---")
        click.echo((page.extract_text() or "").strip() or "(ingen tekst – siden er måske et billede)")


@main.command("web-setup")
@click.option("--url", help="Web-sidens offentlige adresse, fx https://aula.example.dk")
@click.option("--team-domain", help="Cloudflare Access team-domæne, fx myteam.cloudflareaccess.com")
@click.option("--aud", help="Application Audience (AUD) tag fra Access-appen")
@click.option("--email", "emails", multiple=True, help="E-mail der må logge ind (kan gentages; erstatter listen)")
def web_setup(url: str | None, team_domain: str | None, aud: str | None, emails: tuple[str, ...]) -> None:
    """Sæt web-siden op (adresse, Cloudflare Access og hvem der må logge ind). Uden argumenter: vis opsætningen."""
    if url and not url.startswith("https://"):
        raise click.BadParameter("skal starte med https://", param_hint="--url")
    with store.lock():  # fetch rewrites the config too
        cfg = store.load_config()
        web_cfg = cfg.setdefault("web", {})
        if url:
            web_cfg["public_url"] = url.rstrip("/")
        if team_domain:
            web_cfg["access_team_domain"] = team_domain.removeprefix("https://").rstrip("/")
        if aud:
            web_cfg["access_aud"] = aud.strip()
        if emails:
            web_cfg["allowed_emails"] = sorted({e.strip().lower() for e in emails})
        if url or team_domain or aud or emails:
            store.save_config(cfg)
    for key, label in (
        ("public_url", "Adresse"),
        ("access_team_domain", "Team-domæne"),
        ("access_aud", "AUD-tag"),
        ("allowed_emails", "Må logge ind"),
    ):
        value = web_cfg.get(key)
        click.echo(f"{label + ':':14} {', '.join(value) if isinstance(value, list) else value or '(mangler)'}")
    missing = [k for k in ("public_url", "access_team_domain", "access_aud", "allowed_emails") if not web_cfg.get(k)]
    if missing:
        click.echo("Web-siden afviser alle, indtil alt ovenfor er udfyldt.")


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8787, show_default=True)
def web(host: str, port: int) -> None:
    """Start web-siden (bag Cloudflare Access)."""
    import uvicorn

    from .web import create_app

    uvicorn.run(create_app(), host=host, port=port, proxy_headers=False, log_level="info")


@main.command()
def status() -> None:
    """Vis om logins er på plads, hvilke børn/kalendere der findes, og hvad der venter."""
    from .credentials import aula_status, google_status
    from .ledger import Ledger

    cfg, ledger = store.load_config(), Ledger.load()
    aula, google = aula_status(ledger), google_status()
    aula_hint = "" if aula.ok else " (log ind på web-siden eller kør `uv run aula login`)"
    google_hint = {
        "Mangler OAuth-nøgle": f" (læg nøglen i {store.GOOGLE_CLIENT_SECRET})",
        "Ikke forbundet": " (uv run aula-agent google-login)",
    }.get(google.text, "")
    click.echo(f"Aula-login:   {aula.text}{aula_hint}")
    click.echo(f"Google-login: {google.text}{google_hint}")
    click.echo(f"Web-side:     {store.web_url() or 'ikke sat op (valgfri)'}")
    click.echo(f"Sidst hentet: {ledger.last_fetch or 'aldrig'}")
    click.echo(f"Venter:       {len(ledger.pending_keys())} opslag/beskeder")
    click.echo(f"Synkroniseret: {ledger.synced_count()} kalenderposter")
    for cid, child in cfg["children"].items():
        cal = child.get("calendar_name") or ("(oprettes ved første begivenhed)" if not child.get("calendar_id") else child["calendar_id"])
        click.echo(f"  Barn {cid}: {child.get('name')} – {child.get('institution')} → {cal}")


if __name__ == "__main__":
    main()
