# Hvad findes der allerede, der henter fra Aula?

Research til [#38](https://github.com/fMads/aula-agent/issues/38) på kortet [#37](https://github.com/fMads/aula-agent/issues/37).
Her står, hvilke værktøjer der findes, hvem der bruger dem, hvordan de logger ind, hvad brugerne beder om, og hvad
værktøjerne hedder. Det skal bruges til rod-ticket'en ("Hvem er aula-agent til, og hvem installerer det?") og til en
senere navne-ticket.

Undersøgt 2026-11-06. Tallene (stjerner, downloads, installationer) er fra den dag og ændrer sig hurtigt. Mange af
projekterne er kun få uger eller måneder gamle.

## Kilder

| Kilde | Hvad | Tillid |
|---|---|---|
| **[gh]** GitHub API via `gh` (`gh api repos/<repo>`, README'er, issues, releases, forks) | Hvad projekterne gør, stjerner, aktivitet, login, ønsker i issues | Primær |
| **[pypi]** [pypi.org/pypi/aula/json](https://pypi.org/pypi/aula/json) og [pypistats.org/api/packages/aula/recent](https://pypistats.org/api/packages/aula/recent) | Versioner og downloads af `aula` | Primær |
| **[hacs]** [hacs/default `integration`](https://raw.githubusercontent.com/hacs/default/master/integration) | Hvilke integrationer der står i HACS' standardliste | Primær |
| **[ha]** [analytics.home-assistant.io/custom_integrations.json](https://analytics.home-assistant.io/custom_integrations.json) | Installationer af custom integrations. Tæller kun HA-brugere, der har slået analytics til, så de rigtige tal er højere | Primær, men et minimum |
| **[npm]** [api.npmjs.org](https://api.npmjs.org/downloads/point/last-month/@ilenhart/aula-mcp-server) | Downloads af en npm-pakke | Primær |
| **[web]** Produkternes egne sider: [familyplanen.dk](https://familyplanen.dk/), [aula-assistent.dk](https://aula-assistent.dk/), [daysi.dk/hent-aftaler-aula](https://daysi.dk/hent-aftaler-aula/), [rpaasch.github.io/AulaSync](https://rpaasch.github.io/AulaSync/) | Hvad de kommercielle tjenester lover, pris, login | Primær (egne udsagn) |
| **[stores]** [App Store](https://apps.apple.com/dk/app/aula/id1330998651), [Google Play](https://play.google.com/store/apps/details?id=com.netcompany.aulanativeprivate&hl=da&gl=DK) | Den officielle apps bedømmelse og downloads | Primær |
| **[stil]** [STIL: Login med Unilogin-brugernavn udfases for forældre](https://viden.stil.dk/spaces/STILVIDENOFFENTLIG/blog/2025/08/28/292356141/Login+med+Unilogin+brugernavn+udfases+for+for%C3%A6ldre) | Hvorfor alle værktøjer nu skal bruge MitID | Primær |
| **[aarhus]** [Aarhus Kommune: trin-for-trin-vejledning til kalendersynkronisering (PDF)](https://aarhus.dk/media/oxppoeeu/trin-for-trin-vejledning-kalendersynkronisering.pdf) | Aulas egen iCal-eksport | Primær (kommunens vejledning i Aula) |
| **[dr]** [DR: "Snart truer hverdagen: Sådan AI-hacker du familiens logistikgyser"](https://www.dr.dk/nyheder/indland/snart-truer-hverdagen-saadan-ai-hacker-du-familiens-logistikgyser) (9. aug.) | Hvordan pressen ser på at lade en AI læse Aula | Sekundær |
| **[aa]** [agentaccess.dk/sites/aula/browser/aula](https://agentaccess.dk/sites/aula/browser/aula) | Et katalog over, hvordan AI-agenter kan bruge danske tjenester | Sekundær |

Ikke undersøgt: Discord-kanalen for `scaarup/aula` og lukkede Facebook-grupper (kræver login). Der er heller ikke
søgt i Chrome Web Store eller efter userscripts. En websøgning fandt ingen browser-udvidelse til Aula.

## Overblik

| Værktøj | Slags | Hvad det henter | Login | Brug og aktivitet (2026-11-06) |
|---|---|---|---|---|
| [scaarup/aula](https://github.com/scaarup/aula) "Aula" | Home Assistant (HACS standard) | Skema som kalender, samtaler, ugeplaner (Min Uddannelse, Meebook, EasyIQ, Docendo), MU-opgaver, Huskelisten, ulæste beskeder som sensor | MitID (app/QR eller kodeviser) i HA's opsætning | 123 ★, 41 forks, 126 issues, ~24.000 release-downloads, ≥ 347 HA-installationer [ha]. Ny release 4/11 2026. Siden 2022 |
| [nickknissen/aula](https://github.com/nickknissen/aula) (`aula` på PyPI) | Python-bibliotek + CLI + agent-skill | Næsten hele forælder-API'et (se `aula-api.md`) | MitID (app/QR eller kodeviser), refresh-token gemt lokalt | 19 ★, 7 forks, 33 releases siden feb. 2026 (1.15.0 4/11), ~1.700 downloads/md. [pypi]. Det er det, aula-agent bruger |
| [nickknissen/hass-aula](https://github.com/nickknissen/hass-aula) "Aula for Home Assistant" | Home Assistant (HACS standard) | Fremmøde, skolekalender, notifikationer som HA-events, sætte afhentningstid | Via `aula` | 3 ★, ≥ 10 HA-installationer [ha]. Siden feb. 2026 |
| [Casperjuel/aula-mcp](https://github.com/Casperjuel/aula-mcp) | MCP-server (TypeScript/Bun) | Profiler, kalender, beskeder, ugeplaner, fremmøde → Claude, Cursor, ChatGPT osv. | Egen MitID-implementering uden browser, tokens i Keychain eller krypteret fil | 65 ★, 32 forks, 22 åbne issues. Siden maj 2026. Det mest populære nye projekt |
| [gyusza/ha-aula-ui](https://github.com/gyusza/ha-aula-ui) "Aula UI for Home Assistant" | Home Assistant-add-on | Feed med opslag, beskeder, album og fremmøde, AI-resuméer og -oversættelse, **AI-foreslåede events fra opslag og beskeder, som man godkender**, privat ICS, push, MCP | Indbygget MitID (app) | 0 ★, oprettet 30/10 2026, add-on v1.9.62 |
| [piyushpalod/aula-buddy](https://github.com/piyushpalod/aula-buddy) "Aula Buddy" | Selvhostet bot (TypeScript) | Nye beskeder og opslag oversat og opsummeret med Claude, todos og frister, aftenbriefing → Telegram | Via `aula` | 0 ★, oktober 2026. Til internationale forældre |
| [filipbech/aula-notify](https://github.com/filipbech/aula-notify) | Selvhostet poller | Klassificerer beskeder, opslag og notifikationer efter hast med en LLM (Gemini) → e-mail nu eller i et dagligt digest | Via `aula-mcp` som bibliotek | 1 ★, sept. 2026 |
| [chrsoerup/instant-aula](https://github.com/chrsoerup/instant-aula) | HA-add-on (Python) | Ugentligt digest (ugeplan + kalender) og straks-besked om nye beskeder og vigtige opslag → HA-push | Via `aula` | 0 ★, aug. 2026 |
| [thrugz/aula-mcp](https://github.com/thrugz/aula-mcp), [miklen/aula_mcp](https://github.com/miklen/aula_mcp), [ilenhart/aula-mcp-server](https://github.com/ilenhart/aula-mcp-server), [madslundt/aula-mcp-ha](https://github.com/madslundt/aula-mcp-ha), [fhvilshoj/aula-mcp](https://github.com/fhvilshoj/aula-mcp) | Flere MCP-servere | Det samme som `aula-mcp` med varianter (navne på børn, HA, Docker) | MitID over HTTP (thrugz, porteret fra Casperjuel) eller Chrome-automatisering (ilenhart) | 0–2 ★ hver. ilenhart: 29 npm-downloads sidste måned [npm] |
| [HairingX/aula_connect](https://github.com/HairingX/aula_connect) + [ha_aula_connect](https://github.com/HairingX/ha_aula_connect) | Python-bibliotek + HA-integration | Kalender, beskeder, fremmøde, ugeplaner, nyhedsbreve | Egen MitID-klient ([mitid_connect](https://github.com/HairingX/mitid_connect)) | 0 ★, oktober 2026 |
| [eisbaw/aulalibre](https://github.com/eisbaw/aulalibre) "Aula Reversed" | Rust-bibliotek, CLI og FUSE-filsystem | Opslag, beskeder, kalender, galleri, dokumenter som mapper | OIDC i browseren, man indsætter redirect-URL'en | 54 ★, sidst ændret april 2026. Har dekompileret mobilappen |
| [elnigno/aula-dk-picture-download](https://github.com/elnigno/aula-dk-picture-download), [givtrah/aula-gallery-fetch](https://github.com/givtrah/aula-gallery-fetch) (arkiveret) | Scripts | Billeder fra album, opslag og beskeder | Session-cookie fra browseren | 11 ★ / 0 ★ |
| [Dokport/nodered-aula-messages](https://github.com/Dokport/nodered-aula-messages) | Node-RED-flow | Beskedtråde → MQTT | Låner `scaarup/aula`'s session i HA | 0 ★ |
| [rpaasch/AulaSync](https://github.com/rpaasch/AulaSync) "AulaSync" | Desktop-app (Windows/Mac) | **Skemaer** for ansatte, klasser og lokaler → Apple Kalender og Outlook. Beskeder er fjernet i 3.x | "Log ind på Aula, som du plejer" | 0 ★. Til skolens personale, ikke forældre |
| [Familyplanen](https://familyplanen.dk/) | Kommerciel web-app | Familiekalender med AI-post-its (Claude) ud fra Aula-mails, SMS'er og noter, madplan, rutiner | **Intet MitID**: Aulas kalenderlink + man videresender Aulas e-mails | 59 kr./md. (gratis til 1/2 2027). Antal brugere ikke oplyst |
| [Aula-Assistent](https://aula-assistent.dk/) | Kommerciel tjeneste (RexifyIT ApS) | Aula-data i ChatGPT og Claude ("Hvad skal jeg huske til børnene i morgen?") med en "Privacy Firewall" | Ikke oplyst. Firmaet har en fork af `nickknissen/aula` ([RexifyIT/aula-fixes](https://github.com/RexifyIT/aula-fixes)) | 39 kr./md. eller 234 kr./år. Antal brugere ikke oplyst |
| [Daysi](https://daysi.dk/hent-aftaler-aula/) | Kommerciel familiekalender | Aulas kalenderbegivenheder | Aulas kalenderlink (iCal) | Premium 29 kr./md. Siger selv "over 5.000 danske familier" |
| Aulas egen **kalendersynkronisering** | Indbygget i Aula | Kalenderbegivenheder, skema og samtaler som abonnement (årskalender + ugekalender) til Outlook eller Google | Ingen: et hemmeligt link | Alle forældre har den [aarhus] |
| Den officielle app "Aula for forældre og elever" (Netcompany) | App | Alt i Aula | MitID | Google Play: 500.000+ downloads, 2,8 ★ (18.100 anmeldelser). App Store: 3,3 ★ [stores] |

## De enkelte grupper

### 1. Home Assistant: den største og ældste brugerflade

- **`scaarup/aula` er det, de fleste bruger.** Den står i HACS' standardliste [hacs], har mindst 347 installationer i
  HA's analytics [ha] og omkring 24.000 downloads af releases i alt [gh]. Det er det eneste projekt med et fællesskab
  (Discord, 126 issues, flere tråde med 30–100 kommentarer).
- Den viser **skema, ugeplaner og opgaver** og en sensor for ulæste beskeder. Den læser **ikke** opslag og laver
  ikke arrangementer ud fra beskeders indhold [gh README].
- **`nickknissen/hass-aula`** findes, fordi forfatteren ville have login og API i et selvstændigt bibliotek (`aula`)
  frem for at omskrive `scaarup/aula`: "if it covers what you need, use it" [gh README]. Det har fokus på fremmøde
  og notifikationer.
- **Add-ons** (`ha-aula-ui`, `instant-aula`) bruger HA som platform til en hel lille app: feed, push og AI.
  `ha-aula-ui` er det projekt, der ligner aula-agent mest. Det foreslår events fra opslag og beskeder med AI,
  brugeren godkender dem mod originalen, og de udgives som privat ICS. Det kræver Home Assistant OS eller
  Supervised [gh README].

### 2. Biblioteker

- **`aula` (`nickknissen/aula`)** er det aktive Python-bibliotek: 33 releases på ni måneder, egen MitID-login (app
  eller kodeviser) og en indbygget "AI agent skill" til Claude Code og OpenCode [gh README, pypi]. Det bruges af
  `hass-aula`, `instant-aula`, `aula-buddy` og aula-agent. Der er 7 forks, bl.a. `RexifyIT/aula-fixes`.
- **`Casperjuel/aula-mcp`** er også et TypeScript-bibliotek; `aula-notify` bruger den sådan.
- **`aula_connect`** (Python) og **`aulalibre`** (Rust) er uafhængige implementeringer.
- **Ældre:** `morten-olsen/aula-client` (2021) og `Akademiaapp/aula-api` (2024) er døde. Før MitID brugte de fleste
  Unilogin-brugernavn og adgangskode.

### 3. MCP-servere og AI-assistenter: den nye bølge (2026)

- Mindst seks MCP-servere er lavet i 2026. `Casperjuel/aula-mcp` er størst (65 ★, 32 forks på seks måneder) [gh].
  De giver en AI-klient (Claude, ChatGPT, Cursor, lokale modeller) adgang til at **spørge** Aula.
- De advarer alle om privatliv: "Det er klienten der får dataen at se — ikke serveren" og "Hobby-eksperiment,
  ingen garantier" (`Casperjuel/aula-mcp` README).
- `thrugz/aula-mcp` skriver, at MitID-login skal ske fra en hjemmeforbindelse: "STIL's bot protection blocks it from
  datacenter IPs". Refresh virker overalt, så man kan logge ind hjemme og kopiere tokens til en server [gh README].
  Det stemmer med aula-agents egen erfaring (dansk IP).
- **Notifikation og digest:** `aula-notify`, `instant-aula` og `aula-buddy` har samme udgangspunkt: Aulas egne
  notifikationer er "noisy and easy to miss" (`aula-notify`), og man vil kun forstyrres af det vigtige.
  `aula-buddy` er til internationale forældre (oversættelse, svar på dansk) og erstatter børnenes navne med
  pladsholdere, før teksten sendes til AI'en [gh README].

### 4. Kommercielle tjenester

- **Familyplanen** gør næsten det samme som aula-agents kerne: en AI (Claude) laver korte påmindelser ud fra
  Aula-beskeder, "sætter den på det rigtige barn og regner ud, hvilken dag 'i morgen' og 'lørdag' er". Den undgår
  MitID helt: "Familyplanen bruger Aulas egen kalenderdeling og de mails, Aula sender til dig". Den har en grænse
  på 20 beskeder om dagen pr. familie og sletter beskeden efter 30 dage [web]. Siden nævner intet firmanavn eller CVR.
- **Aula-Assistent** (RexifyIT ApS) sælger Aula-adgang i ChatGPT og Claude som abonnement. Siden siger ikke, hvordan
  den logger ind [web].
- **Daysi** importerer kun Aulas kalender via iCal-linket [web].
- Ingen af dem har et arkiv, og de to første er hostede tjenester. Indholdet passerer altså en fremmed server.

### 5. Aula selv

- Aula har **intet officielt API** for forældre [aa, alle README'er]. Alle værktøjer ovenfor bruger det samme
  interne API som web-siden og appen, og README'erne advarer om, at det kan ændre sig uden varsel.
- **Kalendersynkronisering** er Aulas eneste udgang. Forælderen vælger typer af begivenheder og får to links
  (årskalender og ugekalender), der abonneres på i Outlook eller Google. Det er envejs [aarhus]. Det dækker kun det,
  der står i Aula-kalenderen, ikke datoer i beskeder og opslag.
- **Login:** Siden 10. september 2025 kan forældre kun logge ind med MitID. Unilogin-brugernavn og adgangskode er
  kun for elever [stil]. Det brød alle ældre værktøjer: issue
  [scaarup/aula#257 "Unilogin phased out, options?"](https://github.com/scaarup/aula/issues/257) (68 kommentarer)
  endte med, at fællesskabet selv lavede MitID-login ud fra
  [Hundter/MitID-BrowserClient](https://github.com/Hundter/MitID-BrowserClient) og refresh-tokens.
- **Den officielle app** har lave bedømmelser (2,8 på Google Play, 3,3 i App Store) [stores].

## Hvordan de logger ind

| Måde | Hvem | Konsekvens |
|---|---|---|
| Egen MitID-implementering (QR i app eller kodeviser) + refresh-token | `aula`, `scaarup/aula`, `aula-mcp` (Casperjuel, thrugz), `aula_connect`, `ha-aula-ui` | Én godkendelse i MitID-appen, derefter kører det i uger. Går i stykker, når MitID eller Aulas login ændrer sig |
| Browser-login, kopier cookie eller redirect-URL | `aulalibre`, `aula-dk-picture-download` | Simpelt, men manuelt og kortlivet |
| Chrome-automatisering | `ilenhart/aula-mcp-server` | Kræver Chrome på maskinen |
| Låner en anden integrations session | `nodered-aula-messages` (via HA), `aula-notify` (via `aula-mcp`) | Login ét sted |
| Intet login: iCal-link og videresendte e-mails | Familyplanen, Daysi | Virker for alle uden teknik, men får kun kalenderen og det, Aula e-mailer |

Login er den hyppigste fejlkilde. I `scaarup/aula` handler 4 af de 20 mest kommenterede issues om login (fx
[#287](https://github.com/scaarup/aula/issues/287) "MitID - No redirect from post-broker-login", 33 kommentarer;
[#291](https://github.com/scaarup/aula/issues/291) "MitID godkendelse mislykkedes", 26 kommentarer). `aula-mcp` har en
"MitID drift canary" og åbne login-fejl for MitID-brugere med flere identiteter eller en privat organisation
([#114](https://github.com/Casperjuel/aula-mcp/issues/114), [#139](https://github.com/Casperjuel/aula-mcp/issues/139))
[gh]. En bruger spørger: "Er det en god ide at bruge MitID i integrationen? Er der ikke en risiko for, at det kan gå galt?"
(scaarup/aula#257).

## Hvad brugerne beder om

Ud fra issues med flest kommentarer og README'ernes egne begrundelser:

1. **Ugeplaner og lektier fra de systemer, skolen bruger** (Meebook, EasyIQ, Min Uddannelse, Huskelisten, Docendo).
   Det er det mest efterspurgte i `scaarup/aula`: [#17 "Ugeplaner fra Meebook"](https://github.com/scaarup/aula/issues/17)
   (100 kommentarer), [#132 "AULA - EasyIQ"](https://github.com/scaarup/aula/issues/132) (94),
   [#42 Huskelisten](https://github.com/scaarup/aula/issues/42) (48), [#70 Opgaver fra MU](https://github.com/scaarup/aula/issues/70) (43).
   For nylig: ugeplanen som JSON i stedet for HTML ([#376](https://github.com/scaarup/aula/issues/376)) og at bevare
   datoen på "Vigtig information" ([#378](https://github.com/scaarup/aula/issues/378)).
2. **At login bare virker** og kan fornys uden at slette integrationen (se ovenfor).
3. **Mindre støj, kun det vigtige.** Det er hele grunden til `aula-notify`, `instant-aula` og `aula-buddy`.
4. **Beskeder med mere indhold:** flere beskeder end den seneste ([#301](https://github.com/scaarup/aula/issues/301),
   11 kommentarer), et flag for vedhæftede filer ([#356](https://github.com/scaarup/aula/issues/356)).
5. **Ugeplanens punkter som kalenderbegivenheder** ([#112](https://github.com/scaarup/aula/issues/112),
   [#113](https://github.com/scaarup/aula/issues/113)) og en fødselsdagskalender pr. barn
   ([hass-aula#81](https://github.com/nickknissen/hass-aula/issues/81)).
6. **At spørge en AI** om skoledagen: hele MCP-bølgen og Aula-Assistent ("Hvad skal jeg huske til børnene i morgen?").
7. **Oversættelse** for forældre, der ikke læser dansk (`aula-buddy`, `ha-aula-ui`).
8. **Billeder** fra galleriet gemt lokalt (de ældste scripts).

**Hvad mangler?** Ingen af værktøjerne har et **arkiv** af opslag og beskeder, der overlever, at Aula sletter dem
(kun billed-scripts gemmer noget). Kun `ha-aula-ui` (kræver HA OS) og Familyplanen (hostet, betalt, uden MitID og
kun via e-mails) laver **arrangementer ud fra beskedernes og opslagenes tekst**, og ingen af dem retter et
eksisterende event, når skolen sender en rettelse. Ingen læser PDF-vedhæftninger for datoer. Ingen har todos, man
krydser af, eller søgning i det hele. Det er aula-agents særkende.

## Navne (til navne-ticket'en)

| Navn | Hvem |
|---|---|
| Aula | `scaarup/aula` (HA), `nickknissen/aula` (PyPI `aula`) |
| Aula for Home Assistant | `nickknissen/hass-aula` |
| Aula UI for Home Assistant | `gyusza/ha-aula-ui` |
| aula-mcp / aula_mcp / Aula MCP Server | Casperjuel, thrugz, madslundt, miklen, fhvilshoj, ilenhart |
| aula_connect / ha_aula_connect | HairingX |
| Aula Reversed (repo: aulalibre) | eisbaw |
| Aula Buddy ("Aula, in your language.") | piyushpalod |
| aula-notify | filipbech |
| instant-aula | chrsoerup |
| AulaSync | rpaasch (skemaer til personale) |
| Aula-Assistent | RexifyIT ApS (kommerciel) |
| Familyplanen, Daysi | kommercielle familiekalendere |

Næsten alle hedder "aula-" + et ord. "Aula-Assistent" ligger tæt på "aula-agent" både i lyd og i hvad den lover
(en AI-hjælper til Aula). Flere projekter skriver udtrykkeligt, at de "ikke er tilknyttet Aula, KOMBIT, Netcompany
eller KMD" (AulaSync, Aula Buddy, Aula UI).

## Hvad det betyder for "Hvem er aula-agent til, og hvem installerer det?"

- **Dem, der installerer Aula-værktøjer i dag, er teknikere.** Den største gruppe er Home Assistant-brugere (HACS,
  hundredvis af installationer). Den nye er udviklere med Claude Code eller Cursor (MCP-servere). Begge er forældre,
  der kører det for deres egen familie.
- **Familier uden teknisk viden bliver nået af hostede, betalte tjenester**, som enten undgår MitID helt (Familyplanen,
  Daysi: iCal-link og videresendte e-mails) eller kører det for dem (Aula-Assistent). Kortet har lagt SaaS uden for
  scope, så de er ikke en model for aula-agent.
- **MitID-login er den svære del for alle.** Det går i stykker et par gange om året, skal ske fra en dansk
  hjemmeforbindelse og fylder mest i issues. Et værktøj for andre end en udvikler skal have en vej til at logge ind
  igen fra telefonen, og nogen skal rette det, når Aula ændrer sig. aula-agent lægger det i `aula`-biblioteket, som
  har mange releases og flere projekter, der bruger det.
- **Home Assistant er en mulig distributionskanal**, hvis aula-agent skal nå længere end én familie.
  Der er både publikum, HACS og add-ons; `ha-aula-ui` og `instant-aula` er kørt den vej.
- **aula-agents særkende er arkivet og datoerne i teksten** (beskeder, opslag, PDF'er) med rettelser, todos og
  søgning. Det tilbyder ingen andre samlet. Aulas egen kalendersynkronisering dækker kun det, der står i Aula-kalenderen.
- **Privatliv er en kendt bekymring i feltet.** MCP-projekterne advarer om, at indholdet går til AI-udbyderen.
  `aula-buddy` skjuler børnenes navne og sender aldrig følsomme beskeder. Familyplanen og Aula-Assistent bruger det
  som salgsargument. Det understøtter, at kortets punkt om pris og privatliv bliver en ticket, hvis aula-agent skal
  være for andre.
