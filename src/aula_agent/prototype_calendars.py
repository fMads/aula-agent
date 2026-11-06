"""PROTOTYPE – smid væk. Redigering af kalendere på web-siden (wayfinder #43, til #22).

Tre varianter af "Børn og kalendere" på den rigtige /settings i demoen, skiftes med ``?variant=A|B|C`` (eller
pilene i baren nederst / ←→). ``?som=medforaelder`` viser siden, som en medforælder ser den.

    uv run python -m aula_agent.prototype_calendars /tmp/aula-proto

Alt om kalendere ligger i hukommelsen her (ingen config, ingen Google, intet i Ledger). "Planen" er en stub, der
efterligner det, `calendars join/split` (#21) skal gøre: kommende poster flyttes, fortidige bliver liggende, fjernet
af dig følger barnet, og en kalender slettes aldrig. Den er ikke den rigtige logik.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
from dataclasses import dataclass

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from . import demo

EMMA, OSCAR, ALMA = demo.EMMA, demo.OSCAR, 3
VARIANTS = {"A": "Pr. barn, én formular", "B": "Pr. kalender, flyt ét barn ad gangen", "C": "Egen side med prøvekørsel"}
TEMPLATE = "Aula – {name}"
TODAY = dt.date.today()

START = {
    "children": {  # in the config's order
        EMMA: {"name": "Emma Demo", "inst": "Demo Skole", "cal": "c1", "short": "Emma"},
        OSCAR: {"name": "Oscar Demo", "inst": "Demo Børnehus", "cal": "c2", "short": "Oscar"},
        ALMA: {"name": "Alma Demo", "inst": "Demo Vuggestue", "cal": "c2", "short": "Alma"},
    },
    "calendars": {  # own: the name is still the one aula-agent gave it (then it may be renamed)
        "c1": {"name": "Aula – Emma", "own": True},
        "c2": {"name": "Aula – Oscar og Alma", "own": True},
    },
    "placement": "after",
}
STATE = copy.deepcopy(START)
EVENTS = [  # (key, title, days from today, children)
    ("post:40#1", "Høstfest", -18, [EMMA, OSCAR, ALMA]),
    ("post:44#1", "Forældrekaffe", -4, [OSCAR, ALMA]),
    ("post:42#1", "Fællessang", 0, [EMMA]),
    ("post:43#1", "Fodboldstævne", 1, [EMMA, OSCAR, ALMA]),
    ("message:70:a#1", "Tur i skoven", 5, [OSCAR]),
    ("post:3#1", "Frist: tilmelding til julefest", 9, [EMMA, OSCAR, ALMA]),
    ("post:8#1", "Forældremøde", 12, [OSCAR, ALMA]),
    ("post:45#1", "Bedsteforældredag", 20, [ALMA]),
    ("post:2#1", "Juleklip", 28, [OSCAR, ALMA]),
    ("post:3#2", "Julefest", 30, [EMMA, OSCAR, ALMA]),
]
REMOVED = {("post:2#1", OSCAR)}  # "Fjernet af dig" in the demo: Juleklip for Oscar


def e(v) -> str:
    from .web import e as esc
    return esc(v)


def day_text(n: int) -> str:
    from .web import short_date
    return short_date(TODAY + dt.timedelta(days=n), TODAY)


# ----------------------------------------------------------------------------- the stub of join/split


def kids_in(state, cal: str) -> list[int]:
    return [cid for cid, c in state["children"].items() if c["cal"] == cal]


def title_for(state, title: str, cal: str, cids: list[int]) -> str:
    if len(kids_in(state, cal)) < 2:
        return title
    shorts = [state["children"][c]["short"] for c in state["children"] if c in cids]
    return f"{'+'.join(shorts)}: {title}" if state["placement"] == "before" else f"{title} ({', '.join(shorts)})"


def joined(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} og {names[-1]}"


def entries(state, *, past: bool) -> dict[tuple[str, str], tuple[tuple[int, ...], str, int]]:
    out = {}
    for key, title, n, cids in EVENTS:
        if (n < 0) != past:
            continue
        for cal in state["calendars"]:
            mine = tuple(c for c in cids if state["children"][c]["cal"] == cal and (key, c) not in REMOVED)
            if mine:
                out[(key, cal)] = (mine, title_for(state, title, cal, list(mine)), n)
    return out


@dataclass
class Step:
    kind: str  # Opret / Opdatér / Slet
    cal: str
    n: int
    title: str
    old_title: str = ""
    why: str = ""


@dataclass
class Plan:
    new: dict
    cal_lines: list[str]
    steps: list[Step]
    moved_kids: list[int]
    moves: list[str]
    past: int
    removed_kept: list[str]
    names_only: bool

    @property
    def empty(self) -> bool:
        return not self.cal_lines and not self.steps


def propose(fields) -> dict:
    """The state the form asks for (cal_<id>, short_<id>, placement); "new" = an own, new calendar."""
    new = copy.deepcopy(STATE)
    n = 0
    for cid, c in new["children"].items():
        if f"short_{cid}" in fields and fields[f"short_{cid}"].strip():
            c["short"] = fields[f"short_{cid}"].strip()
        want = fields.get(f"cal_{cid}", c["cal"])
        if want == "new":
            if len(kids_in(STATE, c["cal"])) > 1:  # alone already: nothing to split off
                n += 1
                cal = f"n{len(new['calendars']) + n}"
                c["cal"] = cal
                new["calendars"][cal] = {"name": TEMPLATE.format(name=c["short"]), "own": True, "created": True}
        elif want in new["calendars"]:
            c["cal"] = want
    if fields.get("placement") in ("before", "after"):
        new["placement"] = fields["placement"]
    for cal, info in new["calendars"].items():  # aula-agent's own name follows its children when they change (join/split)
        kids = kids_in(new, cal)
        if info["own"] and kids and kids != kids_in(STATE, cal):
            info["name"] = TEMPLATE.format(name=joined([new["children"][k]["short"] for k in kids]))
    return new


def plan(new) -> Plan:
    old_e, new_e = entries(STATE, past=False), entries(new, past=False)
    steps = []
    for k in sorted(set(old_e) | set(new_e), key=lambda k: ((old_e.get(k) or new_e.get(k))[2], k[1])):
        o, nw = old_e.get(k), new_e.get(k)
        if o and not nw:
            steps.append(Step("Slet", k[1], o[2], o[1], why="ikke længere med"))
        elif nw and not o:
            steps.append(Step("Opret", k[1], nw[2], nw[1]))
        elif o != nw:
            steps.append(Step("Opdatér", k[1], nw[2], nw[1], old_title=o[1]))
    moved = [cid for cid in STATE["children"] if STATE["children"][cid]["cal"] != new["children"][cid]["cal"]]
    cal_lines = []
    for cal, info in new["calendars"].items():
        old = STATE["calendars"].get(cal)
        if not old:
            cal_lines.append(f"Ny kalender: {info['name']}. Del den i Google med den anden forælder bagefter.")
        elif old["name"] != info["name"] and kids_in(new, cal):
            cal_lines.append(f"Omdøbes: {old['name']} → {info['name']}")
        if old and kids_in(STATE, cal) and not kids_in(new, cal):
            cal_lines.append(f"Bliver tom: {old['name']}. Den slettes ikke; slet den selv i Google, hvis du vil.")
    past = sum(1 for (_, cal), (cids, _, _) in entries(STATE, past=True).items() if any(new["children"][c]["cal"] != cal for c in cids))
    removed_kept = [
        f"{t} ({day_text(n)}) kommer ikke i {new['calendars'][new['children'][c]['cal']]['name']} for {STATE['children'][c]['short']}"
        for key, t, n, cids in EVENTS for c in cids if (key, c) in REMOVED and c in moved and n >= 0
    ]
    moves = []
    for cid in moved:
        n = sum(1 for key, _, d, cids in EVENTS if cid in cids and d >= 0 and (key, cid) not in REMOVED)
        moves.append(f"{STATE['children'][cid]['short']}: {n} kommende poster flyttes fra {cal_name(STATE['children'][cid]['cal'])}"
                     f" til {new['calendars'][new['children'][cid]['cal']]['name']}")
    return Plan(new, cal_lines, steps, moved, moves, past, removed_kept, names_only=not moved)


# ----------------------------------------------------------------------------- HTML bits


def cal_name(cal: str, state=None) -> str:
    return (state or STATE)["calendars"][cal]["name"]


def hidden(new, variant: str, role: str) -> str:
    f = [f'<input type="hidden" name="variant" value="{variant}"><input type="hidden" name="som" value="{role}">']
    for cid, c in new["children"].items():
        cal = c["cal"] if c["cal"] in STATE["calendars"] else "new"
        f.append(f'<input type="hidden" name="cal_{cid}" value="{cal}"><input type="hidden" name="short_{cid}" value="{e(c["short"])}">')
    f.append(f'<input type="hidden" name="placement" value="{new["placement"]}">')
    return "".join(f)


def example(state, placement: str) -> str:
    s = {**state, "placement": placement}
    joint = next((cal for cal in state["calendars"] if len(kids_in(state, cal)) > 1), None)
    if not joint:
        return "Fodboldstævne (Emma, Oscar)" if placement == "after" else "Emma+Oscar: Fodboldstævne"
    return title_for(s, "Fodboldstævne", joint, kids_in(state, joint))


def placement_html(state, *, only_if_joint: bool = False) -> str:
    if only_if_joint and not any(len(kids_in(state, c)) > 1 for c in state["calendars"]):
        return ""
    opts = "".join(
        f'<label style="display:block;margin:.25rem 0"><input type="radio" name="placement" value="{p}"'
        f'{" checked" if state["placement"] == p else ""}> {label}: <b>{e(example(state, p))}</b></label>'
        for p, label in (("after", "Efter titlen"), ("before", "Før titlen"))
    )
    return f'<fieldset style="border:0;padding:0;margin:.8rem 0"><legend class="mut">Børnenes navne i titlen, i fælles kalendere</legend>{opts}</fieldset>'


def readonly_list(note: str = "") -> str:
    rows = []
    for cid, c in STATE["children"].items():
        others = [STATE["children"][k]["short"] for k in kids_in(STATE, c["cal"]) if k != cid]
        joint = f' <span class="tag">fælles med {e(joined(others))}</span>' if others else ""
        short = f' <span class="mut">· kort navn »{e(c["short"])}«</span>' if others else ""
        rows.append(f'<li><span class="grow">{e(c["name"])} <span class="mut">· {e(c["inst"])}</span>{short}</span>'
                    f'<span class="mut">{e(cal_name(c["cal"]))}{joint}</span></li>')
    return f'<h2>Børn og kalendere</h2><ul class="list box">{"".join(rows)}</ul>{note}'


CO_PARENT_NOTE = '<p class="mut">Kun operatoren kan ændre, hvilke kalendere børnene ligger i.</p>'


def cal_select(cid: int, state, *, name: str | None = None, others_only: bool = False) -> str:
    c = state["children"][cid]
    opts = []
    for cal in state["calendars"]:
        if others_only and cal == c["cal"]:
            continue
        kids = joined([state["children"][k]["short"] for k in kids_in(state, cal)]) or "tom"
        opts.append(f'<option value="{cal}"{" selected" if cal == c["cal"] else ""}>{e(cal_name(cal, state))} ({e(kids)})</option>')
    if len(kids_in(state, c["cal"])) > 1:
        opts.append(f'<option value="new">Egen, ny kalender ({e(TEMPLATE.format(name=c["short"]))})</option>')
    return f'<select name="{name or f"cal_{cid}"}">{"".join(opts)}</select>'


# ----------------------------------------------------------------------------- the variants


def variant_a(role: str) -> str:
    """Pr. barn: every child a row with its calendar and short name, one "Gem" for it all, then a confirmation."""
    if role != "operator":
        return readonly_list(CO_PARENT_NOTE)
    rows = "".join(
        f'<li><span class="grow"><b>{e(c["name"])}</b> <span class="mut">· {e(c["inst"])}</span></span>'
        f'<label class="mut">Kalender {cal_select(cid, STATE)}</label>'
        f'<label class="mut">Kort navn <input type="text" name="short_{cid}" value="{e(c["short"])}" size="7"></label></li>'
        for cid, c in STATE["children"].items()
    )
    return f"""<h2>Børn og kalendere</h2>
<form method="post" action="/proto/plan"><input type="hidden" name="variant" value="A"><input type="hidden" name="som" value="{role}">
<ul class="list box">{rows}</ul>
<p class="mut">Børn i samme kalender får én post sammen. Det korte navn står i titlen, når kalenderen er fælles.</p>
{placement_html(STATE)}
<button class="primary">Gem…</button> <span class="mut">Du ser, hvad der flyttes, før noget sker.</span></form>"""


def variant_b(role: str) -> str:
    """Pr. kalender: a card per calendar with its children; move one child at a time (join/split); short names only
    on joint calendars, saved at once."""
    if role != "operator":
        return readonly_list(CO_PARENT_NOTE)
    cards = []
    for cal in STATE["calendars"]:
        kids = kids_in(STATE, cal)
        if not kids:
            cards.append(f'<div class="card"><b>{e(cal_name(cal))}</b><span class="mut">Tom. Slet den i Google, hvis du ikke vil have den.</span></div>')
            continue
        joint = len(kids) > 1
        rows = []
        for cid in kids:
            c = STATE["children"][cid]
            short = (f'<label class="mut">kort navn <input type="text" name="short_{cid}" value="{e(c["short"])}" size="6" form="names-{cal}"></label>'
                     if joint else "")
            move = (f'<form method="post" action="/proto/plan" class="inline"><input type="hidden" name="variant" value="B">'
                    f'<input type="hidden" name="som" value="{role}">{cal_select(cid, STATE, others_only=True)} '
                    f'<button class="small">Flyt</button></form>')
            rows.append(f'<li><span class="grow">{e(c["name"])} <span class="mut">· {e(c["inst"])}</span></span>{short}{move}</li>')
        names_form = (f'<form id="names-{cal}" method="post" action="/proto/plan"><input type="hidden" name="variant" value="B">'
                      f'<input type="hidden" name="som" value="{role}"><button class="small">Gem korte navne</button></form>' if joint else "")
        tag = ' <span class="tag">fælles</span>' if joint else ""
        cards.append(f'<section style="margin-bottom:1rem"><h3>{e(cal_name(cal))}{tag}</h3><ul class="list box">{"".join(rows)}</ul>{names_form}</section>')
    placement = placement_html(STATE, only_if_joint=True)
    placement_form = (f'<form method="post" action="/proto/plan"><input type="hidden" name="variant" value="B">'
                      f'<input type="hidden" name="som" value="{role}">{placement}<button class="small">Gem</button></form>' if placement else "")
    return f"""<h2>Kalendere</h2>{"".join(cards)}
<p class="mut">»Flyt« lægger barnet i en anden kalender (eller sin egen) og flytter dets kommende poster med. Du bekræfter først.</p>
{placement_form}"""


def variant_c(role: str) -> str:
    """Settings only shows; the editing is on its own page (/settings/calendars)."""
    link = (f'<p><a class="btn" href="/settings/calendars?variant=C&som={role}">Rediger kalendere…</a></p>'
            if role == "operator" else CO_PARENT_NOTE)
    return readonly_list(link)


def editor_c(role: str) -> str:
    cals = list(STATE["calendars"])
    head = "".join(f'<th style="text-align:center;padding:.3rem .5rem">{e(cal_name(cal))}</th>' for cal in cals)
    rows = []
    for cid, c in STATE["children"].items():
        cells = "".join(f'<td style="text-align:center"><input type="radio" name="cal_{cid}" value="{cal}"'
                        f'{" checked" if c["cal"] == cal else ""} aria-label="{e(cal_name(cal))}"></td>' for cal in cals)
        cells += f'<td style="text-align:center"><input type="radio" name="cal_{cid}" value="new" aria-label="Egen, ny kalender"></td>'
        rows.append(f'<tr style="border-top:1px solid var(--line)"><th style="text-align:left;padding:.5rem .5rem .5rem 0">{e(c["name"])}'
                    f'<div class="mut" style="font-weight:400">{e(c["inst"])}</div></th>{cells}'
                    f'<td><input type="text" name="short_{cid}" value="{e(c["short"])}" size="7"></td></tr>')
    return f"""<h2 class="title">Rediger kalendere</h2>
<p class="mut">Hvert barn ligger i én kalender. Børn i samme kalender får én post sammen, med deres korte navne i titlen.</p>
<form method="post" action="/proto/plan"><input type="hidden" name="variant" value="C"><input type="hidden" name="som" value="{role}">
<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:14px">
<tr><th></th>{head}<th style="padding:.3rem .5rem">Egen, ny</th><th style="text-align:left">Kort navn</th></tr>{"".join(rows)}</table></div>
{placement_html(STATE)}
<button class="primary">Prøvekørsel…</button> <a class="btn" href="/settings?variant=C&som={role}">Fortryd</a></form>"""


# ----------------------------------------------------------------------------- the confirmation (per variant)


def step_line(s: Step, state) -> str:
    extra = f' <span class="mut">(var »{e(s.old_title)}«)</span>' if s.old_title and s.old_title != s.title else ""
    why = f' <span class="mut">({e(s.why)})</span>' if s.why else ""
    cal = state["calendars"].get(s.cal) or STATE["calendars"][s.cal]
    return (f'<li><span class="date">{e(day_text(s.n))}</span><span class="grow"><b>{s.kind}:</b> {e(s.title)}{extra}{why}'
            f'<div class="mut">{e(cal["name"])}</div></span></li>')


def counts(p: Plan) -> str:
    by = {k: sum(1 for s in p.steps if s.kind == k) for k in ("Opret", "Opdatér", "Slet")}
    words = {"Opret": "oprettes", "Opdatér": "får ny titel", "Slet": "slettes"}
    return ", ".join(f"{n} {words[k]}" for k, n in by.items() if n) or "ingen kommende poster ændres"


def confirm_html(p: Plan, variant: str, role: str) -> str:
    back = f"/settings/calendars?variant=C&som={role}" if variant == "C" else f"/settings?variant={variant}&som={role}"
    if p.empty:
        return f'<h2 class="title">Intet at gemme</h2><p>Der er ingen ændringer.</p><p><a class="btn" href="{back}">Tilbage</a></p>'
    form = f'<form method="post" action="/proto/apply">{hidden(p.new, variant, role)}'
    cal_lines = "".join(f"<li>{e(x)}</li>" for x in p.cal_lines)
    removed = "".join(f"<li>{e(x)}</li>" for x in p.removed_kept)
    past = f"<li>{p.past} tidligere poster bliver liggende, hvor de er.</li>" if p.past else ""
    steps = "".join(step_line(s, p.new) for s in p.steps)
    who = joined([STATE["children"][c]["short"] for c in p.moved_kids]) if p.moved_kids else ""

    if variant == "B":  # short: one sentence, the rest folded
        head = (f"Flyt {e(who)} til {e(cal_name(p.new['children'][p.moved_kids[0]]['cal'], p.new))}?" if p.moved_kids
                else "Gem?")
        return f"""<h2 class="title">{head}</h2>
<p>{e(" ".join(p.moves))}.{" " + e(" ".join(p.cal_lines)) if p.cal_lines else ""}</p>
{f'<p class="mut">{removed}</p>' if removed else ""}
<details><summary>Vis alle {len(p.steps)} ændringer</summary><ul class="list box">{steps}</ul></details>
{form}<p><button class="primary">{"Flyt" if p.moved_kids else "Gem"}</button> <a class="btn" href="{back}">Fortryd</a></p></form>"""

    if variant == "C":  # the dry run, as `apply --dry-run` and `calendars join --dry-run` print it
        lines = [*(f"Kalender: {x}" for x in p.cal_lines)]
        lines += [f"{s.kind}: {day_text(s.n)} {s.title} [{(p.new['calendars'].get(s.cal) or STATE['calendars'][s.cal])['name']}]"
                  + (f" ({s.why})" if s.why else "") for s in p.steps]
        lines += [f"Springer over: {x} (fjernet af dig)" for x in p.removed_kept]
        lines += [f"Bliver liggende: {p.past} tidligere poster"] if p.past else []
        pre = "\n".join(lines)
        return f"""<h2 class="title">Prøvekørsel</h2>
<p class="mut">Sådan som <code>aula-agent calendars … --dry-run</code> ville vise det. Intet er ændret endnu.</p>
<pre style="background:var(--card);border:1px solid var(--line);border-radius:12px;padding:.8rem;overflow-x:auto;font-size:.85rem">{e(pre)}</pre>
{form}<p><button class="primary">Udfør</button> <a class="btn" href="{back}">Tilbage til redigering</a></p></form>"""

    # A: grouped, with the consequences first and the entries below
    return f"""<h2 class="title">Det her sker, når du gemmer</h2>
{f'<h2>Kalendere</h2><ul>{cal_lines}</ul>' if cal_lines else ""}
{f'<h2>Børn der skifter kalender</h2><ul>{"".join(f"<li>{e(m)}</li>" for m in p.moves)}</ul>' if p.moves else ""}
<h2>Kommende poster</h2><p>{e(counts(p)).capitalize()}.</p>
<details><summary>Vis alle {len(p.steps)}</summary><ul class="list box">{steps}</ul></details>
{f'<h2>Fjernet af dig følger barnet</h2><ul>{removed}</ul>' if removed else ""}
{f'<ul class="mut">{past}</ul>' if past else ""}
{form}<p><button class="primary">Gem og flyt</button> <a class="btn" href="{back}">Fortryd</a></p></form>"""


# ----------------------------------------------------------------------------- routes


SWITCHER = """<div id="proto-bar" style="position:fixed;z-index:99;left:50%;transform:translateX(-50%);
bottom:calc(var(--tabs-h) + env(safe-area-inset-bottom) + .8rem);display:flex;gap:.6rem;align-items:center;
background:#111;color:#fff;border-radius:999px;padding:.4rem .8rem;box-shadow:0 4px 18px rgba(0,0,0,.35);font:13px system-ui">
<a href="#" id="pv" style="color:#fff;text-decoration:none">◀</a><span id="pl"></span><a href="#" id="pn" style="color:#fff;text-decoration:none">▶</a>
<span style="opacity:.5">|</span><a href="#" id="pr" style="color:#ffd166"></a><span style="opacity:.5">|</span>
<a href="/proto/reset" style="color:#aaa">nulstil</a></div>
<style>@media (min-width:60rem){#proto-bar{bottom:1.2rem!important}}</style>
<script>
(()=>{const V=__V__,N=__N__,u=new URL(location.href),cur=u.searchParams.get('variant')||'A',role=u.searchParams.get('som')||'operator';
const go=(v,r)=>{const x=new URL('/settings',location.origin);x.searchParams.set('variant',v);x.searchParams.set('som',r);location.href=x};
const i=V.indexOf(cur);document.getElementById('pl').textContent='PROTOTYPE '+cur+' ('+N[cur]+')';
document.getElementById('pv').onclick=ev=>{ev.preventDefault();go(V[(i+V.length-1)%V.length],role)};
document.getElementById('pn').onclick=ev=>{ev.preventDefault();go(V[(i+1)%V.length],role)};
const pr=document.getElementById('pr');pr.textContent='som '+(role==='operator'?'operator':'medforælder');
pr.onclick=ev=>{ev.preventDefault();go(cur,role==='operator'?'medforaelder':'operator')};
addEventListener('keydown',ev=>{if(ev.target.closest('input,textarea,select,[contenteditable]'))return;
if(ev.key==='ArrowLeft')document.getElementById('pv').click();if(ev.key==='ArrowRight')document.getElementById('pn').click()});})();
</script>"""


def with_bar(html: str) -> str:
    import json
    return html.replace("</body>", SWITCHER.replace("__V__", json.dumps(list(VARIANTS))).replace("__N__", json.dumps(VARIANTS, ensure_ascii=False)) + "</body>")


def params(request: Request, fields=None) -> tuple[str, str]:
    src = fields if fields is not None else request.query_params
    v = src.get("variant") if src.get("variant") in VARIANTS else "A"
    return v, ("medforaelder" if src.get("som") == "medforaelder" else "operator")


async def settings(request: Request) -> Response:
    from . import web
    variant, role = params(request)
    real = await web.settings(request)
    html = real.body.decode()
    section = {"A": variant_a, "B": variant_b, "C": variant_c}[variant](role)
    html = re.sub(r"<h2>Børn og kalendere</h2><ul class=\"list\">.*?</ul>", lambda _: section, html, count=1, flags=re.S)
    return HTMLResponse(with_bar(html), headers={"Cache-Control": "no-store"})


async def calendars_page(request: Request) -> Response:
    from . import web
    variant, role = params(request)
    if role != "operator":
        return RedirectResponse(f"/settings?variant={variant}&som={role}", status_code=303)
    return HTMLResponse(with_bar(web.page("Rediger kalendere", editor_c(role), nav=web.tabs("settings")).body.decode()))


async def plan_page(request: Request) -> Response:
    from . import web
    fields = await request.form()
    variant, role = params(request, fields)
    p = plan(propose(fields))
    if variant == "B" and p.names_only and not p.cal_lines:  # B: names and placement at once, no confirmation
        msg = apply(p.new, p)
        return RedirectResponse(f"/settings?variant=B&som={role}&msg={msg}", status_code=303)
    return HTMLResponse(with_bar(web.page("Gem kalendere", confirm_html(p, variant, role), nav=web.tabs("settings")).body.decode()))


def apply(new, p: Plan) -> str:
    from urllib.parse import quote
    for info in new["calendars"].values():
        info.pop("created", None)
    STATE.clear()
    STATE.update(new)
    return quote(f"Gemt (prototype, intet sendt til Google): {counts(p)}.")


async def apply_page(request: Request) -> Response:
    fields = await request.form()
    variant, role = params(request, fields)
    new = propose(fields)
    msg = apply(new, plan(new))
    return RedirectResponse(f"/settings?variant={variant}&som={role}&msg={msg}", status_code=303)


async def reset(request: Request) -> Response:
    STATE.clear()
    STATE.update(copy.deepcopy(START))
    return RedirectResponse(request.headers.get("referer") or "/settings", status_code=303)


def main() -> None:
    """The demo, with a third child and these routes in front of the real ones."""
    real_app = demo.demo_app

    def app_with_proto():
        from . import store
        cfg = store.load_config()
        cfg["children"][str(ALMA)] = {"name": "Alma Demo", "institution": "Demo Vuggestue", "calendar_name": "Aula – Oscar og Alma"}
        cfg["children"][str(OSCAR)]["calendar_name"] = "Aula – Oscar og Alma"
        store.save_config(cfg)
        asgi = real_app()
        inner = asgi.__closure__  # the Starlette app sits in demo_app's closure
        app = next(c.cell_contents for c in inner if hasattr(c.cell_contents, "router"))
        app.router.routes[:0] = [
            Route("/settings", settings),
            Route("/settings/calendars", calendars_page),
            Route("/proto/plan", plan_page, methods=["POST"]),
            Route("/proto/apply", apply_page, methods=["POST"]),
            Route("/proto/reset", reset),
        ]
        return asgi

    demo.demo_app = app_with_proto
    demo.main()


if __name__ == "__main__":
    main()
