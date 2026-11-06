# aula-agent

Finder arrangementer i Aula-opslag og -beskeder og lægger dem i Google Calendar.
Hvert barn hører til én kalender (standard: sin egen, "Aula – <fornavn>"; ældre installationer har "Skole – <fornavn>" gemt i config og beholder den), og flere børn kan dele én (joint calendar, ADR 0002). Python 3.14 via `uv`. Brugervejledning: `README.md`.

- `src/aula_agent/aula_source.py` – henter fra Aula (bibliotek: `aula` på PyPI) og bestemmer via `Intake`, hvilke items agenten får. `backfill()` læser alt til arkivet uden om intake (kilden er en `BackfillSource`: Aula eller en redningskopi). Starter aldrig MitID-login selv.
- `src/aula_agent/archive.py` – arkivet (`data/archive.sqlite`, ADR 0001): alt hvad `fetch` og `backfill` har set fra Aula eller en redningskopi (opslag og hele beskedtråde), med alle item versions og filer, uanset cutoff, og agentens classifications (pr. item version, evt. med en todo) og topics, plus et fritekstindeks (FTS5) over det hele og teksten i PDF- og docx-filer (`filetext.py` trækker den ud, én gang pr. fil). Kun dette modul rører arkiv-filen og ejer item-nøglerne; skemaets version styres med `user_version`.
- `src/aula_agent/contract.py` – agent-kontrakten: skriver `inbox.json`, validerer events-filen (alle fejl på én gang → exit-kode 5) og gemmer ved `apply` agentens classifications og topics i arkivet (via `archive.py`).
- `src/aula_agent/backup.py` – backup af arkivet til den private Drive-mappe `Aula-arkiv` (aldrig den delte "Aula"); `fetch` og `backfill` tager den til sidst, en fejl er kun en advarsel. Er mappen slettet i Drive, starter den forfra.
- `src/aula_agent/gcal.py` – Google Calendar + Drive: `plan()` beslutter (én post pr. event og kalender med kalenderens børn), `execute()` udfører via `Google`-adapteren (vedhæftninger skal ligge i Drive).
- `src/aula_agent/ledger.py` – alt hvad der huskes mellem kørsler (`data/state.json`), også hvilke todos der er krydset af. Rør ikke state-dict'en direkte andre steder.
- `src/aula_agent/runs.py` – en kørsel: lås + run-log + struktureret udfald (`Outcome`). `credentials.py` – login-status og Google OAuth.
- `src/aula_agent/web.py` – valgfri web-side (Starlette, 127.0.0.1:8787) bag Cloudflare Tunnel + Access. Appen validerer selv Access-JWT'en og e-mail-allowlisten (`web` i `data/config.json`, sættes med `aula-agent web-setup`) og afviser alt, når den ikke er sat op. Den kan ligge på hjemmeskærmen som en app (`/manifest.webmanifest`, ikonet i `static/icon.svg` + PNG'er lavet ud fra den) med navnet `web.app_name` (standard "Overblik"; den må ikke hedde "Aula").
- `src/aula_agent/timeline.py` – tidslinjen på forsiden (`/`): hvad der står under Gælder nu, Kommende og Tidligere, uge for uge (items, events og todos). Returnerer data; `web.py` renderer. Hvornår et item er aktuelt (`current`, `past`, `important_on`), står på `archive.Item`.
- `src/aula_agent/static/overblik.js` – JS-laget på forsiden, søgningen (`/search`) og detaljen (`/item/<key>`) (vanilla, ingen build, ingen afhængigheder): søgning mens man skriver, detaljen i en skuffe på brede skærme, `/` og Esc. Siderne virker uden; de har en CSP uden inline script.
- `src/aula_agent/demo.py` – `uv run python -m aula_agent.demo <tom-mappe>`: web-siden med opdigtede data på 127.0.0.1 uden Access, til at prøve siden i en browser. Rører aldrig `data/`.
- `deploy/` – systemd-skabeloner (`*.in`) + `install-services.sh` (web, tunnel, nightly) og `nightly.sh` (natkørsel uden T3 med Claude Code eller Codex, styret af `AULA_AGENT`).
- Instruktionerne her er fælles for alle agenter: Codex læser `AGENTS.md`, Claude Code læser `CLAUDE.md`, som importerer denne fil.
- `data/` – personlige data, git-ignoreret: `config.json` (børn → kalendere), `state.json`, `inbox.json`, `archive.sqlite`, `attachments/`, Google-tokens. I et git-worktree (T3 Code og Codex kører ofte i et) bliver `data/` automatisk et link til hovedcheckoutets `data/`, så logins, historik og "fjernet af dig" er de samme.
- Tests: `uv run pytest -q`. Domænesprog: `GLOSSARY.md`.

## Natlig kørsel

Når du bliver bedt om at køre den natlige Aula-synkronisering, så gør præcis dette:

1. Kør `uv run aula-agent fetch`.
   - Exit-kode 3 (`AULA_LOGIN_REQUIRED`): stop (gå til "Afslutning af tråden"). Svar kort, at Aula-login er udløbet, og gengiv kommandoens besked om hvor/hvordan brugeren logger ind. Prøv **aldrig** selv at logge ind (MitID kan låse kontoen).
2. Læs `data/inbox.json`. Hvis både `items` og `to_classify` er tomme: svar "Intet nyt fra Aula" og gå til "Afslutning af tråden". Er kun `items` tom, så spring til classification af køen (trin 4, `to_classify`).
3. Læs hvert item: `body` og vedhæftede filer (`attachments[].path` er relativ til `data/`) – indbydelser har ofte dato og detaljer.
   - PDF'er: læs dem direkte, hvis dit værktøj kan (Claude Code: Read). Ellers: `uv run aula-agent pdf-text <attachments/...pdf>`.
   - Billeder: se dem med dit billedværktøj (Claude Code: Read, Codex: view_image), hvis du har et.
4. Find arrangementer og frister, og skriv en classification for hvert item. Skriv `data/events-<YYYY-MM-DD>.json`:

   ```json
   {
     "processed": ["post:123", "message:456:789"],
     "events": [
       {
         "key": "post:123#1",
         "source": "post:123",
         "child_ids": [12345],
         "title": "Juleklip",
         "start": "2026-12-04",
         "end": "2026-12-04",
         "location": "",
         "description": "Husk saks og lim. Forældre er velkomne fra kl. 12.",
         "attachments": ["attachments/post_123/55-indbydelse.pdf"]
       }
     ],
     "classifications": [
       {
         "item": "post:123",
         "category": "arrangement",
         "span": {"start": "2026-12-04"},
         "topics": ["juleklip"],
         "summary": "Juleklip fre 4/12, husk saks og lim. Forældre velkomne fra kl. 12.",
         "todo": {"text": "Medbring saks og lim", "due": "2026-12-04"}
       },
       {"item": "message:456:789", "category": "svar"}
     ],
     "new_topics": {"juleklip": "Juleklip og julearrangementer i klassen"}
   }
   ```

   Regler:
   - `processed`: **alle** item-keys fra inbox, også dem uden arrangementer (ellers kommer de igen i morgen).
   - `start`/`end`: `YYYY-MM-DD` = heldagsbegivenhed, og `end` er sidste dag (inklusiv). `YYYY-MM-DDTHH:MM` = med klokkeslæt, dansk tid; `end` er valgfri (standard 1 time). Årstal mangler ofte i teksten: vælg den nærmeste fremtidige dato set fra opslagets/beskedens dato.
   - Kun ting der ligger i dag eller senere. Ikke generelle informationer uden dato.
   - Frister ("tilmelding senest 10/11", "aflever seddel inden fredag") bliver en heldagsbegivenhed med titel `Frist: …`.
   - `key`: nye begivenheder får `<item-key>#<n>`. Er det en **rettelse/aflysning** af noget i `existing_events`, så genbrug den eksisterende `key` (så opdateres begivenheden i stedet for at blive dubleret). Aflysning: `{"key": "...", "cancelled": true}` – den fjerner begivenheden for alle børn.
   - Et item kan komme igen (rettet i Aula, eller nu med en vedhæftning der ikke kunne hentes før). Har `existing_events` allerede begivenheder med item'et som `source`, så genbrug deres `key`s. `existing_events` har én linje pr. begivenhed med alle børnene i `child_ids`.
   - Spring over, hvis arrangementet allerede står i `aula_calendar` (Aulas egen kalender) for samme dag.
   - `removed_by_user`: begivenheder brugeren selv har fjernet på web-siden for børnene i `child_ids`. Læg dem **ikke** i de børns kalendere igen – heller ikke under en ny key. Andre børn kan godt have dem.
   - `child_ids` er **alle** de børn, begivenheden gælder. Ved en rettelse: skriv alle børnene igen; et barn der ikke er med, får begivenheden fjernet fra sin kalender.
   - `child_ids`: brug item'ets `child_ids` når de er sat. Er listen tom (besked og flere børn), så afgør det ud fra indhold, klasse/stue/gruppe, `child_hints` og institution; hvis det er tvivlsomt, så sæt alle relevante børn.
   - `attachments`: kun filer der hører til arrangementet (indbydelser, sedler, programmer) – ikke billeder fra en tur der har været.
   - Titel: kort, dansk, uden barnets navn. Beskrivelse: det praktiske (hvad skal medbringes, pris, tid, tilmelding). Skriv **ikke** andre børns eller forældres navne ind i kalenderen.

   Classification (`classifications`): én pr. item i `processed`, også dem uden arrangementer. Den gemmes i arkivet og bruges til overblikket på web-siden.
   - `category`: den vigtigste slags information i item'et, én af:
     - `arrangement`: noget der sker på en bestemt dag (juleklip, skolefest, tur, forældremøde),
     - `frist`: tilmelding, aflevering, betaling,
     - `periode`: noget der gælder fra–til (emneuge, svømning i fire uger, ferie i SFO),
     - `info`: stående info, der gælder længe (skema, regler, mødetider, velkommen til skoleåret),
     - `orientering`: kortvarig besked (lus i klassen, et referat, et nyhedsbrev),
     - `svar`: besked uden ny information ("Tak", "Ok", "Det er fint").
   - `span`: hvornår item'et gælder (ikke hvornår det kom): `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`, `end` er sidste dag og valgfri (standard = `start`). Arrangement: dagen; frist: fristens dato; periode: fra–til. Udelad den, når item'et ikke har en dato (typisk info og orientering).
   - `topics`: 1–3 emner. Genbrug et fra inbox' `topics`, når det passer (se beskrivelsen). Opret kun et nyt, når intet passer, og skriv det så i `new_topics` med en linjes beskrivelse af, hvad der hører under det. Korte navne med små bogstaver ("svømning", "lejrskole"). Svar må have 0.
   - `summary`: højst 120 tegn på én linje med det praktiske: hvad, hvornår, hvad skal man huske. Ikke "Kære forældre …". **Ingen** navne på andre børn eller forældre. Svar må udelade den.
   - `todo` (valgfri): noget forælderen selv skal gøre – betale, tilmelde, aflevere, medbringe, svare. `{"text": "...", "due": "YYYY-MM-DD"}`.
     - `text`: kort og i bydeform ("Betal 50 kr. til turen", "Tilmeld i Aula"), højst 120 tegn. **Ingen** navne på andre børn eller forældre.
     - `due`: fristen eller dagen det skal være gjort, når der er en. Udelad den, når der ingen frist er.
     - Kun én pr. item; er der flere ting, så skriv den vigtigste eller saml dem kort. Ingen todo på `svar` eller når der intet er at gøre.
     - En frist med todo er stadig også en frist: den får sit `Frist: …`-event som ovenfor (kun for `items`, aldrig for `to_classify`). Er `due` arrangementets egen dag ("medbring …"), er arrangementets event nok.
     - Skriv også todo for items i `to_classify`. Koden afgør selv, om den kommer på listen (gamle todos starter som klaret).
     - Skriv todo uanset kalenderen: også når brugeren har fjernet eventet (`removed_by_user`), eller når arrangementet allerede står i `aula_calendar`. At fjerne et event er ikke det samme som at have gjort det; brugeren krydser selv todo'en af på web-siden.
   - Følsomme tråde (`"sensitive": true` på item'et): **intet** `summary`.
   - `child_ids`: kun når item'ets `child_ids` er tom, efter samme regler som for events. Har Aula sat børnene, så udelad den (Aulas vinder).

   Køen (`to_classify`): items i arkivet, hvis nyeste version ikke er klassificeret (fra backfill, ældre end cutoff, en ny version af et item, eller alle efter `aula-agent reclassify`). Højst 100, ældste først, i samme form som `items`.
   - Skriv kun en classification for dem – **ingen** events, og de skal ikke i `processed`. Et event med et af dem som `source` bliver afvist.
   - Klassificér ud fra tekst, emne og filnavne. Læs kun en fil, når det er nødvendigt for at forstå item'et.
   - Det er OK at udelade et item, fx hvis det er svært eller der er mange. Det kommer igen i køen næste nat.
5. Kør `uv run aula-agent apply data/events-<dato>.json --dry-run`. Ved exit-kode 5 så ret filen (beskeden lister alle fejl) og prøv igen. Prøvekørslen viser for hvert event og barn, om det bliver oprettet, opdateret, slettet eller sprunget over. Står der `Slet: … (ikke længere med)`, så tjek at barnet med vilje er udeladt.
6. Kør `uv run aula-agent apply data/events-<dato>.json`.
   - Exit-kode 4 (`GOOGLE_LOGIN_REQUIRED`): stop og bed brugeren forbinde Google igen; gengiv kommandoens besked om hvordan.
7. Svar med en kort dansk opsummering: hvad der blev oprettet/opdateret/slettet (dato + titel + barn), hvor mange items fra køen (`to_classify`) du klassificerede, og hvad du var i tvivl om.

### Afslutning af tråden (kun i T3 Code – altid, også når du stopper tidligt i trin 1, 2 eller 6)

Spring dette afsnit over, hvis du ikke kører i T3 Code (fx `claude -p` eller `codex exec` fra `deploy/nightly.sh`).
I T3 Code har hver kørsel sin egen tråd. Ryd op i den, så brugeren kun ser tråde der kræver noget.
Værktøjerne kommer fra T3's MCP-server (`t3_thread_update`, `t3_thread_organize`). I Claude Code kan de være "deferred":
hent dem først med ToolSearch (`select:mcp__t3-code__t3_thread_update,mcp__t3-code__t3_thread_organize`).

1. Omdøb tråden (`t3_thread_update`, `action: "rename"`, uden `threadId`) til `Aula <D>/<M>: <resultat>`, fx
   `Aula 5/11: 2 nye`, `Aula 6/11: intet nyt`, `Aula 7/11: 1 opdateret, 1 aflyst`, `Aula 8/11: ⚠ login udløbet`.
2. Settle tråden (`t3_thread_organize`, `action: "settle"`, uden `threadId`) **kun** når brugeren ikke skal gøre noget.
   Lad den stå åben (ingen settle) når:
   - Aula- eller Google-login er udløbet (exit-kode 3 eller 4),
   - en kommando fejlede på anden måde, eller `fetch` gav advarsler,
   - du var i tvivl om et arrangement (dato, barn, om det skulle med) og har skrevet det i opsummeringen.

## Agent skills

### Issue tracker

Issues ligger i GitHub Issues (`fMads/aula-agent`, via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

De fem standard-labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` i repo-roden (oprettes først når der er brug for dem). See `docs/agents/domain.md`.
