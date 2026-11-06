# Aula for børnehaver: Komme/Gå og resten, set gennem `aula`-biblioteket

Research-ticket [#40](https://github.com/fMads/aula-agent/issues/40) på kortet
[#37](https://github.com/fMads/aula-agent/issues/37). Den skal give grundlag for grilling-ticket'en
"Børnehave: hvad skal aula-agent gøre for et barn i børnehave?" (#46).

Undersøgt 2026-11-06. Der er **ikke** kaldt noget mod det rigtige Aula, og der er ikke læst noget i `data/`.
Alt herunder stammer fra bibliotekets kode og dokumentation og fra offentlige kilder.
`docs/research/aula-api.md` (afsnit 8) har en kortere oversigt over presence-kaldene, inkl. hvad der virkede live
på én skolekonto 2026-11-05.

## Kilder

| Mærke | Kilde | Tillid |
|---|---|---|
| **[lib]** | `aula` v1.15.0 i `.venv/lib/python3.14/site-packages/aula/`: `api_client.py`, `models/presence*.py`, `models/daily_overview.py`, `models/pickup_responsible.py`, `models/vacation_registration.py`, `models/child.py`, `widgets/client.py`, `cli.py`, `const.py`. Linjenumre er fra den installerede version. | Primær (koden) |
| **[up]** | [nickknissen/aula](https://github.com/nickknissen/aula): PR [#63](https://github.com/nickknissen/aula/pull/63) (sygemelding), PR [#48](https://github.com/nickknissen/aula/pull/48) og issue [#47](https://github.com/nickknissen/aula/issues/47) (ferieforespørgsler), issue [#64](https://github.com/nickknissen/aula/issues/64) (`calendar.addVacation`, åbent). Upstream `main` 2026-11-06 og forks. | Primær for biblioteket. PR'erne citerer Aulas egne web-bundles. |
| **[guide]** | Aulas brugervejledning for medarbejdere, afsnit 4.9 Komme/gå (s. 115–160), offentliggjort af Gladsaxe Kommune: [49-Komme_gaa-p115-160.pdf](https://gpv.gladsaxe.dk/p/Subsites-Filer/GPV/Begivenheder/Aula-chatbot/49-Komme_gaa-p115-160.pdf). Afsnit 4.9.3 er forældrenes modul. | Primær (Aulas egen vejledning) |
| **[aulainfo]** | [aulainfo.dk – Komme/gå](https://aulainfo.dk/hjaelp-og-vejledninger/kommegaa) (KOMBIT). Indholdet indlæses dynamisk og kunne ikke hentes; kun via søgeresuméer. | Primær, men ikke læst direkte |
| **[ballerup]** | Ballerup Kommune, [Aula i dagtilbud](https://ballerup.dk/borger/boern-unge/dagtilbud/aula-i-dagtilbud) (anvendelsesstrategi for dagtilbud) | Primær for én kommune |
| **[albertslund]** | Albertslund Kommune, [Sådan gør vi i Aula på dagtilbudsområdet](https://test.albertslund.dk/media/4858509/saadan-goer-vi-i-aula-paa-dagtilbudsomraadet-version-111.pdf) (v1.11) | Primær for én kommune |
| **[re]** | [eisbaw/aulalibre](https://github.com/eisbaw/aulalibre) `re/api_endpoints.md`, `re/data_models.md`, `re/domain_concepts.md`: metodenavne, DTO'er og enums fra den dekompilerede mobilapp. REST-stierne i filen er gættede, men navnene er brugbare. | Sekundær, ikke verificeret |

**Verificeret** betyder herunder: læst i bibliotekets kode eller i Aulas egen vejledning. **Antaget** betyder:
udledt eller kun set i [re], ikke prøvet på en børnehavekonto. Ingen af kaldene er prøvet på et barn i dagtilbud.

---

## (a) Hvad bruger dagtilbud og SFO Aula til?

### Komme/Gå er dagtilbuddets og SFO'ens modul

- Komme/Gå viser, hvilke børn i **SFO og dagtilbud** der er kommet og gået, og forældre kan "indberette ferie og
  sygdom for deres barn eller meddele, hvem der henter barnet". [guide s. 115] Ballerup: "Vi bruger Komme/Gå
  modulet til når forældre tjekker barnet ind og ud, melder barnet syg på dagen og melder ferie." [ballerup]
- **Skolebørn er kun med via SFO.** "Når forældre melder sygdom og ferie/fri i Komme/Gå-modulet, er det kun
  medarbejdere i SFO og daginstitution, der bliver informeret … forældre til skolebørn … skal stadig melde
  sygdom og ferie/fri til skolen." [guide s. 147]
- **Forældrenes modul har fem faner** [guide 4.9.3, s. 137–153]:
  - **Dagens overblik**: barnets status i dag (fx Kommet, Sover, Syg, Gået, på tur, en fysisk placering) og
    sovetider: "I detaljeoverblikket kan medarbejdere og forældre se en oversigt over barnets sovetider for
    dagen". [guide s. 115, 158]
  - **Tider**: hentetype, henteansvarlig, aflevering og afhentning og en bemærkning. Tider kan gentages ugentligt
    eller hver anden uge og kun ligge inden for åbningstiden. "Det, der er muligt for forældrene at angive,
    afhænger af institutionens opsætning af Komme/Gå." [guide s. 142–145]
  - **Fravær**: meld syg (kun i dag; "Børn, der er sygemeldt gennem Komme/Gå, raskmeldes automatisk"), opret,
    ret eller slet ferie/fri for en periode med en note, og svar på **ferieanmodninger**. [guide s. 145–151]
  - **Henteansvarlige**: forældre er automatisk henteansvarlige og kan tilføje flere. [guide s. 152]
  - **Åbningstider og lukkedage**: generelle og særlige åbningstider og lukkedage. [guide s. 153]
- **Hentetyper:** Hentes af, Selvbestemmer, Sendes hjem, Går hjem med (og en afleveringstid). [guide; lib `models/presence.py` `ActivityType`]
- **Ferieanmodning** er institutionens spørgsmål til forældrene: kommer barnet i en ferieperiode? Den har
  *Fra/Til*, en **deadline for svar**, en "Tekst til forældre" (højst 400 tegn) og evt. krav om at angive
  tider. Forælderen svarer kommer/kommer ikke pr. dag. Efter deadline kan svaret ikke ændres. Lukkedage
  registreres automatisk som "kommer ikke". Medarbejderne kan sende en påmindelse "via beskedmodulet" til
  dem, der ikke har svaret. [guide 4.9.2, s. 130–136, 148–152]
- **Notifikationer:** forældre får en notifikation om nye ferieanmodninger, og når barnet tjekker ud via
  tjek-ind-skærmen. [guide s. 130, 153]
- Har man børn i flere institutioner, filtrerer man pr. barn i topbaren. [guide s. 155]

### Resten af Aula i dagtilbud

| Hvad | Hvordan dagtilbud bruger det | Kilde |
|---|---|---|
| **Opslag** | Kun personalet laver opslag. "Vi laver mindst ét opslag om ugen fra stuen til forældrene." Opslag kan markeres "særligt vigtigt", når de kræver en handling. | [ballerup], [albertslund 6.2] |
| **Beskeder** | Til en eller få modtagere og når man inviterer til svar. Hver stue har en **fællespostkasse**. | [ballerup], [albertslund 8.4] |
| **Galleri** | "Billeder fra ture og pædagogiske aktiviteter". Album kan linkes fra opslag. Børn tagges, og samtykke styrer, hvem der ser hvad. Billeder i opslag ryddes hver sommerferie. | [ballerup], [albertslund 12] |
| **Kalender** | Når forældre inviteres i børnehuset, og "til at sende ferietilmelding i efterårsferien, sommerferien, juleferien og så videre". | [ballerup] |
| **Filer / sikre filer** | Dokumentation af barnets udvikling og trivsel, samarbejde med PPR og sundhedsplejen, referater fra stuemøder og forældresamtaler. | [ballerup] |
| **Grupper** | "Forældre er automatisk tilknyttet barnets stue." Når et barn skifter fra vuggestue til børnehave i samme institution, kan forældrene stadig se de tidligere begivenheder; skifter barnet institution, kan de ikke. | [albertslund 3.1, 5.0] |
| **Dagbog** | Aula har **intet dagbogsmodul**. Det, der hedder "dagbog" lokalt, er efter alt at dømme stuens opslag med billeder og i Komme/Gå dagens bemærkning og sovetider. | **Antaget** (ingen af kilderne har et modul med det navn; [albertslund 2.5] nævner "dagbogen" i forbindelse med billeder) |
| **Ugeplaner / widgets** | Dagtilbud har ingen ugeplan: "daycare has no EasyIQ identity", og "daycare, typically, which has no week plan". | [lib `widgets/client.py:63`, `:612`, `cli.py:626`] |

---

## (b) Komme/Gå-kaldene og hvad `aula`-biblioteket kan

Alle kald går til `https://www.aula.dk/api/v25?method=presence.<metode>` med **samme session** som resten
(OAuth-scope `aula-sensitive`, MitID niveau 3). Der er **ikke** noget særskilt login eller scope til Komme/Gå. [lib `const.py`]
Om forælderen må læse eller skrive, styrer **institutionen pr. modul**, ikke login'et (se Tilladelser herunder).

### Læsning (R) – findes i v1.15.0

| Biblioteket | Aula-metode | Giver | Kilde |
|---|---|---|---|
| `get_daily_overview(child_id)` | `presence.getDailyOverview` | Dagens status (`PresenceState`), `check_in_time`/`check_out_time`, planlagt `entry_time`/`exit_time`, `exit_with` (**navn** på den der henter), `location`, `sleep_intervals`, `comment`, `main_group`. **Kun i dag**, der er ingen datoparameter. | [lib `api_client.py:550`, `models/daily_overview.py`] |
| `get_presence_templates(ipids, from, to)` | `presence.getPresenceTemplates` | Planlagte tider pr. dag (`DayTemplate`): `entry_time`, `exit_time`, `activity_type`, `exit_with`, `comment`, `repeat_pattern`, `is_on_vacation`, `spare_time_activity`. Biblioteket skriver selv, at parameternavnet "should be verified". | [lib `api_client.py:569`, `models/presence_template.py`] |
| `get_presence_registrations(ipids, from, to)` / `get_presence_registration_detail(id)` | `presence.getPresenceRegistrations` / `…Detail` | Historik pr. dag: status, planlagte tider og faktiske tjek ind/ud (detaljen har også `exit_with` og `comment`). **Et skolebarn og et dagtilbudsbarn i samme kald giver HTTP 400**, så biblioteket prøver igen ét barn ad gangen. | [lib `api_client.py:797–877`] |
| `get_presence_states(ipids)` | `presence.getPresenceStates` | Status lige nu pr. barn | [lib `api_client.py:894`] |
| `get_presence_configuration(child_ids)` | `presence.getPresenceConfigurationByChildIds` | Institution, tilladte hentetyper (`pickup`, `go_home_with`, `self_decider`) og **tilladelser pr. modul** (`permission()`, `can_edit()`) | [lib `api_client.py:921`, `models/presence_registration.py:130`] |
| `get_activity_overview(ipids, week, year)` | `presence.getActivityOverview` | Ugeoverblik: dage med aktiviteter (`title`, `start_time`, `end_time`) | [lib `api_client.py:947`] |
| `get_pickup_responsibles(child_ids)` | `presence.getPickupResponsibles` | Henteansvarlige: familie (`name`, `relation`) og gemte forslag. **Navne på voksne.** | [lib `api_client.py:766`, `models/pickup_responsible.py`] |
| `get_vacation_registrations(child_ids)` | `presence.getVacationRegistrationsByChildren` | **Ferieanmodninger** fra institutionen: `title`, `start_date`, `end_date`, `response_deadline`, `is_missing_answer`, `note_to_guardian`, `is_editable`, `is_presence_times_required`. Ikke forælderens egen ferie. Før PR #48 kaldte biblioteket medarbejder-metoden `presence.getVacationRegistrations`, som altid gav 400. | [lib `api_client.py:2393`, `models/vacation_registration.py`; up #47, #48] |

`PresenceState` (læses og skrives med samme tal): 0 Ikke kommet, 1 Syg, 2 Ferie/fri, 3 Til stede, 4 På tur,
5 **Sover**, 6 Til aktivitet, 7 Fysisk placering, 8 Gået, 9 Ikke mødt. [lib `models/presence.py`; up #63 citerer portalens bundle]

### Skrivning (W) – findes i v1.15.0, og agenten må aldrig kalde dem

| Biblioteket | Aula-metode | Gør | Kilde |
|---|---|---|---|
| `update_presence_template(ipid, by_date, entry_time, exit_time, activity_type, exit_with, comment, repeat_pattern, …)` | `presence.updatePresenceTemplate` (POST) | Sætter **mødetid, hentetid, hentetype og hvem der henter** for en dag, evt. gentaget ugentligt eller hver anden uge. CLI: `aula update-presence`. | [lib `api_client.py:607`, `cli.py:3643`] |
| `update_presence_status(ipids, status)` | `presence.updateStatusByInstitutionProfileIds` (POST) | **Melder syg** (eller tager sygemeldingen tilbage). Kun i dag, og "the institution sees the change immediately". CLI: `aula report-sick`. | [lib `api_client.py:711`, `cli.py:3990`; up #63] |

### Findes i Aula, men ikke i biblioteket

- **Meld ferie/fri:** `calendar.addVacation` (POST `{childIds, intervals:[{startDate,endDate}], comment}`,
  højst 250 tegn, start i dag eller senere, højst et år). Det er beskrevet ud fra portalens bundle i upstream-issue
  [#64](https://github.com/nickknissen/aula/issues/64) (åbent). Det er hverken på upstream `main` 2026-11-06 eller i
  nogen fork. [up]
- **Svar på en ferieanmodning:** `calendar.respondToVacationRegistrationRequest` (dage med kommer/kommer ikke og
  tider). Udtrykkeligt uden for #64. [up #64; re `RespondToVacationRegistrationRequestRequestDto`]
- Fra appen [re, **antaget**]: `GetClosedDays`, `GetGeneralOpeningHours`, `GetOpeningHoursByInstitutionCodes`,
  `GetSpecificOpeningHourOverview` (åbningstider og **lukkedage**), `GetVacationAnnouncementsByChildren`,
  `GetFutureVacationRequest`, `GetChildGoHomeWith`, `GetTemplateForDate`, og skrivende `UpdateSuggestionsForPickup`,
  `DeletePickupResponsible`, `DeleteVacation`, `AddSleepIntervals` (sandsynligvis kun for medarbejdere).
- Biblioteks-forks (`kasperhartwich`, `asssmidt`, `dinf60`, `RexifyIT/aula-fixes` m.fl.) er 0 commits foran upstream. [up]

### Tilladelser: institutionen bestemmer

- Institutionen slår hvert Komme/Gå-modul fra eller til pr. dashboard: `pickup_times`, `drop_off_time`,
  `daily_message`, `report_sick`, `vacation`, `field_trip`, `spare_time_activity`, `location`, `sleep`, `pickup_type`.
  For forælderen (`guardian_dashboard`) er hvert modul `deactivated`, `readable` eller `editable`.
  Portalen viser kun skriveknapperne ved `editable`. [lib `models/presence.py`, `PresenceConfiguration.can_edit`; up #63]
- Aulas vejledning siger det samme: "Hvis institutionen har slået sygemelding via Aula fra, vises 'Meld sygdom' ikke",
  og hvad forældre kan angive under Tider, "afhænger af institutionens opsætning". [guide s. 142; søgeresumé af kommunale vejledninger]
- Altså: samme login, intet ekstra scope, men **to børnehaver kan se forskellige ud** for den samme kode.

---

## (c) Dagtilbudsbarn mod skolebarn i data

| | Skolebarn | Barn i dagtilbud | Kilde |
|---|---|---|---|
| `Child` i `get_profile()` | `id` (institution profile id), `profile_id`, `name`, `institution_name`. **Ingen institutionstype.** | Samme felter | Verificeret [lib `models/child.py`] |
| Institutionstype | `InstitutionTypeEnum` = Unknown, School, **Daycare**, Municipality, Central på `Institution` i `getProfileContext → data.institutions[]` | Samme | **Antaget** [re `data_models.md` Institution]. Biblioteket læser ikke feltet (`grep institutionType` giver intet). |
| Barnets rolle | `InstitutionRole` har bl.a. `EarlyStudent`, `MiddleLateStudent`, `Child` | Formentlig `Child` | **Antaget** [re]. `institutionProfile.institutionRole` ligger i `Child._raw`. |
| Hovedgruppe | Klassen, fx "3.A" | **Stuen** ("Stue: Daycare group room"). Vuggestue og børnehave kan være forskellige stuer i samme institution. | Grupper verificeret [lib `get_groups`, `MainGroup.uni_group_type`]; stue [re; albertslund] |
| Grupper | Klasse, SFO-grupper | Stue, evt. aldersgruppe | [lib `api_client.py:1458`: "class and SFO groups"] |
| Komme/Gå | Kun hvis SFO | Ja, hvis institutionen bruger modulet | [guide] |
| Ugeplaner/widgets (MU, Meebook, EasyIQ …) | Ja, afhænger af skolen | **Nej** | Verificeret [lib `widgets/client.py:63`] |
| Aula calendar | Lektioner (`type: lesson`) og events | **Ingen lektioner**, kun events (forældrekaffe, ferietilmelding, lukkedage?) | Lektioner verificeret [lib]. Resten **antaget**: `EventType` har `Holiday`, `PresenceHoliday` og `VacationRegistration` [re]. |
| Beskedtråde | Alm. tråde | Også stuens fællespostkasse som afsender, og påmindelser om ferieanmodninger (`ThreadType.VacationRequestReminder`) | [ballerup; guide s. 136; re] |
| Notifikationer | … | Også `VacationResponseRequired`, `PresenceRegistrationUpdatedExitWith` | **Antaget** [re `NotificationEventType`] |

### Hvad det betyder for aula-agent i dag (verificeret i `src/aula_agent/aula_source.py`)

- **Items virker uden ændringer.** Opslag hentes med forælderens og børnenes institution profile ids
  (`aula_source.py:269`), beskeder pr. tråd. Det er det samme for et barn i dagtilbud. Med ét barn får alle items
  `child_ids=[barnet]` (`_only_child`). Med flere børn i forskellige institutioner afgør agenten barnet ud fra
  `regardingChildren` (`_child_hints`) og `institution` på barnet i inbox (`_child`, `_update_children`).
- **Aula calendar** hentes for alle børn i ét kald, og kun `lesson` springes over (`aula_source.py:388–413`). For et
  barn i dagtilbud er der ingen lektioner, så alt, hvad Aula har i kalenderen (også ferietyper, hvis de dukker
  op), lander i `aula_calendar`, og agenten springer det over. **Antaget**, ikke set på en børnehavekonto.
- **Intet fra Komme/Gå når agenten i dag.** aula-agent kalder ingen `presence.*`-metoder. Ferieanmodninger med
  svarfrist ses i dag kun, hvis institutionen også skriver et opslag, sender en besked (påmindelse) eller lægger
  et event i kalenderen.
- **Flere børn med blandede institutionstyper:** presence-kald skal laves **pr. barn**, fordi et samlet kald med
  et skolebarn uden SFO giver 400 [lib `api_client.py:806`].
- **Navne:** `exit_with`, `get_pickup_responsibles` og `PresenceRegistrationDetail.child_name` indeholder navne på
  voksne og børn. Efter AGENTS.md må de ikke i kalenderen eller i resuméer.

---

## Muligheder for "Børnehave: hvad skal aula-agent gøre?" (input til #46, ikke beslutninger)

1. **Ferieanmodninger som frister og todos.** `get_vacation_registrations` er read-only og giver `title`,
   periode, `response_deadline` og `is_missing_answer` direkte. Det passer til et `Frist: Svar på ferieanmodning`-event
   og en todo ("Svar på ferieanmodning i Aula"), som forsvinder, når `is_missing_answer` bliver falsk. Det kræver
   et nyt slags item eller en ny kilde ud over opslag og beskeder.
2. **Lukkedage og særlige åbningstider** i kalenderen. Det er nyttigt, men metoderne er ikke i biblioteket
   (kun [re]), så det kræver et bidrag upstream.
3. **Dagligdagen (status, sover, tjek ind/ud, hvem henter)** er ikke arrangementer. Det hører højst til web-siden
   (fx "i dag": hentes kl. 15:30 af …), og det rejser privatlivsspørgsmål på grund af navnene. `get_daily_overview`
   er kun "i dag", så en natkørsel ser næsten intet. Historik kræver `get_presence_registrations`.
4. **Skrive til Komme/Gå** (melde ferie, sætte hentetid, melde syg): biblioteket kan sætte tider og melde syg, men
   ikke melde ferie. Det strider mod aula-agents nuværende rolle (read-only, agenten må aldrig skrive i Aula),
   og institutionen ser ændringen med det samme. Det bør være en udtrykkelig produktbeslutning, hvis det overhovedet skal med.
5. **Ingen ugeplaner** for dagtilbud. De widget-ideer, der er skrevet til skolebørn, gælder ikke.

## Åbne spørgsmål, der kræver en børnehavekonto

- Står `institutionType` på `getProfileContext → institutions[]`, og hvilken `institutionRole` har et barn i dagtilbud?
- Hvilke `EventType`'er kommer i `calendar.getEventsByProfileIdsAndResourceIds` for et barn i dagtilbud
  (fx `PresenceHoliday`, `VacationRegistration`, lukkedage)?
- Svarer `get_presence_templates` med de parametre, biblioteket sender?
- Bliver forælderens egen ferie/fri (`calendar.addVacation`) synlig i et læsekald, og i hvilket? #64 siger, at
  den *ikke* kan antages at komme i `getVacationRegistrationsByChildren`.
