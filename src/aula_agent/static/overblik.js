// Overblik: et lag ovenpå siderne (#32). Uden JS virker alt; dette gør dem bare hurtigere at bruge:
// - søgning mens man skriver (og filtre og barn-filter uden genindlæsning), URL'en følger med,
// - detaljen i en skuffe på brede skærme (tilbage-knappen og Esc lukker den), fuld side på mobil,
// - "/" fokuserer søgefeltet (på detaljen som fuld side: går til overblikket med fokus i det),
// - "Klaret"/"Fortryd" uden at miste pladsen på siden.
// Siderne hentes som almindelig HTML, og kun <main id="overblik-main"> (og toppen) skiftes ud.
// Går noget galt (fx Cloudflare Access vil have et nyt login), så navigeres der helt almindeligt.
(() => {
  "use strict";

  const MAIN = "overblik-main";
  const WIDE = window.matchMedia("(min-width: 64rem)");
  const DEBOUNCE_MS = 250;
  const PAUSE_MS = 1500; // så længe uden at skrive, og næste søgning er en ny post i historikken
  const parser = new DOMParser();
  const top = () => document.getElementById("overblik-top");
  const main = () => document.getElementById(MAIN);

  if (!main()) return;

  // ------------------------------------------------------------------ hent en side

  let controller = null;

  // Henter en side og giver dens dokument; null når en nyere hentning er startet.
  async function load(url, init = {}) {
    if (!init.keep) {
      if (controller) controller.abort();
      controller = new AbortController();
    }
    const signal = init.keep ? undefined : controller.signal;
    const r = await fetch(url, { credentials: "same-origin", headers: { Accept: "text/html" }, signal, ...init.fetch });
    const type = r.headers.get("content-type") || "";
    if (!type.startsWith("text/html")) throw new Error("ikke html");
    const doc = parser.parseFromString(await r.text(), "text/html");
    if (!doc.getElementById(MAIN)) throw new Error("ikke en overbliks-side");
    return { doc, url: r.url };
  }

  function fallback(url, err) {
    if (err && err.name === "AbortError") return;
    window.location.assign(url);
  }

  // Det der skiftes ud fra en hentet side: dens <main>, med sidens besked (fx "Prøv igen om lidt") øverst.
  function mainOf(doc) {
    const content = doc.getElementById(MAIN);
    const flash = doc.querySelector("body > .flash");
    if (flash) content.prepend(flash);
    return content;
  }

  // Links og formularer på en side vist et andet sted (skuffen) er skrevet til sidens egen adresse (fx "?show=1").
  function rebase(content, url) {
    content.querySelectorAll("a[href]").forEach((a) => (a.href = new URL(a.getAttribute("href"), url).href));
    content.querySelectorAll("form[action]").forEach((f) => (f.action = new URL(f.getAttribute("action"), url).href));
    return content;
  }

  function sameList(a, b) {
    const u = new URL(a, location.href), v = new URL(b, location.href);
    u.searchParams.delete("msg");
    v.searchParams.delete("msg");
    return u.pathname + u.search === v.pathname + v.search;
  }

  // ------------------------------------------------------------------ listen (overblik og søgning)

  let listUrl = location.href;

  // Skifter listen ud med siden på ``url``; ``whole``: også søgefeltet og filtrene (ikke mens man skriver).
  // ``history``: "push", "replace" eller "search" (en søgning: push første gang, siden replace, se newSearch).
  async function showList(url, { whole = true, history = null, keepScroll = false } = {}) {
    let got;
    try {
      got = await load(url);
    } catch (err) {
      return fallback(url, err);
    }
    const { doc } = got;
    const y = window.scrollY;
    document.querySelectorAll("body > .flash").forEach((el) => el.remove());
    if (whole && top() && doc.getElementById("overblik-top")) {
      const fresh = doc.getElementById("overblik-top");
      const was = top().querySelector("details.filtre"), now = fresh.querySelector("details.filtre");
      if (was && now) now.open = was.open; // "Filtre" bliver, som brugeren lod den
      top().replaceWith(fresh);
    } else if (top()) {
      const chips = doc.getElementById("barn-filter");
      const mine = document.getElementById("barn-filter");
      if (chips && mine) mine.replaceWith(chips);
    }
    main().replaceWith(mainOf(doc));
    document.title = doc.title;
    listUrl = url;
    if (history === "search") {
      history = typing ? "replace" : "push";
      typing = true;
      clearTimeout(pause);
      pause = setTimeout(newSearch, PAUSE_MS);
    }
    if (history === "push") window.history.pushState({ list: url }, "", url);
    if (history === "replace") window.history.replaceState({ list: url }, "", url);
    window.scrollTo(0, keepScroll ? y : 0);
  }

  // Søgefeltets URL: kun de felter, der har en værdi.
  function searchUrl(form) {
    const params = new URLSearchParams();
    for (const [k, v] of new FormData(form)) if (String(v).trim()) params.append(k, v);
    const q = params.toString();
    return form.getAttribute("action") + (q ? "?" + q : "");
  }

  // Én søgning = én post i historikken: det første resultat lægger sig dér, resten (mens man skriver) erstatter
  // det. En ny søgning begynder efter en pause, når søgefeltet mister fokus, og et filter-skift er sin egen post.
  let timer = null;
  let pause = null;
  let typing = false; // den søgning, man er i gang med, har sin post i historikken

  function newSearch() {
    clearTimeout(pause);
    typing = false;
  }

  function search(form, now = false, history = "search") {
    clearTimeout(timer);
    const go = () => {
      const url = searchUrl(form);
      if (!sameList(url, listUrl)) showList(url, { whole: false, history });
    };
    if (now) go();
    else timer = setTimeout(go, DEBOUNCE_MS);
  }

  document.addEventListener("input", (ev) => {
    const form = ev.target.closest && ev.target.closest("form#search");
    if (form && ev.target.matches("input[type=search]")) search(form);
  });
  document.addEventListener("change", (ev) => {
    const form = ev.target.closest && ev.target.closest("form#search");
    if (!form || !ev.target.matches("select")) return;
    newSearch();
    search(form, true, "push");
  });
  document.addEventListener("focusout", (ev) => {
    if (ev.target.id === "q") newSearch();
  });

  // ------------------------------------------------------------------ skuffen (detaljen på brede skærme)

  let drawer = null; // { el, body, full, opener }

  function makeDrawer() {
    const el = document.createElement("aside");
    el.id = "skuffe";
    el.setAttribute("aria-label", "Detalje");
    el.hidden = true;
    el.innerHTML =
      '<div class="skuffe-top"><a class="skuffe-fuld" href="#">Åbn som side</a>' +
      '<button type="button" class="skuffe-luk" aria-label="Luk (Esc)">✕ Luk</button></div>' +
      '<div class="skuffe-indhold" tabindex="-1"></div>';
    document.body.append(el);
    el.querySelector(".skuffe-luk").addEventListener("click", closeDrawer);
    return { el, body: el.querySelector(".skuffe-indhold"), full: el.querySelector(".skuffe-fuld"), opener: null };
  }

  const isOpen = () => drawer && !drawer.el.hidden;

  // Viser detaljen på ``url`` i skuffen; ``history``: "push" når den åbnes ved et klik.
  // ``opener``: linket den blev åbnet fra (fokus går tilbage dertil, når den lukkes).
  async function showDrawer(url, { history = null, focus = true, opener = null } = {}) {
    let got;
    try {
      got = await load(url);
    } catch (err) {
      return fallback(url, err);
    }
    drawer = drawer || makeDrawer();
    if (opener) drawer.opener = opener;
    drawer.body.replaceChildren(rebase(mainOf(got.doc), url));
    drawer.body.dataset.url = url;
    drawer.full.href = url;
    drawer.el.hidden = false;
    drawer.body.scrollTop = 0;
    document.documentElement.classList.add("skuffe-aaben");
    markChosen(url);
    if (history === "push") window.history.pushState({ drawer: url, list: listUrl }, "", url);
    if (focus) drawer.body.focus({ preventScroll: true });
  }

  function hideDrawer() {
    if (!drawer) return;
    drawer.el.hidden = true;
    drawer.body.replaceChildren();
    document.documentElement.classList.remove("skuffe-aaben");
    markChosen(null);
    let opener = drawer.opener;
    if (opener && !document.contains(opener)) {
      // listen er hentet igen imens (fx efter "Klaret"): samme link i den nye liste
      opener = [...main().querySelectorAll("a.item-link")].find((a) => a.href === drawer.opener.href);
    }
    if (opener) opener.focus({ preventScroll: true });
    drawer.opener = null;
  }

  // Luk: tilbage i historikken, når skuffen selv lagde sig der (så tilbage-knappen og Luk er det samme).
  function closeDrawer() {
    if (window.history.state && window.history.state.drawer) window.history.back();
    else hideDrawer();
  }

  function markChosen(url) {
    const path = url && new URL(url, location.href).pathname;
    document.querySelectorAll("li.valgt").forEach((li) => li.classList.remove("valgt"));
    if (!path) return;
    main().querySelectorAll("a[href]").forEach((a) => {
      if (new URL(a.href).pathname === path) a.closest("li")?.classList.add("valgt");
    });
  }

  function isDetail(a) {
    const u = new URL(a.href, location.href);
    return u.origin === location.origin && u.pathname.startsWith("/overblik/item/") && !u.pathname.includes("/fil/");
  }

  function isList(a) {
    const u = new URL(a.href, location.href);
    return u.origin === location.origin && u.pathname === "/overblik";
  }

  // ------------------------------------------------------------------ klik og formularer

  document.addEventListener("click", (ev) => {
    if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    const a = ev.target.closest && ev.target.closest("a[href]");
    if (!a || a.target || a.hasAttribute("download")) return;
    const inDrawer = drawer && drawer.el.contains(a);
    if (a.classList.contains("skuffe-fuld")) return; // "Åbn som side": helt almindeligt
    if (isDetail(a) && top() && (inDrawer || WIDE.matches)) {
      ev.preventDefault();
      showDrawer(a.href, { history: "push", opener: inDrawer ? null : a });
      return;
    }
    if (isList(a) && top() && !inDrawer) {
      ev.preventDefault();
      newSearch();
      if (isOpen()) hideDrawer();
      showList(a.href, { history: "push" });
    }
  });

  document.addEventListener("submit", (ev) => {
    const form = ev.target;
    if (form.id === "search" && top()) {
      ev.preventDefault();
      search(form, true);
      return;
    }
    const action = new URL(form.action, location.href);
    if (form.method.toLowerCase() !== "post" || !action.pathname.startsWith("/todos/")) return;
    ev.preventDefault();
    tick(form);
  });

  // "Klaret"/"Fortryd": send formularen, og vis det, siden sender tilbage til, der hvor knappen sad.
  async function tick(form) {
    const inDrawer = drawer && drawer.el.contains(form);
    const button = form.querySelector("button");
    if (button) button.disabled = true;
    let got;
    try {
      got = await load(form.action, {
        keep: true,
        fetch: { method: "POST", body: new URLSearchParams(new FormData(form)) },
      });
    } catch (err) {
      if (button) button.disabled = false;
      return form.submit();
    }
    if (inDrawer) {
      drawer.body.replaceChildren(rebase(mainOf(got.doc), drawer.body.dataset.url));
      showList(listUrl, { keepScroll: true });
    } else {
      document.querySelectorAll("body > .flash").forEach((el) => el.remove());
      main().replaceWith(mainOf(got.doc));
      markChosen(isOpen() ? drawer.body.dataset.url : null);
    }
  }

  // ------------------------------------------------------------------ tilbage og frem

  window.addEventListener("popstate", (ev) => {
    const state = ev.state || {};
    newSearch();
    if (state.drawer && top()) {
      if (state.list && !sameList(state.list, listUrl)) showList(state.list, { keepScroll: true });
      showDrawer(state.drawer, { focus: false });
      return;
    }
    if (isOpen()) hideDrawer();
    if (!sameList(location.href, listUrl)) showList(location.href);
  });

  // Skuffen giver kun mening på en bred skærm: bliver den smal, så luk den.
  WIDE.addEventListener("change", () => {
    if (!WIDE.matches && isOpen()) closeDrawer();
  });

  // ------------------------------------------------------------------ tastatur

  document.addEventListener("keydown", (ev) => {
    if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const t = ev.target;
    const editing = t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);
    if (ev.key === "/" && !editing) {
      ev.preventDefault();
      const q = document.getElementById("q");
      if (q) focusSearch(q);
      else window.location.assign("/overblik#q"); // detaljen som fuld side: søgefeltet er på overblikket
    } else if (ev.key === "Escape" && isOpen()) {
      ev.preventDefault();
      closeDrawer();
    }
  });

  function focusSearch(q) {
    q.focus();
    q.select();
  }

  // Kom man hertil med "/" fra detaljen (/overblik#q): fokus i søgefeltet, og adressen uden #q.
  if (location.hash === "#q" && document.getElementById("q")) {
    focusSearch(document.getElementById("q"));
    listUrl = location.href.split("#")[0];
  }
  window.history.replaceState({ ...(window.history.state || {}), list: listUrl }, "", listUrl);
})();
