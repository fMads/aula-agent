# aula-agent

Finder arrangementer og frister i forældrenes Aula og lægger dem i børnenes Google-kalendere. En LLM-agent læser indholdet; koden henter, husker og synkroniserer.

## Fra Aula

**Child**:
Et af forælderens børn i Aula. Hvert event gælder et eller flere børn, og hvert barn hører til præcis én kalender.
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
Det, agenten får udleveret ved hver kørsel: ventende items, børnene, eksisterende og fjernede events og Aulas egen kalender.
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
Agentens svar på en inbox: de behandlede item-keys plus de events og cancellations, kalenderne skal opdateres med.
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

## I kalenderen

**Calendar**:
En Google-kalender, som aula-agent lægger events i, for et eller flere børn. Hvert barn hører til præcis én kalender. Om børnene har hver sin, eller nogle deler én, vælger hver installation selv.
_Avoid_: Child's calendar, Aula calendar (det er Aulas egen)

**Joint calendar**:
En calendar for flere børn. Hvert event i den viser i titlen, hvilke af kalenderens børn det gælder, og i beskrivelsen deres fulde navne. En kalender for ét barn gør ingen af delene.
_Avoid_: Delt kalender, shared calendar (at dele en kalender er noget, man gør med andre i Google), fælles kalender (kun i brugertekst)

**Short name**:
Det korte navn for et barn, som står i titlen på events i en joint calendar, fx et kælenavn eller ét bogstav. Er der ikke valgt et, er det barnets fornavn.
_Avoid_: Nickname, kælenavn (kun i brugertekst), kaldenavn

**Synced entry**:
Ét event i én kalender for de af eventets børn, der hører til den kalender. Et event for to børn giver to synced entries, når de har hver sin kalender, og én, når de deler.
_Avoid_: Kalenderpost (kun i brugertekst)

**Removed by user**:
Et event, som en forælder har fjernet for ét barn på web-siden. Eventet må aldrig komme tilbage for det barn, heller ikke under en ny key. Eventets andre børn beholder det, også når de deler kalender med barnet.
_Avoid_: Ignored, deleted

**Ledger**:
Alt det, aula-agent husker mellem kørsler: sete items, pending items, synced entries, hvad brugeren har fjernet, og run-loggen.
_Avoid_: State, database

**Plan**:
Det, en synkronisering vil gøre for hvert event og barn: opret, opdatér, slet eller spring over. Den vises ved en prøvekørsel.

## Kørsler

**Run**:
En kørsel af en kommando (fetch, apply, login). Hver kørsel ender med et udfald i run-loggen.

**Outcome**:
Hvordan en kørsel endte: ok, Aula-login mangler, Google-login mangler, events document afvist eller fejl.
