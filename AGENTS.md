# aula-agent

Finder arrangementer i Aula-opslag og -beskeder og lægger dem i Google Calendar,
én kalender pr. barn ("Skole – <fornavn>"). Python 3.14 via `uv`. Brugervejledning: `README.md`.

- `src/aula_agent/aula_source.py` – henter fra Aula (bibliotek: `aula` på PyPI) og bestemmer via `Intake`, hvilke items agenten får. Starter aldrig MitID-login selv.
- `src/aula_agent/contract.py` – agent-kontrakten: skriver `inbox.json` og validerer events-filen (alle fejl på én gang → exit-kode 5).
- `src/aula_agent/gcal.py` – Google Calendar + Drive: `plan()` beslutter, `execute()` udfører via `Google`-adapteren (vedhæftninger skal ligge i Drive).
- `src/aula_agent/ledger.py` – alt hvad der huskes mellem kørsler (`data/state.json`). Rør ikke state-dict'en direkte andre steder.
- `src/aula_agent/runs.py` – en kørsel: lås + run-log + struktureret udfald (`Outcome`). `credentials.py` – login-status og Google OAuth.
- `src/aula_agent/web.py` – valgfri web-side (Starlette, 127.0.0.1:8787) bag Cloudflare Tunnel + Access. Appen validerer selv Access-JWT'en og e-mail-allowlisten (`web` i `data/config.json`, sættes med `aula-agent web-setup`) og afviser alt, når den ikke er sat op.
- `deploy/` – systemd-skabeloner (`*.in`) + `install-services.sh` (web, tunnel, nightly) og `nightly.sh` (natkørsel uden T3 med Claude Code eller Codex, styret af `AULA_AGENT`).
- Instruktionerne her er fælles for alle agenter: Codex læser `AGENTS.md`, Claude Code læser `CLAUDE.md`, som importerer denne fil.
- `data/` – personlige data, git-ignoreret: `config.json` (børn → kalendere), `state.json`, `inbox.json`, `attachments/`, Google-tokens.
- Tests: `uv run pytest -q`. Domænesprog: `GLOSSARY.md`.

## Natlig kørsel

Når du bliver bedt om at køre den natlige Aula-synkronisering, så gør præcis dette:

1. Kør `uv run aula-agent fetch`.
   - Exit-kode 3 (`AULA_LOGIN_REQUIRED`): stop (gå til "Afslutning af tråden"). Svar kort, at Aula-login er udløbet, og gengiv kommandoens besked om hvor/hvordan brugeren logger ind. Prøv **aldrig** selv at logge ind (MitID kan låse kontoen).
2. Læs `data/inbox.json`. Hvis `items` er tom: svar "Intet nyt fra Aula" og gå til "Afslutning af tråden".
3. Læs hvert item: `body` og vedhæftede filer (`attachments[].path` er relativ til `data/`) – indbydelser har ofte dato og detaljer.
   - PDF'er: læs dem direkte, hvis dit værktøj kan (Claude Code: Read). Ellers: `uv run aula-agent pdf-text <attachments/...pdf>`.
   - Billeder: se dem med dit billedværktøj (Claude Code: Read, Codex: view_image), hvis du har et.
4. Find arrangementer og frister. Skriv `data/events-<YYYY-MM-DD>.json`:

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
     ]
   }
   ```

   Regler:
   - `processed`: **alle** item-keys fra inbox, også dem uden arrangementer (ellers kommer de igen i morgen).
   - `start`/`end`: `YYYY-MM-DD` = heldagsbegivenhed, og `end` er sidste dag (inklusiv). `YYYY-MM-DDTHH:MM` = med klokkeslæt, dansk tid; `end` er valgfri (standard 1 time). Årstal mangler ofte i teksten: vælg den nærmeste fremtidige dato set fra opslagets/beskedens dato.
   - Kun ting der ligger i dag eller senere. Ikke generelle informationer uden dato.
   - Frister ("tilmelding senest 10/11", "aflever seddel inden fredag") bliver en heldagsbegivenhed med titel `Frist: …`.
   - `key`: nye begivenheder får `<item-key>#<n>`. Er det en **rettelse/aflysning** af noget i `existing_events`, så genbrug den eksisterende `key` (så opdateres begivenheden i stedet for at blive dubleret). Aflysning: `{"key": "...", "child_ids": [...], "cancelled": true}`.
   - Et item kan komme igen (rettet i Aula, eller nu med en vedhæftning der ikke kunne hentes før). Har `existing_events` allerede begivenheder med item'et som `source`, så genbrug deres `key`s. `existing_events` har én linje pr. begivenhed med alle børnene i `child_ids`.
   - Spring over, hvis arrangementet allerede står i `aula_calendar` (Aulas egen kalender) for samme dag.
   - `removed_by_user`: begivenheder brugeren selv har fjernet på web-siden. Opret dem **ikke** igen – heller ikke under en ny key.
   - `child_ids`: brug item'ets `child_ids` når de er sat. Er listen tom (besked og flere børn), så afgør det ud fra indhold, klasse/gruppe, `child_hints` og institution; hvis det er tvivlsomt, så sæt alle relevante børn.
   - `attachments`: kun filer der hører til arrangementet (indbydelser, sedler, programmer) – ikke billeder fra en tur der har været.
   - Titel: kort, dansk, uden barnets navn. Beskrivelse: det praktiske (hvad skal medbringes, pris, tid, tilmelding). Skriv **ikke** andre børns eller forældres navne ind i kalenderen.
5. Kør `uv run aula-agent apply data/events-<dato>.json --dry-run`. Ved exit-kode 5 så ret filen (beskeden lister alle fejl) og prøv igen. Prøvekørslen viser for hvert event og barn, om det bliver oprettet, opdateret, slettet eller sprunget over.
6. Kør `uv run aula-agent apply data/events-<dato>.json`.
   - Exit-kode 4 (`GOOGLE_LOGIN_REQUIRED`): stop og bed brugeren forbinde Google igen; gengiv kommandoens besked om hvordan.
7. Svar med en kort dansk opsummering: hvad der blev oprettet/opdateret/slettet (dato + titel + barn), og hvad du var i tvivl om.

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
