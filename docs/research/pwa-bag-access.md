# PWA bag Cloudflare Access: service worker, offline og push

Hvad web-siden kan som PWA på hjemmeskærmen (iOS og Android), når den ligger bag Cloudflare Access, og hvilke
andre veje der er til at nå forælderens telefon. Det er grundlaget for **Offline-visning af web-siden** (#45) og
**Påmindelser om todos uden event** (#44). Ticket: #39.

Undersøgt 2026-11-06. Intet er prøvet på en rigtig telefon. Det, der kun kommer fra andres fejlrapporter eller er
ræsonneret frem, er markeret.

## Udgangspunkt i koden

- Starlette på `127.0.0.1:8787` bag en Cloudflare Tunnel og en Access-app. Access-sessionen er 30 dage (UI/UX-runden #34, punkt 18).
- `AccessMiddleware` (`src/aula_agent/web.py`) afviser **alle** requests uden en gyldig Access-JWT (header
  `Cf-Access-Jwt-Assertion` eller cookien `CF_Authorization`) for en e-mail på allowlisten med `403 Adgang nægtet`.
  POST kræver samme origin. Der er ingen offentlige stier.
- Sider sendes med `Cache-Control: no-store`, `/static/` med `no-cache` og filer med `private, no-store`. Overblikket har en CSP
  med `script-src 'self'` og intet inline script.
- Besluttet (#34, punkt 17): manifest og ikon, men ingen service worker og ingen offline-visning endnu.

## Kilder

| Kode | Kilde | Tillid |
|---|---|---|
| **[cf-cookie]** | Cloudflare: [Authorization cookie](https://developers.cloudflare.com/cloudflare-one/identity/authorization-cookie/) | Primær |
| **[cf-session]** | Cloudflare: [Session management](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/) | Primær |
| **[cf-policy]** | Cloudflare: [Access policies](https://developers.cloudflare.com/cloudflare-one/policies/access/) (Allow, Block, Bypass, Service Auth) | Primær |
| **[cf-paths]** | Cloudflare: [Application paths](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/) | Primær |
| **[html]** | WHATWG HTML: [link type `manifest`](https://html.spec.whatwg.org/multipage/links.html#link-type-manifest) og [CORS settings attribute credentials mode](https://html.spec.whatwg.org/multipage/urls-and-fetching.html#cors-settings-attribute-credentials-mode) | Primær (spec) |
| **[manifest]** | W3C: [Web Application Manifest](https://www.w3.org/TR/appmanifest/) | Primær (spec) |
| **[sw]** | W3C: [Service Workers](https://w3c.github.io/ServiceWorker/) (Update-algoritmen, Handle Fetch, Soft Update) | Primær (spec) |
| **[mdn-reg]** | MDN: [`ServiceWorkerContainer.register()`](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerContainer/register) | Primær (reference) |
| **[mdn-push]** | MDN: [Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API), [`PushManager.subscribe()`](https://developer.mozilla.org/en-US/docs/Web/API/PushManager/subscribe) | Primær (reference) |
| **[mdn-csd]** | MDN: [`Clear-Site-Data`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Clear-Site-Data) | Primær (reference) |
| **[wd-life]** | web.dev: [The service worker lifecycle](https://web.dev/articles/service-worker-lifecycle) | Primær (Chrome-teamet) |
| **[wd-push]** | web.dev: [Push notifications overview](https://web.dev/articles/push-notifications-overview), [Web Push Protocol](https://web.dev/articles/push-notifications-web-push-protocol) | Primær (Chrome-teamet) |
| **[workbox]** | Chrome: [Caching strategies overview](https://developer.chrome.com/docs/workbox/caching-strategies-overview) | Primær (Chrome-teamet) |
| **[wk-ios]** | WebKit: [Web Push for Web Apps on iOS and iPadOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/) (16.4) | Primær |
| **[wk-push]** | WebKit: [Meet Web Push](https://webkit.org/blog/12945/meet-web-push/) | Primær |
| **[wk-decl]** | WebKit: [Meet Declarative Web Push](https://webkit.org/blog/16535/meet-declarative-web-push/) (iOS 18.4) | Primær |
| **[wk-itp]** | WebKit: [Full Third-Party Cookie Blocking and More](https://webkit.org/blog/10218/full-third-party-cookie-blocking-and-more/) (7-dagesgrænsen) | Primær |
| **[wk-storage]** | WebKit: [Updates to Storage Policy](https://webkit.org/blog/14403/updates-to-storage-policy/) | Primær |
| **[apple-eu]** | Apple: [Update on apps distributed in the EU](https://developer.apple.com/support/dma-and-apps-in-the-eu/) (hjemmeskærms-apps i EU) | Primær |
| **[gcal]** | Google: [Calendar API, Events](https://developers.google.com/workspace/calendar/api/v3/reference/events) (`reminders`), [scopes](https://developers.google.com/workspace/calendar/api/auth) | Primær |
| **[gmail]** | Google: [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes) | Primær |
| **[ntfy]** | ntfy: [Configuration](https://docs.ntfy.sh/config/), [Publishing](https://docs.ntfy.sh/publish/), [FAQ](https://docs.ntfy.sh/faq/) | Primær |
| **[tg]** | Telegram: [FAQ](https://telegram.org/faq), [Bots](https://core.telegram.org/bots) | Primær |
| **[signal-cli]** | [AsamK/signal-cli](https://github.com/AsamK/signal-cli) README | Primær for værktøjet (uofficielt) |
| **[ff-2068]** | FlowFuse: [node-red-dashboard #2068](https://github.com/FlowFuse/node-red-dashboard/issues/2068) og [tråden på Node-RED-forummet](https://discourse.nodered.org/t/dashboard-2-0-pwa-service-worker-prevents-authentication-redirect-when-session-expires/100574) | Sekundær: fejlrapport fra en bruger (PWA på iPhone bag Access) |

## Kort svar

1. **Det farlige er en service worker, der svarer på navigationer fra sin cache.** Så kommer Access' redirect til
   login aldrig frem, og appen hænger, når sessionen er udløbet. Den kendte løsning er at slette appen fra
   hjemmeskærmen og lægge den på igen [ff-2068]. Sender man navigationer til nettet og kun bruger cachen, når
   nettet slet ikke svarer, virker login, som det gør i dag.
2. **Manifest og service worker-script bliver ikke låst fast.** Er sessionen udløbet, fejler hentningen, og den gamle
   worker kører videre [sw, wd-life]. Den nye kommer ved næste besøg med en gyldig session. Det eneste, man skal sørge
   for, er at ikonet er rigtigt første gang, og det kan kræve en Bypass for ikon og manifest (se 1.2).
3. **Web Push virker på begge platforme uden et hul i Access.** Serveren POSTer krypteret til browserens push-tjeneste
   (Apple, Google, Mozilla), og intet udefra skal nå appen [wd-push]. Abonnementet sendes fra siden til appen gennem
   Access. På iOS kræver det 16.4+, en app på hjemmeskærmen og et tryk fra brugeren [wk-ios]. Fra iOS 18.4 kan det
   gøres uden service worker (Declarative Web Push) [wk-decl].
4. **En service worker kun til push (uden `fetch`-handler) har ingen af offline-problemerne**, fordi den aldrig rører
   navigationer. Den kan komme før og uafhængigt af offline-visning.
5. **Alternativerne:**
   - e-mail (enklest, når begge forældre skal have den),
   - påmindelser i Google Calendar (er der allerede, men kun for events og kun for den, der ejer kalenderen),
   - ntfy (ntfy.sh ser indholdet, og en selv-hostet ntfy skal kunne nås uden om Access),
   - Telegram (let at lave, men Telegram kan læse indholdet),
   - Signal (krypteret, men kun via et uofficielt værktøj med sit eget telefonnummer).

## (a) Service worker, manifest og cache, når Access-sessionen er udløbet

### 1.1 Hvad Access gør

- Access tjekker hver request for `CF_Authorization` og blokerer den, hvis cookien mangler [cf-cookie]. Der er to tokens:
  en global session på team-domænet (`<team>.cloudflareaccess.com`, altid `HttpOnly`) og en app-token på appens domæne [cf-cookie].
- Er app-tokenet udløbet, men den globale session ikke, udsteder Access et nyt app-token. Ellers skal brugeren
  logge ind hos IdP'en igen [cf-session]. Begge dele sker for en browser som en kæde af redirects via team-domænet,
  altså **cross-origin**.
- For AJAX og SPA'er siger Cloudflare selv, at "sub-requests" kan blive blokeret med en udløbet token. Sender man headeren
  `X-Requested-With: XMLHttpRequest`, svarer Access **401** i stedet for at redirecte, så siden kan vise "sessionen er
  udløbet" eller genindlæse [cf-session]. `overblik.js` bruger allerede `fetch` til afkrydsning (#34, punkt 19), så det
  passer også i dag.
- Sessionen kan tilbagekaldes pr. app eller pr. bruger i Zero Trust [cf-session]. Det fjerner kun cookien hos
  Cloudflare og intet på telefonen (se 1.5).

### 1.2 Manifest og ikon

- Manifestet hentes med `mode: "cors"` og credentials mode efter `<link>`'s `crossorigin`-attribut [html]. Uden
  attributten er tilstanden *No CORS*, og den giver credentials mode `same-origin` [html]. **Et manifest på samme
  origin får altså cookien med**, og `crossorigin="use-credentials"` er ikke nødvendig her.
- Er sessionen udløbet, svarer Access med et redirect til team-domænet. Det er en CORS-request, og login-siden sender
  ingen `Access-Control-Allow-Origin` for appens origin, så hentningen fejler. *(Ræsonneret ud fra [html] og
  Fetch-reglerne. Cloudflare beskriver det ikke.)* Får man HTML i stedet for JSON, regnes manifestet ikke for hentet [html].
- Et installeret app henter manifestet ved hver sideindlæsning [manifest]. Fejler det, opdateres manifestet bare ikke.
  Manifestet bliver ikke "låst fast" med login-siden, men en installation, der sker med en udløbet session, kan få
  forkert navn og ikon.
- **Kun rapporteret, ikke verificeret:** iOS henter ikoner (og `apple-touch-icon`) uden cookies og beholder det ikon,
  den så første gang. Bag Access giver det login-sidens ikon eller intet. Andre projekter løser det med en Bypass-app i Access for
  de præcise stier til ikon og manifest (fx en
  [DZone-artikel](https://dzone.com/articles/ios-pwa-offline-caching)). Det skal prøves på en iPhone, før man åbner noget.
- Bypass for en sti: en Access-app for en smallere sti vinder over den brede og arver ingen regler fra den [cf-paths].
  Bypass "does not enforce any Access security controls and requests are not logged", og Cloudflare anbefaler ikke
  Bypass til varig direkte adgang til interne apps [cf-policy]. Wildcards dækker ikke forældrestien, og query strings
  kan ikke matches [cf-paths].
- Følger man den vej, skal `AccessMiddleware` også slippe de samme præcise stier igennem, ellers svarer appen selv 403.
  Indholdet må ikke være privat: app-navnet er som standard "Overblik", og ikonet er generisk, så det er OK.

### 1.3 Registrering og opdatering af service workeren

- Scriptet skal ligge på samme origin og have en JavaScript-MIME-type. Scope er som standard scriptets mappe.
  Skal workeren styre `/`, skal den ligge i roden eller have headeren `Service-Worker-Allowed` [mdn-reg, sw]. `/static/sw.js`
  dækker altså kun `/static/`, så den skal serveres fra fx `/sw.js`.
- Ved opdatering sætter Update-algoritmen `redirect mode` til **`"error"`** for hovedscriptet [sw]. Med en udløbet
  session fejler opdateringen, og registreringen bevares. Den fjernes kun, hvis der aldrig har været en worker [sw].
  web.dev siger det samme: "the new worker is thrown away, but the current one remains active" [wd-life].
- Opdatering sker ved hver navigation i scope og ved push o.l., hvis der ikke er tjekket i 24 timer [sw, wd-life].
  Standard er `updateViaCache: "imports"`, så hovedscriptet hentes uden om HTTP-cachen [sw, mdn-reg].
- Konsekvens: service workeren kan godt blive bag Access. Når sessionen er udløbet, kører den gamle bare videre.
  Opdateringen kommer, når brugeren er logget ind igen. Det kræver dog, at workeren lader login-redirectet komme
  igennem (1.4), ellers bliver brugeren aldrig logget ind igen.

### 1.4 Fælden: navigationer fra cachen

- **Kendt fejl** [ff-2068]: Node-RED Dashboard 2.0 bag Access med Google som IdP, installeret på en iPhone. Workbox'
  `NavigationRoute` svarede alle navigationer med en cachet `index.html`. Så nåede ingen navigation nettet, og Access
  fik aldrig lov at redirecte til login. API-kaldene fejlede ("the cross-origin redirect causes the fetch to fail"), og appen
  viste en fejl, også efter genindlæsning. Eneste udvej var at slette appen fra hjemmeskærmen og lægge den på igen.
  Rettelsen var at tage workeren helt ud af navigationerne. Maintaineren kaldte det at slå workeren fra "the only reliable fix".
- Hvorfor det virker at lade dem passere: navigationer har `redirect: "manual"`. En worker, der svarer med
  `fetch(event.request)`, giver et `opaqueredirect`-svar videre, og browseren følger det selv til login
  ([Fetch-spec](https://fetch.spec.whatwg.org/), opsummeret af [MDN](https://developer.mozilla.org/en-US/docs/Web/API/Request/redirect)).
  Sideløbende henter `fetch()` med `redirect: "follow"` som standard, så et cross-origin-redirect fra Access bliver en
  netværksfejl i JavaScript.
- Cache API'et er helt adskilt fra HTTP-cachen, og `Cache-Control` påvirker ikke, hvad workeren gemmer [workbox].
  **`no-store` på siderne beskytter altså ikke mod, at en service worker gemmer dem.** Det må workerens egen kode afgøre.
  Workbox-eksemplerne gemmer svaret uden at tjekke status [workbox].

**Regler, hvis der kommer offline-visning (#45):**

1. Navigationer går **altid først til nettet** (network-first) [workbox]. Cachen bruges kun, når `fetch` kaster en
   fejl (intet net). Den bruges aldrig, når svaret er et redirect, `opaqueredirect`, 401 eller 403.
2. Gem kun svar med `type === "basic"`, `status === 200` og `redirected === false`. Alt andet er login eller en fejl.
3. Rør aldrig `/cdn-cgi/` (Access' login-callback og logout) og aldrig POST (`/todos/…`, `/events/remove`, `/aula/login`, `/google…`).
4. `fetch`-kald fra siden sender `X-Requested-With: XMLHttpRequest`, så en udløbet session giver 401 og ikke et
   redirect [cf-session]. Siden viser så "Log ind igen" med et link til den aktuelle side (en almindelig navigation, som
   Access kan redirecte).

### 1.5 Forældede eller andres data

- Cachen hører til origin og browserprofil, ikke til Access-identiteten. Logger en anden forælder ind på samme telefon,
  ser vedkommende den første forælders cache, indtil den overskrives. *(Ræsonneret.)* Begge forældre ser dog de samme
  data i aula-agent i dag. Det er først et problem med flere familier.
- At tilbagekalde Access-sessionen (en mistet telefon) sletter intet på telefonen. Den cachede forside kan stadig ses
  offline af den, der har telefonen ulåst. *(Ræsonneret ud fra [cf-session] og [workbox].)*
- Værktøjer:
  - `Clear-Site-Data: "cache", "storage"` (eller `"*"`) på et svar sletter HTTP-cachen og afregistrerer service workers.
    MDN siger ikke klart, om Cache API'et også ryddes, og anbefaler at slette det selv med `caches.delete()` [mdn-csd].
    Det virker kun, når telefonen kommer online og når appen igen.
  - Workeren kan slette cachen, når den er ældre end N dage, og når den ser et 401 eller redirect.
  - Cachens navn kan bindes til e-mailen fra Access, som siden kender (`request.state.email`).
- iOS: apps på hjemmeskærmen tæller selv deres brugsdage. WebKit forventer ikke, at deres data slettes efter
  7-dagesgrænsen, og vil kalde det en alvorlig fejl, hvis det sker [wk-itp]. Kvoten er den samme som i browseren, og
  `navigator.storage.persist()` gives bl.a., når siden kører som hjemmeskærms-app [wk-storage].
- EU: Apple ville fjerne hjemmeskærms-apps i EU med iOS 17.4 (DMA), men trak det tilbage. De virker fortsat i EU, bygget på WebKit [apple-eu].

### 1.6 Åbent (skal prøves på en telefon)

- Om Access-login (redirect til team-domænet og IdP'en) i en iOS-app på hjemmeskærmen åbner inde i appen eller i
  Safari, og om `CF_Authorization` deles med Safari eller ej. Kilderne ovenfor siger det ikke. En åben
  [issue](https://github.com/traxon99/jackson-os/issues/14) i et andet projekt stiller det samme spørgsmål.
- Om ikonet faktisk hentes uden cookies på den iOS-version, familien har (1.2).

## (b) Web Push på iOS og Android

### 2.1 Hvordan det virker

- Siden abonnerer med `pushManager.subscribe({userVisibleOnly: true, applicationServerKey})`. `applicationServerKey` er en
  offentlig ECDSA P-256-nøgle (**VAPID**, RFC 8292), og alle beskeder skal så signeres med den private nøgle.
  Chrome og Edge kræver den [mdn-push]. Kræver HTTPS og et tryk fra brugeren [mdn-push, wd-push].
- Abonnementet indeholder en `endpoint`-URL hos browserens push-tjeneste og nøglerne til kryptering. Endpointet er en
  "capability URL" og skal holdes hemmeligt [mdn-push]. Browseren vælger tjenesten, og man har ingen kontrol over den
  [wd-push]. I Chrome er det typisk `fcm.googleapis.com`, og Apple bruger `*.push.apple.com` [wd-push, wk-push].
- Serveren sender med en POST til endpointet (signeret JWT i en header). Indholdet er krypteret, så "a push service
  can't read the data". `TTL` er påkrævet og afgør, hvor længe tjenesten holder på beskeden, hvis telefonen er offline
  [wd-push]. Tjenesten kan altså ikke læse teksten, men ser metadata (tidspunkt, størrelse, hvilket endpoint).
- **Retningen:** alt går fra appens server og ud. Svaret kommer som HTTP-svaret på POST'en [wd-push]. Intet skal nå
  appen uden om Access:
  - Abonnementet POSTes fra siden til appen gennem Access (samme origin, så `AccessMiddleware` tillader det).
  - Natkørslen eller web-tjenesten POSTer ud til Apple eller Google. Maskinen skal bare have udgående HTTPS, og hos
    Apple skal `*.push.apple.com` være tilladt [wk-ios].
  - Et tryk på notifikationen er en navigation. Er sessionen udløbet, sender Access til login som normalt, så længe
    workeren ikke svarer på navigationer (1.4).
- En push-handler kan ikke regne med at hente noget fra appen, fordi sessionen kan være udløbet. Teksten skal derfor stå i
  selve beskeden. Den vises på låseskærmen, så den bør være kort og uden navne (samme regel som `todo.text`).

### 2.2 iOS og iPadOS

- Web Push fra **iOS/iPadOS 16.4**, kun for **apps på hjemmeskærmen**. Manifestet skal have `display: standalone` eller
  `fullscreen`. Tilladelsen kan kun bedes om efter et tryk fra brugeren (fx en "Slå påmindelser til"-knap), og den styres
  pr. app i Indstillinger → Notifikationer [wk-ios].
- Bygger på APNs. Man skal **ikke** være med i Apple Developer Program [wk-ios, wk-push].
- Hver push skal vise en notifikation. Ellers tilbagekalder Safari abonnementet ("not an invitation for silent background
  runtime") [wk-push].
- Badging (`setAppBadge`) på hjemmeskærms-ikonet, fx antal åbne todos, også mens en push behandles [wk-ios].
  Notifikationerne følger Fokus-tilstande [wk-ios].
- **Declarative Web Push** (iOS/iPadOS 18.4, macOS 15.5) [wk-decl]:
  - Kræver **ingen service worker**: `window.pushManager.subscribe(...)` direkte fra siden.
  - Beskeden er JSON med `"web_push": 8030` og et `notification`-objekt med `title` og `navigate` (påkrævet) samt fx
    `body`, `lang` og `app_badge`. Sendes som almindelig Web Push.
  - Det er robust mod, at ITP sletter service workers, og en worker kan ikke miste abonnementet ved at glemme at vise
    noget.
  - Er der en service worker, får den stadig et `push`-event og kan erstatte notifikationen. Viser den intet, bruges
    beskedens egen.
  - Indtil videre kun WebKit. Andre browsere læser det som en almindelig push (bagudkompatibelt).

### 2.3 Android

- Chrome bruger sin egen push-tjeneste (FCM). En worker kan vise notifikationer, selv når siden eller browseren er
  lukket, og tjenesten holder beskeden, til telefonen er online [wd-push]. Kravet om at ligge på hjemmeskærmen er
  Apples. På Android virker push også fra en almindelig fane. *(Ikke fundet ordret i kilderne ovenfor; det står ikke i
  web.dev-artiklen.)*
- Declarative Web Push findes ikke uden for WebKit [wk-decl], så Android kræver en service worker med en `push`-handler.

### 2.4 Hvad det kræver i aula-agent

- Et VAPID-nøglepar i `data/` og en tabel med abonnementer pr. enhed i `ledger` eller arkivet. Fjern abonnementet, når
  tjenesten svarer, at det er væk (404/410).
- `/sw.js` i roden med kun `push`- og `notificationclick`-handlere og **ingen `fetch`-handler**. Den skal sende samme
  JSON i deklarativ form, så iOS 18.4+ kan klare sig uden.
- En knap under Indstillinger ("Påmindelser på denne enhed").
- Et bibliotek til at sende (RFC 8291-kryptering og VAPID). Biblioteket er ikke undersøgt.
- CSP: `script-src 'self'` dækker et worker-script fra samme origin. *(Ikke undersøgt i detaljer; `worker-src` falder
  tilbage til `script-src`.)*

## (c) Alternativer til push på telefonen

| Kanal | Hvad det kræver | Privatliv | Fordele | Ulemper |
|---|---|---|---|---|
| **Web Push** (2) | VAPID, service worker eller deklarativ, iOS 16.4+ på hjemmeskærmen, et opt-in pr. enhed | Krypteret til enheden. Apple/Google ser kun metadata [wd-push] | I appen, intet nyt login, intet hul i Access. Trykket åbner todo'en | Hver forælder skal installere og slå det til på hver telefon. Svært at fejlsøge |
| **ntfy.sh** | ntfy-appen, et hemmeligt topic | Topic'et er adgangskoden. ntfy.sh logger topic-navne og IP'er og gemmer beskeder i 12 t. Til Android sendes alt også via FCM. "If … your messages are sensitive, run your own server" [ntfy FAQ] | Meget enkelt (`curl -d … ntfy.sh/<topic>`), planlagt levering op til 3 dage frem (`Delay`), klik-URL og knapper [ntfy publish] | Tredjepart kan læse teksten. Kræver en app mere |
| **ntfy selv-hostet** | En ntfy-server mere, `auth-default-access: deny-all` og tokens, `behind-proxy` [ntfy config] | Bliver hjemme. Android: direkte forbindelse uden Google (0–1 % batteri på 17 t) [ntfy FAQ]. iOS: serveren sender kun besked-id og SHA256 af topic-URL'en videre til ntfy.sh/APNs, og telefonen henter selve beskeden hjemmefra [ntfy config] | Alt er i egne hænder | **Skal kunne nås uden om Access** (ntfy-appen kan ikke logge ind i Access), så den skal have sit eget hostname med ntfy's egen auth. iOS-appen er "very bare bones and … a little buggy" [ntfy FAQ]. En tjeneste mere at drive |
| **E-mail** (fx morgenbrev) | SMTP, eller Gmail API med `gmail.send`, et *sensitive* scope, der kræver verifikation for en udgivet app [gmail] | Indholdet ligger i postkassen hos mailudbyderen | Når begge forældre uden installation. Godt til en samlet besked om morgenen og overskredne todos | Ikke "på dagen"-præcist. Drukner let. Et nyt scope eller en SMTP-konto |
| **Google Calendar-påmindelser** | Findes: `reminders.overrides` med `popup` eller `email`, højst 5, 0–40320 min før [gcal] | Google har det allerede | Ingen ny kanal. Kalenderens egne notifikationer | Kun for noget med en dato. Påmindelser er "for the authenticated user", altså den konto, der ejer kalenderen. En anden forælder, der deler kalenderen, får sine egne standarder [gcal]. En todo uden `due` passer ikke. "Todo: …"-poster blander *gøre* ind i *hvornår* (#34, punkt 4), og at krydse af i kalenderen når ikke tilbage |
| **Telegram-bot** | Telegram-konto, Bot API (HTTPS). Botten kan hente svar med long polling, altså kun udgående | Bot-chats er cloud chats: krypteret mellem klient og server, ikke end-to-end, og gemt hos Telegram [tg] | Let at lave, knapper og svar uden et hul i Access (fx "Klaret") | Telegram kan læse alt. En konto mere for begge forældre |
| **Signal** | `signal-cli`: uofficielt, kræver sit eget telefonnummer eller en tilknyttet enhed og skal holdes opdateret, fordi serveren ændrer sig [signal-cli] | End-to-end-krypteret | Bedst privatliv af beskedtjenesterne | Ingen officiel bot-API. Skrøbeligt at drive |

## Hvad det betyder

### For "Offline-visning af web-siden" (#45)

- Det kan lade sig gøre bag Access, men kun med en service worker, der **aldrig svarer på en navigation, når nettet
  svarer**. Ellers låser den brugeren ude, når de 30 dage er gået (1.4). Reglerne i 1.4 er minimum.
- `no-store` på siderne hjælper ikke. Workeren afgør selv, hvad der gemmes (1.4).
- Offline bør være **skrivebeskyttet**. Afkrydsning og "Fjern for <barn>" er POSTs gennem Access, og en kø til
  senere ville sende dem med en session, der måske er udløbet. Vis tydeligt "Hentet <tidspunkt>, uden net".
- En mistet eller udlånt telefon: Access kan tilbagekaldes, men det rydder ikke cachen (1.5). Den eneste beskyttelse er
  at gemme lidt (fx forsiden og ikke filerne), lade cachen udløbe efter få dage, rydde den ved 401 og ved skift af
  e-mail, og `Clear-Site-Data` næste gang telefonen er online.
- Ikon og manifest skal måske have en snæver Bypass i Access og i `AccessMiddleware`. Prøv først på en iPhone (1.2, 1.6).

### For "Påmindelser om todos uden event" (#44)

- **Web Push kræver ikke noget uden om Access** (2.1) og ingen offline-cache: en worker kun til push (eller
  Declarative Web Push på iOS 18.4+) kan bygges uden at åbne for offline-problemerne.
- Prisen er, at hver forælder skal lægge siden på hjemmeskærmen og slå det til på hver enhed (iOS), og at teksten står
  i beskeden, fordi workeren ikke kan regne med en gyldig session.
- **E-mail** er den nemmeste kanal, når begge forældre skal have den, og den passer til en samlet besked om morgenen,
  også med todos uden `due`.
- **Google Calendar-påmindelser** dækker kun todos med en dato og kun kalenderens ejer. Det er ikke en løsning for
  todos uden event.
- **ntfy** er godt, hvis man vil undgå Apple og Google. Men ntfy.sh kan læse indholdet, og en selv-hostet server skal
  have et hostname uden om Access. Telegram og Signal er mere arbejde eller dårligere privatliv end alternativerne.
