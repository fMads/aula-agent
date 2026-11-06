"""PROTOTYPE (#34) – smides væk. Tre varianter af en samlet forside, på /forside?variant=A|B|C, kun i demoen.

Spørgsmålet: hvordan ser forsiden ud, når status og overblik er ét (Q7–Q9, Q12 i #34)? Data er demoens
opdigtede, læst med de rigtige funktioner (archive, timeline, ledger). Ingen tests, ingen fejlhåndtering.
"Klaret" sender til den rigtige /todos/check i demo-mappen og streger ud uden at genindlæse.
"""

from __future__ import annotations

import datetime as dt
from html import escape as e

from starlette.requests import Request
from starlette.responses import HTMLResponse

from . import archive, credentials, store, timeline
from .ledger import Ledger
from .web import CATEGORY_NAMES, SENSITIVE, item_title, todo_lists, why_current

VARIANTS = {"A": "Faner + to kolonner", "B": "Én dagsliste", "C": "Tavle"}
WD = ("man", "tir", "ons", "tor", "fre", "lør", "søn")
WD_LONG = ("Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Lørdag", "Søndag")
PALETTE = ("#0f9d8a", "#e8710a", "#7b5cd6", "#d23f77")


def load(today: dt.date):
    cfg, ledger = store.load_config(), Ledger.load()
    kids = {int(k): (v["name"].split()[0], PALETTE[i % len(PALETTE)]) for i, (k, v) in enumerate(cfg["children"].items())}
    events = ledger.upcoming(today.isoformat())
    from .ledger import UpcomingEvent  # PROTOTYPE: to opdigtede events i dag og i morgen, kun i hukommelsen
    ids = [int(k) for k in cfg["children"]]
    events += [UpcomingEvent("proto#1", "Morgensang i hallen", f"{today}T08:15", f"{today}T09:00", ids[:1], None, {}, None),
               UpcomingEvent("proto#2", "Fodboldstævne", (today + dt.timedelta(days=1)).isoformat(),
                             (today + dt.timedelta(days=1)).isoformat(), ids, None, {}, None)]
    with archive.read() as a:
        items, todos = a.items(), a.todos()
        sources = {k: it for k in {ev.source for ev in events if ev.source} if (it := a.item(k))}
    shown = [i for i in items if i.classification and i.classification.category != archive.Category.SVAR]
    open_, _ = todo_lists(todos, ledger)
    weeks = timeline.upcoming(shown, events, open_, today, sources)
    days: dict[dt.date, list] = {}
    for w in weeks:
        for line in w.lines:
            days.setdefault(line.day, []).append(line)
    now = timeline.now(shown, today)
    info = [i for i in shown if i.classification.category == archive.Category.INFO]
    warn = []
    if not credentials.google_status().ok:
        warn.append("Google er ikke forbundet, så intet kommer i kalenderen.")
    return dict(kids=kids, open=open_, days=days, now=now, info=info, warn=warn, today=today)


# ----------------------------------------------------------------------------- små byggeklodser (fælles)


def who(ids, kids, *, dots_only=False) -> str:
    ids = list(ids) or list(kids)
    if len(ids) == len(kids) and len(kids) > 1:
        return '<span class="who all">Begge</span>' if not dots_only else "".join(
            f'<i class="dot" style="background:{kids[c][1]}"></i>' for c in ids)
    return "".join(
        f'<span class="who" style="--k:{kids[c][1]}"><i class="dot"></i>{"" if dots_only else e(kids[c][0])}</span>'
        for c in ids if c in kids)


def href(item) -> str:
    return e(store.item_path(item.key))


def title_of(item) -> str:
    return SENSITIVE if item.sensitive else item_title(item)


def tick(t) -> str:
    return (f'<button class="tick" data-item="{e(t.item.key)}" data-version="{t.version.id}" '
            f'aria-label="Klaret"></button>')


def todo_text(t) -> str:
    return SENSITIVE if t.item.sensitive else e(t.text)


def due_text(t, today) -> str:
    if t.due is None:
        return ""
    n = (t.due - today).days
    if n < 0:
        return f'<span class="over">{-n} dage over</span>'
    return {0: "i dag", 1: "i morgen"}.get(n, f"{WD[t.due.weekday()]} {t.due:%d/%m}")


def line_parts(line, kids):
    """(ikon, titel-html, under-tekst, børn) for én linje i en dag."""
    match line:
        case timeline.EventLine(event=ev, source=src):
            t = ev.start[11:16] if len(ev.start) > 10 else ""
            link = f'<a href="{href(src)}">{e(ev.title)}</a>' if src else e(ev.title)
            return "event", link, (f"kl. {t}" if t else "hele dagen"), ev.child_ids
        case timeline.ItemLine(item=it):
            c = it.classification
            sub = "" if it.sensitive else (c.summary or "")
            return c.category, f'<a href="{href(it)}">{e(title_of(it))}</a>', sub, it.child_ids
        case timeline.TodoLine(todo=t):
            return "todo", todo_text(t), f'fra <a href="{href(t.item)}">{e(title_of(t.item))}</a>', t.item.child_ids


def day_label(d: dt.date, today: dt.date) -> str:
    n = (d - today).days
    rel = {0: "I dag", 1: "I morgen"}.get(n)
    return f"{rel} · {WD_LONG[d.weekday()].lower()} {d.day}/{d.month}" if rel else f"{WD_LONG[d.weekday()]} {d.day}/{d.month}"


BASE_CSS = """
:root{--fg:#1c1c1e;--mut:#6b6b70;--line:#e6e6ea;--bg:#fafaf9;--card:#fff;--acc:#2156c9;--warn:#b3261e;--soft:#f1f1ef}
@media (prefers-color-scheme:dark){:root{--fg:#ececef;--mut:#9a9aa0;--line:#2c2c30;--bg:#121214;--card:#1b1b1e;--acc:#8ab0ff;--warn:#ff8a80;--soft:#202024}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}body{margin:0;font:16px/1.45 system-ui,-apple-system,sans-serif;color:var(--fg);background:var(--bg)}
a{color:inherit;text-decoration:none}a:hover{text-decoration:underline}
.mut{color:var(--mut);font-size:.88rem}
.dot{display:inline-block;width:.6rem;height:.6rem;border-radius:50%;background:var(--k,var(--mut));margin-right:.3rem;vertical-align:.05rem}
.who{font-size:.8rem;color:var(--k);font-weight:600;white-space:nowrap;margin-right:.5rem}.who.all{color:var(--mut)}
.tick{width:1.6rem;height:1.6rem;border-radius:50%;border:2px solid var(--line);background:transparent;cursor:pointer;flex:none}
.tick:hover{border-color:var(--acc)}.done .tick{background:var(--acc);border-color:var(--acc)}.done .txt{text-decoration:line-through;color:var(--mut)}
.over{color:var(--warn);font-weight:600}
.warn{background:#fdecea;color:#7a1c14;padding:.6rem 1rem;border-radius:10px;margin:0 0 1rem;font-size:.92rem}
@media (prefers-color-scheme:dark){.warn{background:#3a1d1b;color:#ffb4ab}}
.warn a{text-decoration:underline}
input.q{width:100%;font:inherit;font-size:1.05rem;padding:.7rem 1rem;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.k-event{--ic:"📅"}.ic{width:1.6rem;flex:none;text-align:center}
#proto-bar{position:fixed;left:50%;bottom:5.2rem;transform:translateX(-50%);z-index:99;display:flex;gap:.6rem;align-items:center;
background:#111;color:#fff;border-radius:999px;padding:.35rem .5rem;box-shadow:0 6px 20px rgba(0,0,0,.35);font:600 13px system-ui}
#proto-bar a{color:#fff;padding:.2rem .6rem;border-radius:999px;background:#333}#proto-bar a:hover{background:#555;text-decoration:none}
@media (min-width:60rem){#proto-bar{bottom:1rem}}
"""

SWITCH_JS = """
document.addEventListener('keydown',ev=>{if(ev.target.closest('input,textarea,[contenteditable]'))return;
 const a=ev.key==='ArrowLeft'?'prev':ev.key==='ArrowRight'?'next':null;if(a)location=document.getElementById('proto-'+a).href});
document.addEventListener('click',async ev=>{const b=ev.target.closest('.tick');if(!b)return;ev.preventDefault();
 const row=b.closest('[data-todo]');const on=!row.classList.contains('done');
 const f=new FormData();f.append('item',b.dataset.item);f.append('version',b.dataset.version);
 await fetch('/todos/'+(on?'check':'uncheck'),{method:'POST',body:f,redirect:'manual'});row.classList.toggle('done',on)});
"""


def switcher(cur: str, tab: str) -> str:
    keys = list(VARIANTS)
    i = keys.index(cur)
    prev, nxt = keys[i - 1], keys[(i + 1) % len(keys)]
    t = f"&tab={tab}" if tab != "forside" else ""
    return (f'<nav id="proto-bar"><a id="proto-prev" href="?variant={prev}{t}">←</a>'
            f'<span>PROTOTYPE {cur} · {e(VARIANTS[cur])}</span><a id="proto-next" href="?variant={nxt}{t}">→</a></nav>')


def doc(title: str, css: str, body: str, cur: str, tab: str) -> HTMLResponse:
    return HTMLResponse(
        f'<!doctype html><html lang="da"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="theme-color" content="#2156c9"><title>{e(title)}</title><style>{BASE_CSS}{css}</style></head>'
        f'<body>{body}{switcher(cur, tab)}<script>{SWITCH_JS}</script></body></html>',
        headers={"Cache-Control": "no-store"},
    )


def warn_html(d) -> str:
    return "".join(f'<div class="warn">⚠ {e(w)} <a href="/">Indstillinger</a></div>' for w in d["warn"])


def search_box() -> str:
    return '<form action="/overblik"><input class="q" type="search" name="q" placeholder="Søg i alt fra Aula – også i PDF\'er"></form>'


def info_html(d) -> str:
    by: dict[str, list] = {}
    for it in d["info"]:
        by.setdefault((it.classification.topics or ("Uden emne",))[0], []).append(it)
    return "".join(
        f'<h3>{e(t.capitalize())}</h3>' + "".join(
            f'<div class="info-row"><a href="{href(i)}"><b>{e(title_of(i))}</b></a> {who(i.child_ids, d["kids"])}'
            f'<div class="mut">{e(i.classification.summary or "")}</div></div>' for i in items)
        for t, items in sorted(by.items()))


# ----------------------------------------------------------------------------- A: faner + to kolonner

A_CSS = """
.wrap{max-width:72rem;margin:0 auto;padding:1rem 1rem 6rem}
header.top{display:flex;align-items:center;gap:1.5rem;padding:.4rem 0 1rem}header.top .brand{font-weight:700;font-size:1.1rem}
nav.tabs{display:flex;flex:1}nav.tabs a{padding:.5rem .2rem;margin-right:1rem;color:var(--mut);font-weight:500}nav.tabs a.on{color:var(--fg);border-bottom:2px solid var(--acc)}
nav.tabs .gear{margin-left:auto}
@media (max-width:59.99rem){header.top nav.tabs{display:none}
 nav.bottom{position:fixed;left:0;right:0;bottom:0;display:flex;background:var(--card);border-top:1px solid var(--line);padding:.3rem 0 calc(.3rem + env(safe-area-inset-bottom));z-index:10}
 nav.bottom a{flex:1;text-align:center;font-size:.75rem;color:var(--mut);padding:.3rem 0}nav.bottom a b{display:block;font-size:1.25rem;font-weight:400}
 nav.bottom a.on{color:var(--acc)}}
@media (min-width:60rem){nav.bottom{display:none}.cols{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);grid-template-areas:"todo near" "now later";column-gap:2rem;align-items:start}
 .a-todo{grid-area:todo}.a-near{grid-area:near}.a-now{grid-area:now}.a-later{grid-area:later}}
h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:1.6rem 0 .5rem}
.box{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:.2rem .9rem}
.r{display:flex;gap:.7rem;align-items:flex-start;padding:.65rem 0;border-top:1px solid var(--line)}.box>.r:first-child{border-top:0}
.r .g{flex:1;min-width:0}.r .t{font-weight:550}.r .s{font-size:.88rem;color:var(--mut)}
.day h3{font-size:.95rem;margin:1.1rem 0 .3rem}.day.today h3{color:var(--acc)}
.tm{min-width:3.2rem;font-variant-numeric:tabular-nums;color:var(--mut);font-size:.88rem;padding-top:.1rem}
.cat{font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
.info-row{padding:.5rem 0;border-top:1px solid var(--line)}
"""


def a_rows_day(lines, d, kids) -> str:
    out = []
    for line in lines:
        kind, title, sub, ids = line_parts(line, kids)
        if kind == "todo":
            t = line.todo
            out.append(f'<div class="r" data-todo>{tick(t)}<div class="g"><div class="t txt">{title}</div>'
                       f'<div class="s">{who(ids, kids)}{sub}</div></div></div>')
            continue
        tm = sub if kind == "event" else ""
        label = "Kalender" if kind == "event" else CATEGORY_NAMES[kind]
        out.append(f'<div class="r"><span class="tm">{e(tm.removeprefix("kl. ")) if tm and tm != "hele dagen" else ""}</span>'
                   f'<div class="g"><div class="cat">{e(label)}</div><div class="t">{title}</div>'
                   f'<div class="s">{who(ids, kids)}{"" if kind == "event" else e(sub)}</div></div></div>')
    return "".join(out)


def variant_a(d, tab: str) -> HTMLResponse:
    kids, today = d["kids"], d["today"]
    tabs = [("forside", "Forside", "⌂"), ("info", "Info", "ℹ"), ("tidligere", "Tidligere", "↶"), ("indst", "Indstillinger", "⚙")]

    def tlink(k, label, ic, cls=""):
        url = "/" if k == "indst" else (f"/overblik#tidligere" if k == "tidligere" else f"?variant=A&tab={k}")
        return f'<a class="{"on" if k == tab else ""} {cls}" href="{url}"><b>{ic}</b>{label}</a>' if cls == "b" else \
            f'<a class="{"on" if k == tab else ""}{" gear" if k == "indst" else ""}" href="{url}">{label if k != "indst" else "⚙ " + label}</a>'

    top = (f'<header class="top"><span class="brand">Aula</span><nav class="tabs">'
           f'{"".join(tlink(*t) for t in tabs)}</nav></header>')
    bottom = f'<nav class="bottom">{"".join(tlink(*t, cls="b") for t in tabs)}</nav>'
    if tab == "info":
        return doc("Info", A_CSS, f'<div class="wrap">{top}{search_box()}<h2>Stående info</h2>{info_html(d)}</div>{bottom}', "A", tab)

    todos = "".join(
        f'<div class="r" data-todo>{tick(t)}<div class="g"><div class="t txt">{todo_text(t)}</div>'
        f'<div class="s">{who(t.item.child_ids, kids)}{due_text(t, today)}</div></div></div>' for t in d["open"]
    ) or '<div class="r mut">Intet at gøre 🎉</div>'
    now = "".join(
        f'<div class="r"><div class="g"><div class="t"><a href="{href(i)}">{e(title_of(i))}</a></div>'
        f'<div class="s">{who(i.child_ids, kids)}{e(why_current(i, why) or "")}</div></div></div>' for i, why in d["now"])
    soon = [today, today + dt.timedelta(days=1)]
    near = "".join(
        f'<div class="day{" today" if x == today else ""}"><h3>{e(day_label(x, today))}</h3>'
        f'<div class="box">{a_rows_day(d["days"].get(x, []), x, kids) or "<div class=r><span class=mut>Intet</span></div>"}</div></div>'
        for x in soon)
    later = "".join(
        f'<div class="day"><h3>{e(day_label(x, today))}</h3><div class="box">{a_rows_day(ls, x, kids)}</div></div>'
        for x, ls in sorted(d["days"].items()) if x not in soon)
    body = (f'<div class="wrap">{top}{warn_html(d)}{search_box()}<div class="cols">'
            f'<div class="a-todo"><h2>Skal gøres · {len(d["open"])}</h2><div class="box">{todos}</div></div>'
            f'<div class="a-near">{near}</div>'
            f'<div class="a-now"><h2>Gælder nu</h2><div class="box">{now}</div></div>'
            f'<div class="a-later"><h2>Kommende</h2>{later}</div></div></div>{bottom}')
    return doc("Forside", A_CSS, body, "A", tab)


# ----------------------------------------------------------------------------- B: én dagsliste

B_CSS = """
.wrap{max-width:40rem;margin:0 auto;padding:0 1rem 6rem}
@media (min-width:60rem){.shell{display:grid;grid-template-columns:16rem minmax(0,40rem);gap:2.5rem;justify-content:center}.wrap{margin:0;padding-top:1rem}
 aside{position:sticky;top:0;height:100vh;padding:1.2rem 0;display:flex;flex-direction:column;gap:.4rem}}
@media (max-width:59.99rem){aside{display:none}}
aside .brand{font-weight:700;font-size:1.2rem;margin-bottom:1rem}aside a.nav{padding:.45rem .7rem;border-radius:8px;color:var(--mut)}aside a.nav.on{background:var(--soft);color:var(--fg);font-weight:600}
aside .kids{margin-top:1.5rem}aside .kids div{padding:.3rem .7rem;font-size:.9rem}aside .bottom{margin-top:auto}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);padding:.8rem 0 .6rem;display:flex;gap:.5rem;align-items:center}
.bar form{flex:1}.bar .gear{font-size:1.3rem;padding:.3rem .5rem}@media (min-width:60rem){.bar .gear{display:none}}
.pins{display:flex;gap:.5rem;overflow-x:auto;padding:.2rem 0 .8rem;scrollbar-width:none}
.pin{flex:none;max-width:15rem;background:var(--card);border:1px solid var(--line);border-left:4px solid var(--k,var(--acc));border-radius:10px;padding:.45rem .7rem;font-size:.88rem}
.pin b{display:block;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.daycard{margin:1.4rem 0}.daycard h2{font-size:1.15rem;margin:0 0 .4rem;display:flex;gap:.5rem;align-items:baseline}
.daycard h2 small{color:var(--mut);font-weight:400;font-size:.85rem}
.daycard.overdue h2{color:var(--warn)}
.e{display:flex;gap:.7rem;align-items:center;padding:.55rem .8rem;margin:.35rem 0;border-radius:12px;background:var(--card);border:1px solid var(--line)}
.e.event{border-left:4px solid var(--k,var(--acc))}.e .g{flex:1;min-width:0}.e .s{font-size:.86rem;color:var(--mut)}
.e .tm{font-weight:700;font-variant-numeric:tabular-nums;min-width:3rem}
.e.todo{background:transparent;border-style:dashed}
.gap{color:var(--mut);font-size:.85rem;text-align:center;margin:1rem 0}
h2.sec{font-size:1.15rem}
.info-row{padding:.5rem 0;border-top:1px solid var(--line)}
"""


def variant_b(d, tab: str) -> HTMLResponse:
    kids, today = d["kids"], d["today"]
    nav = "".join(f'<a class="nav{" on" if k == tab else ""}" href="{u}">{n}</a>' for k, n, u in (
        ("forside", "Dagene", "?variant=B"), ("info", "Stående info", "?variant=B&tab=info"),
        ("tidligere", "Tidligere", "/overblik#tidligere")))
    kids_html = "".join(f'<div><i class="dot" style="background:{c}"></i>{e(n)}</div>' for n, c in kids.values())
    aside = (f'<aside><span class="brand">Aula</span>{nav}<div class="kids mut">{kids_html}</div>'
             f'<a class="nav bottom" href="/">⚙ Indstillinger</a></aside>')
    bar = f'<div class="bar">{search_box()}<a class="gear" href="/" title="Indstillinger">⚙</a></div>'
    if tab == "info":
        return doc("Info", B_CSS, f'<div class="shell">{aside}<div class="wrap">{bar}<h2 class="sec">Stående info</h2>{info_html(d)}</div></div>', "B", tab)

    def kcolor(ids):
        ids = list(ids)
        return kids[ids[0]][1] if len(ids) == 1 and ids[0] in kids else "var(--mut)"

    pins = "".join(
        f'<a class="pin" style="--k:{kcolor(i.child_ids)}" href="{href(i)}"><b>{e(title_of(i))}</b>'
        f'<span class="mut">{e(why_current(i, why) or "")}</span></a>' for i, why in d["now"])

    def entry(line):
        kind, title, sub, ids = line_parts(line, kids)
        if kind == "todo":
            return (f'<div class="e todo" data-todo>{tick(line.todo)}<div class="g"><div class="txt">{title}</div>'
                    f'<div class="s">{who(ids, kids)}{sub}</div></div></div>')
        if kind == "event":
            tm = sub.removeprefix("kl. ") if sub != "hele dagen" else ""
            return (f'<div class="e event" style="--k:{kcolor(ids)}"><span class="tm">{e(tm) or "📅"}</span>'
                    f'<div class="g"><div>{title}</div><div class="s">{who(ids, kids)}</div></div></div>')
        return (f'<div class="e"><div class="g"><div>{title}</div><div class="s">{who(ids, kids)}'
                f'{e(CATEGORY_NAMES[kind])}{" · " + e(sub) if sub else ""}</div></div></div>')

    overdue = [t for t in d["open"] if t.due and t.due < today]
    nodue = [t for t in d["open"] if t.due is None]
    todo_entry = lambda t: (f'<div class="e todo" data-todo>{tick(t)}<div class="g"><div class="txt">{todo_text(t)}</div>'  # noqa: E731
                            f'<div class="s">{who(t.item.child_ids, kids)}{due_text(t, today)}</div></div></div>')
    parts = []
    if overdue:
        parts.append(f'<section class="daycard overdue"><h2>Glemt?</h2>{"".join(todo_entry(t) for t in overdue)}</section>')
    days = dict(d["days"])
    days.setdefault(today, [])
    days.setdefault(today + dt.timedelta(days=1), [])
    prev = None
    for x in sorted(days):
        if prev and (x - prev).days > 1:
            parts.append(f'<div class="gap">· · · {(n_ := (x - prev).days - 1)} {"dag" if n_ == 1 else "dage"} uden noget · · ·</div>')
        prev = x
        n = (x - today).days
        head = {0: "I dag", 1: "I morgen"}.get(n, f"{WD_LONG[x.weekday()]}")
        body = "".join(entry(line) for line in days[x])
        if n == 0:
            body = "".join(todo_entry(t) for t in nodue) + body
        parts.append(f'<section class="daycard"><h2>{head} <small>{x.day}/{x.month}</small></h2>'
                     f'{body or "<div class=mut>Intet fra Aula</div>"}</section>')
    body = (f'<div class="shell">{aside}<div class="wrap">{bar}{warn_html(d)}<div class="pins">{pins}</div>'
            f'{"".join(parts)}</div></div>')
    return doc("Dagene", B_CSS, body, "B", tab)


# ----------------------------------------------------------------------------- C: tavle

C_CSS = """
.wrap{max-width:84rem;margin:0 auto;padding:1rem 1rem 6rem}
header{display:flex;gap:1rem;align-items:center;margin-bottom:1rem}header .brand{font-weight:800;font-size:1.3rem}
header form{flex:1}header a.gear{font-size:1.3rem}
.board{display:grid;gap:1rem}
@media (min-width:60rem){.board{grid-template-columns:repeat(3,minmax(0,1fr));align-items:start}}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:1rem 1.1rem}
.card h2{font-size:1rem;margin:0 0 .6rem;display:flex;justify-content:space-between}.card h2 .n{background:var(--soft);border-radius:999px;padding:0 .55rem;font-size:.85rem}
.big{font-size:2.2rem;font-weight:800;line-height:1}
.today-card{background:linear-gradient(135deg,#2156c9,#5b3cc4);color:#fff;border:0}.today-card .mut,.today-card a{color:#e7e9ff}
.today-card .it{border-color:rgba(255,255,255,.2)}
.it{display:flex;gap:.6rem;align-items:center;padding:.5rem 0;border-top:1px solid var(--line)}.it:first-of-type{border-top:0}.it .g{flex:1;min-width:0}
.it .s{font-size:.85rem;color:var(--mut)}
.wk{display:grid;grid-template-columns:3.2rem 1fr;gap:.3rem .6rem;font-size:.92rem}
.wk .d{color:var(--mut);font-variant-numeric:tabular-nums;padding:.35rem 0;border-top:1px solid var(--line)}.wk .c{padding:.35rem 0;border-top:1px solid var(--line)}
.wk .c div+div{margin-top:.2rem}.wk .d.t{color:var(--acc);font-weight:700}
details.card>summary{cursor:pointer;font-weight:700;list-style:none}details.card>summary::after{content:" ▾";color:var(--mut)}
@media (max-width:59.99rem){details.card:not([open]){padding:.8rem 1.1rem}}
.info-row{padding:.4rem 0;border-top:1px solid var(--line);font-size:.92rem}
.kidtabs{display:flex;gap:.4rem;margin:0 0 1rem}.kidtabs a{border:1px solid var(--line);border-radius:999px;padding:.3rem .8rem;font-size:.9rem}.kidtabs a.on{background:var(--fg);color:var(--bg)}
"""


def variant_c(d, tab: str) -> HTMLResponse:
    kids, today = d["kids"], d["today"]
    tomorrow = today + dt.timedelta(days=1)

    def short(line):
        kind, title, sub, ids = line_parts(line, kids)
        if kind == "todo":
            return f'<div data-todo class="it">{tick(line.todo)}<span class="g txt">{title}</span>{who(ids, kids, dots_only=True)}</div>'
        tm = f'<b>{e(sub.removeprefix("kl. "))}</b> ' if kind == "event" and sub != "hele dagen" else ""
        mark = "📅 " if kind == "event" else ""
        return f'<div>{who(ids, kids, dots_only=True)}{tm}{mark}{title}</div>'

    today_lines = d["days"].get(today, []) + d["days"].get(tomorrow, [])
    today_card = (
        f'<section class="card today-card"><div class="mut">{WD_LONG[today.weekday()]} {today.day}/{today.month}</div>'
        f'<div class="big">{len(today_lines) or "Intet"}</div><div class="mut">ting i dag og i morgen</div>'
        + "".join(f'<div class="it"><span class="g">{"I dag" if line.day == today else "I morgen"}: {short(line)}</span></div>'
                  for line in today_lines) + "</section>")
    todos = "".join(
        f'<div class="it" data-todo>{tick(t)}<div class="g"><div class="txt">{todo_text(t)}</div>'
        f'<div class="s">{due_text(t, today)}</div></div>{who(t.item.child_ids, kids, dots_only=True)}</div>' for t in d["open"])
    week = []
    for n in range(14):
        x = today + dt.timedelta(days=n)
        ls = d["days"].get(x, [])
        if not ls and x.weekday() >= 5:
            continue
        week.append(f'<span class="d{" t" if n == 0 else ""}">{WD[x.weekday()]} {x.day}</span>'
                    f'<span class="c">{"".join(short(line) for line in ls) or "<span class=mut>–</span>"}</span>')
    now = "".join(f'<div class="it"><div class="g"><a href="{href(i)}">{e(title_of(i))}</a><div class="s">{e(why_current(i, why) or "")}</div></div>'
                  f'{who(i.child_ids, kids, dots_only=True)}</div>' for i, why in d["now"])
    kidtabs = '<nav class="kidtabs"><a class="on" href="#">Alle</a>' + "".join(
        f'<a href="#"><i class="dot" style="background:{c}"></i>{e(n)}</a>' for n, c in kids.values()) + "</nav>"
    body = (f'<div class="wrap"><header><span class="brand">Aula</span>{search_box()}<a class="gear" href="/">⚙</a></header>'
            f'{warn_html(d)}{kidtabs}<div class="board">'
            f'<div>{today_card}<section class="card" style="margin-top:1rem"><h2>Skal gøres <span class="n">{len(d["open"])}</span></h2>{todos}</section></div>'
            f'<section class="card"><h2>De næste to uger</h2><div class="wk">{"".join(week)}</div></section>'
            f'<div><section class="card"><h2>Gælder nu <span class="n">{len(d["now"])}</span></h2>{now}</section>'
            f'<details class="card" style="margin-top:1rem"><summary>Stående info</summary>{info_html(d)}</details>'
            f'<a class="card" style="display:block;margin-top:1rem" href="/overblik#tidligere">↶ Tidligere</a></div>'
            f'</div></div>')
    return doc("Tavle", C_CSS, body, "C", tab)


async def forside(request: Request) -> HTMLResponse:
    v = request.query_params.get("variant", "A").upper()
    tab = request.query_params.get("tab", "forside")
    d = load(dt.date.today())
    return {"A": variant_a, "B": variant_b, "C": variant_c}.get(v, variant_a)(d, tab)
