<img src="assets/icon.svg" width="64" height="64" alt="">

# aula-agent

Mange datoer fra skolen eller børnehaven står aldrig i Aula-kalenderen. De kommer i stedet i beskeder, i ugebreve og som PDF-indbydelser i opslag. **aula-agent** finder dem og lægger dem i Google Calendar.

Hver aften sker følgende:

1. Nye beskeder og opslag hentes fra Aula, sammen med de vedhæftede filer.
2. En AI-agent (Claude Code eller Codex) læser teksten og filerne. Den finder arrangementer og frister, f.eks. *"Fredag d. 14. er der juleklip, husk saks"* eller *"tilmelding senest 10/11"*.
3. Arrangementerne lægges i en Google-kalender. Som standard har hvert barn sin egen kalender, men flere børn kan dele én. Indbydelser og andre filer bliver hængt på begivenhederne.

Hvis institutionen sender en rettelse, f.eks. *"det er flyttet til torsdag"*, opdateres den begivenhed, der allerede ligger i kalenderen. Der bliver ikke oprettet en ny.

## Sådan hænger det sammen

```
              hver aften (T3 Code, systemd-timer eller cron)
                                  │
                                  ▼
 AI-agent (Claude Code, Codex – evt. via T3 Code), følger AGENTS.md
   ├─ uv run aula-agent fetch    → Python henter fra Aula → data/inbox.json + bilag
   ├─ læser beskeder og PDF'er og finder arrangementer   ← det er her, "AI'en" er
   ├─ skriver data/events-<dato>.json
   └─ uv run aula-agent apply    → Python opretter og opdaterer i Google Calendar + Drive

 Valgfri web-side (bag Cloudflare Access): status, MitID-login, Google-login,
 kommende begivenheder og en knap til at fjerne forkerte
```

Python-delen forstår ikke selv tekst. Den henter og gemmer data og taler med Aula og Google. Det er agenten, der læser og vurderer indholdet.

**Arkivet.** Aula sletter opslag og beskeder efter nogle måneder. Derfor gemmer `fetch` hvert opslag og hver beskedtråd, den ser, i et arkiv (`data/archive.sqlite`) med alle filer og billeder, også det der er for gammelt til agenten. En tråd gemmes hel, som Aula sendte den, også systembeskeder (fx modtagere tilføjet); beskeder fra en følsom tråd er markeret som følsomme. Retter institutionen et opslag eller en besked, gemmes den nye version ved siden af den gamle. Hver nat læser `fetch` kun de tråde, der er ændret; `uv run aula-agent backfill` henter én gang alt, hvad Aula stadig har (alle opslag og alle tråde), til arkivet. Den kan køres igen uden dubletter, giver ikke agenten noget og starter aldrig et MitID-login. Med en sti til en redningskopi (`uv run aula-agent backfill data/rescue-2026-11-05`) læser den kopien i stedet for Aula: `posts.json`, `threads.json` og filerne i `files/` kopieres ind, og versionerne får datoen i mappens navn. Det kræver ikke Aula-login, og kører du bagefter `fetch` eller `backfill` mod Aula, giver de samme items ingen dubletter. Arkivet rydder aldrig op. Det er en almindelig SQLite-fil, som du kan åbne med SQLite-værktøjer, DuckDB eller ClickHouse.

**Backup.** Til sidst i hver `fetch` kopieres arkivet og dets filer til Drive-mappen **"Aula-arkiv"**, som aula-agent selv opretter. Den er privat: del den ikke (modsat mappen "Aula" med kalenderens vedhæftninger). Arkiv-filen erstattes hver gang, og hver fil lægges kun op én gang. Fejler backuppen, eller mangler Google-login, giver `fetch` en advarsel, men natkørslen fortsætter. Mappens navn kan ændres med `archive_folder_name` i `data/config.json`, før den første backup tages. `uv run aula-agent backup` tager en backup med det samme.

## Hvad er påkrævet, og hvad er valgfrit?

| | | Hvorfor |
|---|---|---|
| **Påkrævet** | Linux eller macOS med [uv](https://docs.astral.sh/uv/) | Kører Python 3.14 og projektet |
| | MitID-app og forældreadgang til Aula | Aula har ikke noget officielt API |
| | Google-konto + et (gratis) Google Cloud-projekt | Kalender og Drive (til vedhæftede filer) |
| | [Claude Code](https://claude.com/claude-code) **eller** [Codex](https://github.com/openai/codex) med abonnement eller API-adgang | Agenten, der læser beskederne |
| **Valgfrit** | [T3 Code](https://t3.codes) | Planlagte kørsler med en tråd pr. kørsel. Alternativet er en systemd-timer eller cron |
| | Web-side | Status og login fra telefon eller computer. Uden den logger du ind fra en terminal |
| | Cloudflare (domæne + Zero Trust, gratis) | Kun hvis web-siden skal kunne nås udefra. Står for login og adgangskontrol |

## Vigtigt at vide først

- **Uofficielt API.** Projektet bruger [`aula`](https://github.com/nickknissen/aula)-biblioteket, som taler med Aulas interne API. Hvis Aula ændrer noget, kan det holde op med at virke, indtil biblioteket bliver opdateret.
- **Pas på MitID.** For mange loginforsøg kan låse din MitID. aula-agent starter derfor **aldrig** et MitID-login af sig selv. Når sessionen udløber, siger den til, og så logger du selv ind igen. Web-siden tillader højst 3 loginforsøg pr. kvarter.
- **Dansk IP-adresse.** MitID-login skal ske fra en maskine med en dansk IP-adresse. Login fra andre lande bliver ofte stoppet af et sikkerhedstjek.
- **Privatliv.** Beskeder og opslag handler ofte om andre børn og forældre. Under natkørslen bliver indholdet sendt til den AI-tjeneste, du bruger (Anthropic for Claude, OpenAI for Codex). Det skal du selv tage stilling til. Alt det hentede ligger lokalt i `data/`, og den mappe er udelukket fra git. Agenten har besked på ikke at skrive andre børns eller forældres navne ind i kalenderen.

## Opsætning

### 1. Hent projektet

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # hvis du ikke har uv
git clone https://github.com/fMads/aula-agent.git && cd aula-agent
uv sync
```

### 2. Log ind i Aula

```bash
uv run aula --username <dit-mitid-brugernavn> login
```

Scan QR-koden med MitID-appen. QR-koden består af to halvdele, der skifter, så hold telefonen stille, indtil appen har læst begge. Sessionen bliver gemt i `~/.config/aula/` og fornyes automatisk. Når den til sidst udløber, kører du kommandoen igen eller logger ind via web-siden.

### 3. Forbind Google

aula-agent opretter sine egne kalendere og Drive-mapper ("Aula" og "Aula-arkiv") og kan **kun** se og ændre dem. Resten af din kalender og dit Drive kan den ikke se. Det tager cirka 10 minutter at sætte op første gang.

1. Opret et projekt på https://console.cloud.google.com/projectcreate, f.eks. med navnet `aula-agent`.
2. Slå begge API'er til ved at klikke **Enable** på disse to sider:
   - https://console.cloud.google.com/apis/library/calendar-json.googleapis.com
   - https://console.cloud.google.com/apis/library/drive.googleapis.com
3. Gå til https://console.cloud.google.com/auth/overview og klik **Get started**.
   - **App name:** f.eks. `Aula Kalender`
   - Vælg din e-mail som support-mail.
   - **Audience:** External
4. Gå til **Branding** og udfyld:
   - **Application home page** og **privacy policy link:** Google kræver dem, før appen kan udgives. Brug adresser på et domæne, du selv har, f.eks. web-sidens adresse. Google tjekker kun indholdet, hvis du beder om verificering, og det skal du ikke.
   - **Authorized domains:** tilføj dit domæne.
   - **Upload ikke et logo.** Så kræver Google, at appen bliver verificeret.
5. Gå til **Audience** og klik **Publish app**, så status bliver *In production*. Ellers udløber adgangen efter 7 dage.
   Har du ikke et domæne, kan du lade appen blive i *Testing* og tilføje dig selv som testbruger. Så skal du bare forbinde Google igen hver 7. dag.
6. Gå til https://console.cloud.google.com/auth/clients/create.
   - **Type:** Web application
   - **Authorized redirect URIs:**
     - `http://localhost:8765/` (til login fra en terminal)
     - `https://<din-web-adresse>/google/callback`, hvis du bruger web-siden
   - Klik **Create**, og klik **Download JSON** med det samme. Nøglen vises kun den ene gang.
7. Gem filen som `data/google_client_secret.json`, og kør:

   ```bash
   uv run aula-agent google-login
   ```

   Åbn linket, og vælg din Google-konto.
   - Google advarer om, at appen ikke er verificeret. Det er forventet. Klik **Advanced** og derefter **Go to … (unsafe)**.
   - **Sæt hak i begge rettigheder**, både kalender og Drive.
   - Til sidst viser browseren en fejlside, fordi localhost ikke kan nås. Det er også forventet. Kopiér hele adressen fra adresselinjen ind i terminalen.

   Bruger du web-siden, kan du i stedet uploade JSON-filen og forbinde Google på `https://<din-web-adresse>/google`.

### 4. Første kørsel

```bash
uv run aula-agent fetch     # henter den seneste måneds beskeder og opslag
uv run aula-agent status
```

Start derefter din agent i projektmappen, og bed den om at køre den natlige synkronisering:

```bash
claude "Kør den natlige Aula-synkronisering"     # Claude Code
codex "Kør den natlige Aula-synkronisering"      # eller Codex
```

Codex skal have lov til at bruge netværket, fordi `fetch` og `apply` taler med Aula og Google. Godkend det, når den spørger.

Agenten følger [`AGENTS.md`](AGENTS.md). `CLAUDE.md` henviser bare til den, så begge agenter bruger de samme instruktioner. Den læser alt, der er hentet, laver en events-fil, laver en prøvekørsel og opretter derefter begivenhederne. Kalenderen hedder **"Aula – <barnets fornavn>"** og bliver oprettet automatisk. Navnet kommer fra `calendar_name_template` i `data/config.json`; har din config allerede en skabelon (ældre installationer har "Skole – {name}"), bliver den brugt, og eksisterende kalendere omdøbes ikke. Første gang vil det meste ligge tilbage i tiden, så der bliver typisk kun oprettet nogle få begivenheder.

### 5. Kør automatisk hver aften

Vælg én af disse muligheder.

**A) T3 Code.** Opret en planlagt opgave i projektet, der kører dagligt, f.eks. kl. 21:00. Indstil den, så hver kørsel får sin egen tråd, og brug denne prompt:

> Kør den natlige Aula-synkronisering i projektet aula-agent. Følg instruktionerne i AGENTS.md under "Natlig kørsel" præcist.

Det virker, uanset om T3 kører Claude eller Codex.

Hver kørsel giver tråden en titel som `Aula 6/11: 2 nye`. Hvis du ikke skal gøre noget, bliver tråden afsluttet (settled). Den bliver kun stående åben, hvis et login er udløbet, hvis der er sket en fejl, eller hvis agenten var i tvivl om noget.

**B) systemd-timer (Linux):**

```bash
./deploy/install-services.sh nightly                     # med Claude Code
AULA_AGENT=codex ./deploy/install-services.sh nightly    # eller med Codex
sudo loginctl enable-linger $USER    # så den også kører, når du ikke er logget ind
```

Den kører `deploy/nightly.sh` hver aften kl. 21:00, og loggen ligger i `data/logs/`. Agenten er begrænset, så den kun kan det nødvendige:
- **Claude Code** må kun køre `uv run aula-agent …`, læse filer og skrive i `data/`.
- **Codex** kører i sin sandkasse. Den kan kun skrive i projektmappen, i Aula-sessionen og i uv's cache, og den har netværksadgang til Aula og Google.

**C) cron (macOS/Linux)**, med `crontab -e`:

```
0 21 * * * /sti/til/aula-agent/deploy/nightly.sh                    # Claude Code
0 21 * * * AULA_AGENT=codex /sti/til/aula-agent/deploy/nightly.sh   # eller Codex
```

### 6. Valgfrit: web-side med Cloudflare

Web-siden viser (status og logins under fanen Indstillinger, `/settings`):
- om Aula- og Google-login virker
- de seneste kørsler
- hvor mange opslag og beskeder arkivet har, hvornår der sidst blev gemt noget, og hvornår der sidst blev taget backup (med en advarsel, hvis det er mere end 2 døgn siden eller aldrig er sket)
- de kommende begivenheder med en knap til at fjerne forkerte (de bliver så ikke oprettet igen). Gælder en begivenhed flere børn, er der en knap pr. barn
- et overblik på forsiden (`/`) som en tidslinje: øverst "Skal gøres" (opgaver med en "Klaret"-knap, overskredne først), så "I dag og i morgen" (det samme som Kommende, for de to dage), "Gælder nu" (perioder, der kører, opslag Aula har markeret som vigtige, og nye orienteringer), "Kommende" dag for dag fra i overmorgen med en ugelinje, når ugen skifter (opslag og beskeder efter hvornår de gælder, begivenhederne i kalenderen med link til det, de kom fra, og opgaver med en frist), "Stående info" efter emne og "Tidligere" uge for uge med nyeste først ("Vis ældre" går længere tilbage). Svar som "Tak" og "Ok" vises ikke på tidslinjen, kun i søgningen. Har du flere børn, kan du vælge ét barn. Så ser du det, der gælder det barn, og det, der ikke er knyttet til et bestemt barn. Nederst står, hvor mange items agenten endnu ikke har inddelt
- et søgefelt øverst i overblikket. En søgning viser resultaterne på sin egen adresse, `/search` (fx `/search?q=svøm`). Søgningen finder alt i arkivet på ordbegyndelser ("svøm" finder "svømning"), også tekst inde i vedhæftede PDF- og Word-filer (docx), og kan indsnævres efter barn, slags, emne og kilde (opslag, besked, med dokument). Overståede ting er dæmpede, og følsomme tråde vises uden emne og uddrag
- hvert opslag og hver besked på sin egen side, `/item/<item-key>` (fx `/item/post:123` eller `/item/message:456:789`). Adressen ligger fast, så den kan bogmærkes og linkes til. Siden viser hele teksten, filerne (PDF åbnes i browseren, andre filer hentes ned), tidligere versioner, hvis det er rettet i Aula, hvad det blev til i kalenderen (med en knap "Fjern for <barn>" pr. barn, så længe begivenheden ikke er overstået; du bekræfter, før den fjernes), en evt. opgave og for beskeder hele tråden. Følsomme tråde viser kun dato, kilde og art, indtil du trykker "Vis teksten". Filerne udleveres fra arkivet på `/item/<item-key>/file/<vedhæftnings-id>`
- de gamle adresser (`/overblik…`) sender permanent videre til de nye, så links i begivenheder, der allerede ligger i kalenderen, stadig virker

Fra web-siden kan du også logge ind i Aula med MitID (QR-koden vises i browseren) og forbinde Google.

Web-siden lytter kun på `127.0.0.1:8787`, og Cloudflare Access står for login. Appen kontrollerer selv Cloudflares login-bevis (signatur, AUD og e-mail). Den afviser alt, indtil den er sat op.

Det kræver et domæne på Cloudflare. Zero Trust er gratis til få brugere.

1. **Opret Access-appen først**, så siden aldrig er åben:
   - Gå til Zero Trust → Access → Applications → *Add an application* → **Self-hosted**.
   - Domæne: f.eks. `aula.ditdomæne.dk`
   - Politik: *Allow*, med *Include → Emails* og de adresser, der må logge ind.
   - Login-metode: *One-time PIN* (kode på e-mail) virker uden videre. Google-login kræver, at du tilføjer Google som identity provider ([vejledning](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/google/)).
   - Notér **Application Audience (AUD) Tag** og dit team-domæne (`<team>.cloudflareaccess.com`).
2. **Opret tunnellen:**
   - Gå til Zero Trust → Networks → Tunnels → *Create a tunnel* → Cloudflared.
   - Gem tokenet i `~/.config/aula-agent/tunnel-token` med `chmod 600`.
   - Public hostname: `aula.ditdomæne.dk` → `HTTP` → `127.0.0.1:8787`.
   - Under hostnamets *Access*-indstillinger kan du også slå *Protect with Access* til. Så afviser tunnellen også trafik uden login.
3. **Sæt web-siden op og start den:**

   ```bash
   uv run aula-agent web-setup --url https://aula.ditdomæne.dk \
     --team-domain <team>.cloudflareaccess.com --aud <AUD-tag> \
     --email dig@example.com --email partner@example.com
   ./deploy/install-services.sh web tunnel     # kræver cloudflared
   ```

4. Tilføj `https://aula.ditdomæne.dk/google/callback` som redirect-URI på Google-nøglen (trin 3.6).

Se opsætningen med `uv run aula-agent web-setup`. Hvis du kører `--email` igen, erstatter det hele listen.

Siden kan ligge på telefonens hjemmeskærm som en app (i Safari: Del → *Føj til hjemmeskærm*, i Chrome: *Installer app*). Den hedder "Overblik". Et andet navn sætter du med `"app_name"` under `"web"` i `data/config.json`, fx `"app_name": "Hjemme"`. Det står også øverst på siden. Kald den ikke "Aula", så den ikke forveksles med Aula-appen. Siden virker kun med net.

Når adressen (`--url`) er sat, får begivenhederne i Google Calendar et link til item'ets detalje-side på web-siden (`/item/<item-key>`). Begivenheder, der allerede ligger i kalenderen, får linket, næste gang de bliver opdateret.

## I hverdagen

- **Login udløbet:** Natkørslen stopper og siger det. Så logger du ind igen med `uv run aula login` eller på web-siden. Det, der ikke er nået at blive behandlet, venter bare til næste kørsel.
- **Forkert begivenhed:** Fjern den på web-siden. Hvis du sletter den direkte i Google Calendar, kan den dukke op igen, næste gang institutionen skriver om det samme.
- **Del med den anden forælder:**
  - I Google Calendar: Indstillinger → *Aula – …* → Delt med → tilføj vedkommende med *Se alle begivenhedsdetaljer*. Gør det ikke med redigeringsret, for rettelser fra institutionen overskriver ændringer.
  - Vedhæftede filer ligger i Drive-mappen **"Aula"**. Den skal også deles, hvis den anden forælder skal kunne åbne filerne. Del **ikke** mappen "Aula-arkiv" (backuppen af arkivet).
- **Flere børn:** Hvert barn får sin egen kalender. Et nyt barn bliver fundet automatisk ved næste `fetch`. Aula fortæller ikke, hvilket barn et opslag gælder, så agenten afgør det ud fra klasse, stue og grupper og ud fra indholdet. Når det er uklart, lægger den begivenheden hos alle relevante børn.
- **Fælles kalender for flere børn:** Hvert barn hører til præcis én kalender, men flere børn kan dele den. Sæt det samme `calendar_id` (og `calendar_name`) på børnene i `data/config.json`. En begivenhed for flere børn i samme kalender bliver så til én post dér, ikke én pr. barn. Fjerner du den for ét barn på web-siden, bliver posten liggende for de andre og slettes først, når ingen af kalenderens børn er tilbage. En rettelse, der ikke længere nævner et af børnene, gør det samme.
  - Har du før ladet flere børn pege på samme kalender (den gamle omvej), kender poster fra dengang kun ét af børnene, indtil begivenheden bliver rettet. Der kommer ikke dubletter.

## Kommandoer

| Kommando | Hvad den gør |
|---|---|
| `aula-agent status` | Viser logins, børn, kalendere og hvad der venter |
| `aula-agent fetch` | Henter nye beskeder og opslag med filer til `data/inbox.json`, gemmer alle opslag og læste tråde i arkivet og tager backup |
| `aula-agent backfill [REDNINGSKOPI]` | Henter alt, hvad Aula stadig har (alle opslag og tråde med filer), til arkivet og tager backup. Med en sti læses en redningskopi i stedet for Aula. Rører ikke indbakken og kan køres igen |
| `aula-agent backup` | Kopierer arkivet og nye filer til den private Drive-mappe "Aula-arkiv" nu |
| `aula-agent reclassify` | Sletter alle classifications i arkivet (topics bliver), så agenten klassificerer alle items igen via køen, højst 100 pr. nat. Krydsede todos huskes stadig pr. version |
| `aula-agent apply FIL [--dry-run]` | Opretter, opdaterer og sletter begivenheder ud fra en events-fil |
| `aula-agent google-login` | Forbinder Google fra en terminal |
| `aula-agent web-setup [...]` | Viser eller ændrer web-sidens opsætning |
| `aula-agent pdf-text FIL` | Udskriver teksten i en PDF (bruges af agenter, der ikke selv kan læse PDF'er) |
| `aula-agent web` | Starter web-siden (normalt via `install-services.sh web`) |

Alle kommandoer køres som `uv run aula-agent …` fra projektmappen.

**Exit-koder:**

| Kode | Betydning |
|---|---|
| 3 | Aula-login mangler |
| 4 | Google-login mangler |
| 5 | Ugyldig events-fil (fx ugyldig JSON, items fra indbakken mangler i `processed`, eller et item mangler sin classification) |

## Filer

- `data/` (ikke i git): `config.json` (børn, kalendere, web-opsætning), `state.json` (hvad der er set og synkroniseret), `inbox.json`, `archive.sqlite` (arkivet), `attachments/` (filer og billeder fra Aula), Google-nøgle og -token, `logs/`
- `~/.config/aula/`: Aula-session (fra `aula`-biblioteket)
- `~/.config/aula-agent/tunnel-token`: Cloudflare-tunnel (kun hvis du bruger web-siden)

## Udvikling

```bash
uv run pytest -q
```

Begreberne (item, event, ledger, …) er forklaret i [`GLOSSARY.md`](GLOSSARY.md). Agentens fremgangsmåde står i [`AGENTS.md`](AGENTS.md): hvad der tæller som et arrangement, hvordan rettelser genkendes, og hvordan børn tildeles. Ret den, hvis agenten skal opføre sig anderledes.

## Licens

[MIT](LICENSE). Projektet er ikke tilknyttet Aula, KOMBIT, MitID, Google, Anthropic eller OpenAI. Du bruger det på eget ansvar.
