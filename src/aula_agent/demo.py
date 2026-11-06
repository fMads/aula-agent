"""A demo of the web page with made-up data, to try the overview in a browser: ``uv run python -m aula_agent.demo <dir>``.

It fills ``<dir>`` (empty, or made by an earlier demo) with two made-up children and made-up items, classifications,
todos and events, dated around today, and serves the web page from it on 127.0.0.1 only, on a free port, without
Cloudflare Access: every request is let in as a made-up parent. MitID and Google are switched off. It never uses
the real ``data/``: the data directory is set before anything reads it, and a directory that is (or is in, or
holds) the real one is refused.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import socket
import sys
from pathlib import Path

HOST = "127.0.0.1"  # never anything else: the demo has no Access in front
MARKER = ".aula-agent-demo"
EMAIL = "forælder@demo.invalid"
EMMA, OSCAR = 1, 2


def refuse_real_data(target: Path, real: Path) -> None:
    """Raise SystemExit when ``target`` is the real data directory, inside it, or holds it."""
    pairs = [(target.absolute(), real.absolute()), (target.resolve(), real.resolve())]
    if any(t == r or t.is_relative_to(r) or r.is_relative_to(t) for t, r in pairs):
        raise SystemExit(f"Demoen bruger aldrig de rigtige data ({real}). Vælg en anden mappe, fx /tmp/aula-demo.")


def prepare(target: Path) -> None:
    """Make ``target`` the demo's data directory (empty or from an earlier demo, which is started over)."""
    if target.exists() and any(target.iterdir()) and not (target / MARKER).exists():
        raise SystemExit(f"{target} er ikke tom og ikke en demo-mappe. Vælg en tom eller ny mappe.")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("archive.sqlite", "state.json", "config.json"):
        (target / name).unlink(missing_ok=True)
    (target / MARKER).write_text("Data til aula-agent's demo (opdigtet). Kan slettes.\n")


def free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def fill(today: dt.date, port: int) -> None:
    """The made-up data, dated around ``today``."""
    from . import archive, store
    from .archive import Category as C
    from .archive import Classification, Todo
    from .ledger import Ledger, SyncedEntry

    def day(n: int) -> dt.date:
        return today + dt.timedelta(days=n)

    def stamp(n: int, hour: int = 8) -> str:
        return f"{day(n).isoformat()}T{hour:02d}:00:00"

    cfg = store.load_config()
    cfg["children"] = {
        str(EMMA): {"name": "Emma Demo", "institution": "Demo Skole", "calendar_name": "Aula – Emma"},
        str(OSCAR): {"name": "Oscar Demo", "institution": "Demo Skole", "calendar_name": "Aula – Oscar"},
    }
    cfg["web"] = {"public_url": f"http://{HOST}:{port}", "allowed_emails": [EMAIL],
                  "access_team_domain": "demo.invalid", "access_aud": "demo"}
    store.save_config(cfg)

    def post(pid, title, came, text, *, important=None, author="Lærer Demo"):
        raw = {"id": pid, "title": title, "content": {"html": "".join(f"<p>{p}</p>" for p in text.split("\n\n"))},
               "publishAt": stamp(came), "ownerProfile": {"id": 3, "profileId": 4, "fullName": author}}
        if important:
            raw |= {"isImportant": True, "importantFrom": stamp(important[0]), "importantTo": stamp(important[1])}
        return raw

    def msg(mid, came, text, sender="Pædagog Demo"):
        return {"id": mid, "messageType": "Message", "sendDateTime": stamp(came, 15),
                "text": {"html": f"<p>{text}</p>"}, "sender": {"fullName": sender}}

    def c(category, start=None, end=None, topics=(), summary=None, todo=None, due=None, children=()):
        return Classification(
            category, start and day(start), end and day(end), tuple(topics), summary,
            child_ids=tuple(children), todo=Todo(todo, due and day(due)) if todo else None,
        )

    topics = {
        "svømning": "Svømmeundervisning", "jul": "Julearrangementer", "skolefest": "Skolefest og fester",
        "tur": "Ture ud af huset", "skema": "Skema og mødetider", "sundhed": "Lus, sygdom og trivsel",
        "sfo": "SFO og fritid", "forældremøde": "Forældremøder og samtaler", "idræt": "Idræt og sport",
    }
    with archive.open() as a:
        for name, description in topics.items():
            a.create_topic(name, description)

        def save(raw, classification=None, children=()):
            a.save_post(raw, child_ids=children)
            if classification:
                a.classify(archive.post_key(raw["id"]), classification)

        save(post(1, "Svømning i november", -10, "Vi skal svømme om onsdagen i fire uger.\n\nHusk badetøj og håndklæde."),
             c(C.PERIODE, -4, 24, ["svømning"], "Svømning onsdage i fire uger, husk badetøj og håndklæde."), [EMMA])
        save(post(2, "Juleklip i klassen", -3, "Vi klipper julepynt. Medbring saks og lim.\n\nForældre er velkomne fra kl. 12."),
             c(C.ARRANGEMENT, 28, None, ["jul"], "Juleklip, husk saks og lim. Forældre velkomne fra kl. 12.",
               "Medbring saks og lim", 28), [OSCAR])
        save(post(3, "Tilmelding til skolefesten", -2, "Skolefesten holdes om en måned. Tilmeld jer i Aula senest om ni dage."),
             c(C.FRIST, 9, None, ["skolefest"], "Tilmelding til skolefesten senest om ni dage.", "Tilmeld i Aula", 9))
        save(post(4, "Skema for skoleåret", -60, "Her er skemaet for året.\n\nMandag: dansk, matematik.\nTirsdag: idræt."),
             c(C.INFO, None, None, ["skema"], "Skemaet for skoleåret, idræt tirsdag."), [EMMA])
        save(post(5, "Mødetider i SFO", -45, "SFO åbner kl. 6.30 og lukker kl. 17."),
             c(C.INFO, None, None, ["sfo"], "SFO åbner 6.30 og lukker 17."))
        save(post(6, "Lus i klassen", -2, "Der er konstateret lus. Tjek venligst jeres barns hår."),
             c(C.ORIENTERING, None, None, ["sundhed"], "Lus i klassen, tjek håret."), [EMMA])
        save(post(7, "Ny mødetid om morgenen", -1, "Fra på mandag møder vi kl. 8.15.", important=(-1, 6)),
             c(C.ORIENTERING, None, None, ["skema"], "Ny mødetid kl. 8.15 fra mandag."), [OSCAR])
        save(post(8, "Forældremøde", -5, "Forældremøde i klassen om tolv dage kl. 19."),
             c(C.ARRANGEMENT, 12, None, ["forældremøde"], "Forældremøde kl. 19 i klassen."), [OSCAR])
        save(post(9, "Idrætsdag", -4, "Idrætsdag for hele skolen. Husk drikkedunk og tøj til at være ude."),
             c(C.ARRANGEMENT, 16, None, ["idræt"], "Idrætsdag, husk drikkedunk og udetøj.", "Pak drikkedunk og udetøj", 16))
        save(post(10, "Emneuge om rummet", -6, "Næste uge er emneuge om rummet."),
             c(C.PERIODE, 3, 7, ["skema"], "Emneuge om rummet, normale mødetider."), [EMMA])
        for n in range(11):  # Tidligere, a few months back (with "Vis ældre")
            ago = -14 - 8 * n
            save(post(20 + n, f"Ugebrev uge {(day(ago)).isocalendar().week}", ago, "Hvad vi har lavet i ugen."),
                 c(C.ORIENTERING, None, None, ["sfo" if n % 2 else "skema"], "Ugens nyt fra klassen."),
                 [EMMA] if n % 2 else [OSCAR])
        save(post(40, "Høstfest", -21, "Høstfest i skolegården."), c(C.ARRANGEMENT, -18, None, ["skolefest"], "Høstfest i skolegården."))
        save(post(41, "Billeder fra turen", -1, "Tak for en dejlig tur."))  # not classified yet

        a.save_thread("70", [msg("a", -2, "Vi skal i skoven på fredag. Betal 40 kr. via MobilePay."),
                             msg("b", -1, "Tak, det er betalt.", sender="Forælder Demo")],
                      thread={"id": "70", "subject": "Tur til skoven", "sensitive": False}, child_ids=[OSCAR])
        a.classify("message:70:a", c(C.ARRANGEMENT, 5, None, ["tur"], "Tur i skoven, betal 40 kr. via MobilePay.",
                                     "Betal 40 kr. til turen", 3))
        a.classify("message:70:b", c(C.SVAR))
        a.save_thread("71", [msg("a", -1, "Kan vi tale om skoledagen? Ring gerne.")],
                      thread={"id": "71", "subject": "Samtale om skoledagen", "sensitive": True}, child_ids=[EMMA])
        a.classify("message:71:a", c(C.ORIENTERING, todo="Ring til skolen", due=4))

    ledger = Ledger.load()
    for key, children, title, start, source in (
        ("post:2#1", [OSCAR], "Juleklip", day(28), "post:2"),
        ("post:3#1", [EMMA, OSCAR], "Frist: tilmelding til skolefest", day(9), "post:3"),
        ("post:8#1", [OSCAR], "Forældremøde", day(12), "post:8"),
        ("message:70:a#1", [OSCAR], "Tur i skoven", day(5), "message:70:a"),
        ("post:3#2", [EMMA, OSCAR], "Skolefest", day(30), "post:3"),
    ):
        for child in children:
            ledger.record(SyncedEntry(key, [child], f"cal{child}", f"ev-{key}-{child}", title, start.isoformat(),
                                      start.isoformat(), source, {}, f"https://calendar.google.com/demo/{key}"))
        ledger.add_history(key, "created", source, title=title, start=start.isoformat(), end=start.isoformat())
    ledger.mark_removed("post:2#1", OSCAR, title="Juleklip", start=day(28).isoformat(), end=day(28).isoformat())
    ledger.save()


class DemoGoogle:
    """Google as the demo has it: "Fjern" on the status page removes from the demo's own calendar (none)."""

    def delete_event(self, calendar_id: str, event_id: str) -> bool:
        return True


class DemoVerifier:
    """Lets every request in as the made-up parent (the demo's stand-in for Cloudflare Access)."""

    def email(self, token: str) -> str:
        return EMAIL


def demo_app():
    """The web app as the demo serves it: every request carries a made-up Access token, MitID and Google are off."""
    from starlette.responses import PlainTextResponse

    from . import web

    app = web.create_app(verifier_factory=DemoVerifier, google=DemoGoogle)

    async def asgi(scope, receive, send):
        if scope["type"] == "http":
            if scope["path"].startswith(("/aula", "/google")):
                await PlainTextResponse("Ikke i demoen.", status_code=403)(scope, receive, send)
                return
            scope = {**scope, "headers": [*scope["headers"], (b"cf-access-jwt-assertion", b"demo")]}
        await app(scope, receive, send)

    return asgi


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m aula_agent.demo", description=__doc__.splitlines()[0])
    parser.add_argument("dir", type=Path, help="en tom mappe (eller en tidligere demo-mappe), fx /tmp/aula-demo")
    parser.add_argument("--port", type=int, default=0, help="port på 127.0.0.1 (standard: en ledig)")
    args = parser.parse_args(argv)
    target = args.dir.expanduser().absolute()

    # The data directory is set before store is imported: then store never works out (or links) the real one.
    if "aula_agent.store" in sys.modules:
        raise SystemExit("Demoen skal startes for sig: uv run python -m aula_agent.demo <mappe>")
    real = [Path(os.environ["AULA_AGENT_DATA"])] if os.environ.get("AULA_AGENT_DATA") else []
    os.environ["AULA_AGENT_DATA"] = str(target)

    from . import store

    for path in real + store.real_data_dirs():
        refuse_real_data(target, path)
    if store.DATA_DIR.resolve() != target.resolve():
        raise SystemExit(f"Demoen ville bruge {store.DATA_DIR}, ikke {target}.")
    prepare(target)
    port = args.port or free_port()
    fill(dt.date.today(), port)

    import uvicorn

    from . import credentials

    credentials.aula_username = lambda: None  # the status page does not look at the real Aula login

    print(f"Demo: http://{HOST}:{port}/overblik  (opdigtede data i {target}; Ctrl-C stopper)", flush=True)
    uvicorn.run(demo_app(), host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
