# aula-agent

Finder arrangementer og frister i forældrenes Aula og lægger dem i en Google-kalender pr. barn. En LLM-agent læser indholdet; koden henter, husker og synkroniserer.

## Fra Aula

**Child**:
Et af forælderens børn i Aula. Hvert event gælder et eller flere børn, og hvert barn har sin egen kalender.
_Avoid_: Elev, profil

**Item**:
Et Aula-opslag eller én Aula-besked med tekst og vedhæftede filer. Hvilke items agenten får, afgør intake. Har en item-key som `post:123` eller `message:<tråd>:<id>`. Et svar i samme tråd er et nyt item.
_Avoid_: Post (betyder kun opslag), besked (betyder kun besked), tråd

**Item version**:
Et item, som det så ud på et bestemt tidspunkt. Når et item rettes i Aula, bliver det en ny version af samme item, ikke et nyt item.
_Avoid_: Revision, kopi

**Intake**:
Reglerne for, hvilke items agenten får: nye eller ændrede siden sidst og ikke ældre end cutoff.
_Avoid_: Import, sync

**Cutoff**:
Den dato, items skal være nyere end for overhovedet at nå agenten. Ældre items bliver aldrig udleveret, heller ikke senere.
_Avoid_: Skæringsdato (kun i brugertekst)

**Aula calendar**:
Aulas egen kalender for børnene. Står et arrangement allerede dér, laver agenten ikke et event for det.
_Avoid_: Kalender (uden præcisering)

**Inbox**:
Det, agenten får udleveret ved hver kørsel: ventende items, unclassified items, topics, børnene, eksisterende og fjernede events og Aulas egen kalender.
_Avoid_: Queue, feed

**Pending item**:
Et item, der ligger i inbox, men som agenten endnu ikke har markeret som behandlet.

## Arkivet

**Archive**:
Alt det, aula-agent har gemt fra Aula, med alle item versions, også efter Aula har slettet det. Arkivet fyldes med alt, hvad hver hentning ser, uanset cutoff. Intake bestemmer stadig alene, hvad agenten får.
_Avoid_: Historik, backup, database, Ledger (Ledger husker, hvad der er sket; arkivet gemmer indholdet)

**Tagged photo**:
Et billede i Aulas galleri, hvor et af børnene er tagget. Det er de eneste galleribilleder, arkivet gemmer. Billeder vedhæftet et item hører til item'et.
_Avoid_: Galleri, medie, billede (uden præcisering)

**Backfill**:
En hentning af alt, hvad Aula stadig har, direkte til arkivet. Den kan køres igen uden at give dubletter og giver aldrig items til agenten.
_Avoid_: Import, første kørsel

**Rescue copy**:
En mappe med Aulas rå data (opslag, tråde, filer), taget før Aula slettede noget, med datoen i navnet (`rescue-2026-11-05`). Backfill kan læse den i stedet for Aula; versionerne får kopiens dato.
_Avoid_: Backup (det er arkivets kopi i Drive), eksport

## Fra agenten

**Events document**:
Agentens svar på en inbox: de behandlede item-keys, de events og cancellations, kalenderne skal opdateres med, og en classification pr. item.
_Avoid_: Events-fil (kun når selve filen menes), output

**Event**:
Et arrangement eller en frist med titel, tid og evt. vedhæftninger. Et event nævner altid alle de børn, det gælder; et barn, der ikke er nævnt, har det ikke.
_Avoid_: Begivenhed (i kode), appointment

**Cancellation**:
Besked i et events document om, at et eksisterende event er aflyst for alle dets børn. Den består kun af eventets key.
_Avoid_: Aflyst event, sletning

**Deadline**:
Et event for en frist (tilmelding, aflevering), lagt som heldag på fristens dato med titlen `Frist: …`.
_Avoid_: Deadline-påmindelse, reminder

**Event key**:
Den stabile identitet for et event på tværs af kørsler (`<item-key>#<n>`). Samme key betyder rettelse, ikke et nyt event.

**Agent contract**:
Aftalen mellem kode og agent om, hvordan inbox og events document ser ud.

**Classification**:
Agentens fortolkning af én item version: hvad slags information det er, hvornår det gælder, emner og et resumé på én linje. Fakta som afsender, dato, barn og koblingen til events hører ikke til den. Dem ved koden selv.
_Avoid_: Inddeling (kun i brugertekst), tagging, kategorisering

**Category**:
Den vigtigste slags information i en item version, én pr. version: arrangement, frist, periode, stående info, orientering eller svar. Svar er beskeder uden ny information ("Tak", "Ok").
_Avoid_: Art (kun i brugertekst), kind (i koden er det opslag eller besked), type

**Span**:
Hvornår et item gælder: én dag eller fra–til. Agenten sætter den som en del af en classification. Den er ikke det samme som, hvornår item'et kom.
_Avoid_: Periode (det er en category), gyldighed

**Topic**:
Et emne, der binder items om det samme sammen ("svømning", "lejrskole"), med en linjes beskrivelse af, hvad der hører under det. Et item har 1–3 topics. Agenten genbruger et eksisterende topic, når det passer, og opretter kun et nyt, når intet passer.
_Avoid_: Emne (kun i brugertekst), tag, label

**Unclassified item**:
Et item i arkivet, hvis nyeste version mangler en classification, og som ikke er et pending item, fx fra backfill eller ældre end cutoff. Agenten får det i inbox for at klassificere det, ikke for at finde events i det.

## I kalenderen

**Child's calendar**:
Den Google-kalender, aula-agent opretter til ét barn ("Skole – <fornavn>").

**Synced entry**:
Ét event i ét barns kalender. Et event for to børn giver to synced entries.
_Avoid_: Kalenderpost (kun i brugertekst)

**Removed by user**:
En synced entry, som en forælder har fjernet på web-siden. Eventet må aldrig komme i det barns kalender igen, heller ikke under en ny key; eventets andre børn beholder det.
_Avoid_: Ignored, deleted

**Ledger**:
Alt det, aula-agent husker mellem kørsler: sete items, pending items, synced entries, event history, hvad brugeren har fjernet, og run-loggen.
_Avoid_: State, database

**Event history**:
De items, der har oprettet, rettet eller aflyst et event, hvert med hvad det gjorde og hvornår. Et aflyst event har stadig sin event history.
_Avoid_: Log, source (et event har én source ad gangen; historikken har dem alle)

**Plan**:
Det, en synkronisering vil gøre for hvert event og barn: opret, opdatér, slet eller spring over. Den vises ved en prøvekørsel.

## Overblikket

**Past**:
Et item, der ikke længere er aktuelt. Det gælder et arrangement, en frist eller en periode, hvis span er slut, og en orientering kort tid efter den kom. Stående info bliver aldrig past. Past items skjules ikke, de dæmpes kun.
_Avoid_: Overstået (kun i brugertekst), udløbet (det er Aulas expireAt), arkiveret

**Overview**:
Web-siden, hvor arkivets items vises efter span og category, med fritekstsøgning og et item ad gangen i fuld længde.
_Avoid_: Overblik (kun i brugertekst), dashboard, arkiv-side

## Kørsler

**Run**:
En kørsel af en kommando (fetch, apply, login). Hver kørsel ender med et udfald i run-loggen.

**Outcome**:
Hvordan en kørsel endte: ok, Aula-login mangler, Google-login mangler, events document afvist eller fejl.
