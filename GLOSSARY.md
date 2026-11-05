# aula-agent

Finder arrangementer og frister i forældrenes Aula og lægger dem i en Google-kalender pr. barn. En LLM-agent læser indholdet; koden henter, husker og synkroniserer.

## Fra Aula

**Item**:
Et Aula-opslag eller en Aula-besked, som agenten skal læse, med tekst og vedhæftede filer. Har en item-key som `post:123` eller `message:<tråd>:<id>`.
_Avoid_: Post (betyder kun opslag), besked (betyder kun besked)

**Intake**:
Reglerne for, hvilke items agenten får: nye eller ændrede siden sidst og ikke ældre end skæringsdatoen.
_Avoid_: Import, sync

**Inbox**:
Det, agenten får udleveret ved hver kørsel: ventende items, børnene, eksisterende og fjernede events og Aulas egen kalender.
_Avoid_: Queue, feed

**Pending item**:
Et item, der ligger i inbox, men som agenten endnu ikke har markeret som behandlet.

## Fra agenten

**Events document**:
Agentens svar på en inbox: de behandlede item-keys plus de events, der skal i kalenderen.
_Avoid_: Events-fil (kun når selve filen menes), output

**Event**:
Et arrangement eller en frist for et eller flere børn, med titel, tid og evt. vedhæftninger. Kan også være en aflysning.
_Avoid_: Begivenhed (i kode), appointment

**Event key**:
Den stabile identitet for et event på tværs af kørsler (`<item-key>#<n>`). Samme key betyder rettelse, ikke et nyt event.

**Agent contract**:
Aftalen mellem kode og agent om, hvordan inbox og events document ser ud.

## I kalenderen

**Child's calendar**:
Den Google-kalender, aula-agent opretter til ét barn ("Skole – <fornavn>").

**Synced entry**:
Ét event i ét barns kalender. Et event for to børn giver to synced entries.
_Avoid_: Kalenderpost (kun i brugertekst)

**Removed by user**:
Et event, som en forælder har fjernet på web-siden. Det må aldrig oprettes igen, heller ikke under en ny key.
_Avoid_: Ignored, deleted

**Ledger**:
Alt det, aula-agent husker mellem kørsler: sete items, pending items, synced entries, events fjernet af brugeren og run-loggen.
_Avoid_: State, database

**Plan**:
Det, en synkronisering vil gøre for hvert event og barn: opret, opdatér, slet eller spring over. Den vises ved en prøvekørsel.

## Kørsler

**Run**:
En kørsel af en kommando (fetch, apply, login). Hver kørsel ender med et udfald i run-loggen.

**Outcome**:
Hvordan en kørsel endte: ok, Aula-login mangler, Google-login mangler, events document afvist eller fejl.
