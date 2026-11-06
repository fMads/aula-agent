# aula-agent

Finder arrangementer og frister i forældrenes Aula og lægger dem i børnenes Google-kalendere. En LLM-agent læser indholdet; koden henter, husker og synkroniserer.

## Hvem

**Installation**:
Én kørende aula-agent for én familie med sin egen config, Ledger, arkiv, Aula-session og Google-login. Den har præcis én operator.
_Avoid_: Instans, deployment, opsætning

**Operator**:
Forælderen, der har installeret aula-agent og driver installationen: maskinen, natkørslen og nye logins. Installationen læser Aula med operatorens eget MitID. Andre forældre i familien kan bruge web-siden og kalenderne uden at være operator.
_Avoid_: Admin, ejer, vært, bruger (en bruger er enhver forælder, der bruger siden eller kalenderne)

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
Agentens fortolkning af én item version: hvad slags information det er, hvornår det gælder, emner, et resumé på én linje, evt. en todo og – når Aula ikke har sat dem – hvilke børn det gælder (ADR 0003). Har Aula sat børnene, vinder Aulas over agentens. Fakta som afsender, dato og koblingen til events hører ikke til den. Dem ved koden selv.
_Avoid_: Inddeling (kun i brugertekst), tagging, kategorisering

**Category**:
Den vigtigste slags information i en item version, én pr. version: arrangement, frist, periode, stående info, orientering eller svar. Svar er beskeder uden ny information ("Tak", "Ok"). Gemmes som `arrangement`, `frist`, `periode`, `info` (stående info), `orientering` og `svar`.
_Avoid_: Art (kun i brugertekst), kind (i koden er det opslag eller besked), type

**Span**:
Hvornår et item gælder: én dag eller fra–til. Agenten sætter den som en del af en classification. Den er ikke det samme som, hvornår item'et kom.
_Avoid_: Periode (det er en category), gyldighed

**Topic**:
Et emne, der binder items om det samme sammen ("svømning", "lejrskole"), med en linjes beskrivelse af, hvad der hører under det. Et item har 1–3 topics. Agenten genbruger et eksisterende topic, når det passer, og opretter kun et nyt, når intet passer.
_Avoid_: Emne (kun i brugertekst), tag, label

**Todo**:
Noget forælderen skal gøre (betale, tilmelde, aflevere, medbringe), med en kort tekst i bydeform og evt. en dato (`due`). Den er et valgfrit felt på en classification, ikke en category, fordi opgaver findes i alle categories. Den står på listen "Skal gøres", til forælderen krydser den af, også når datoen er passeret (så er den overskredet). Afkrydsning gælder hele item'et, ikke pr. barn. En ny item version har sin egen todo, der er åben igen. En todo, der ikke var aktuel, da den blev klassificeret (`due` passeret, eller ingen `due` på et item over 14 dage gammelt), starter som klaret.
_Avoid_: Opgave (kun i brugertekst), task, reminder, frist (det er en category og et event)

**Unclassified item**:
Et item i arkivet, hvis nyeste version mangler en classification, og som ikke er et pending item, fx fra backfill, ældre end cutoff eller efter `aula-agent reclassify` (der sletter alle classifications). Agenten får det i inbox for at klassificere det, ikke for at finde events i det.

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
Ledger gemmer den pr. (event key, kalender) med `child_ids`, Google-eventets id, titel, tid og source. Fjernes et barn, bliver entry'en med de andre børn; den slettes, når ingen er tilbage.
_Avoid_: Kalenderpost (kun i brugertekst)

**Removed by user**:
Et event, som en forælder har fjernet for ét barn på web-siden. Eventet må aldrig komme tilbage for det barn, heller ikke under en ny key. Eventets andre børn beholder det, også når de deler kalender med barnet.
_Avoid_: Ignored, deleted

**Ledger**:
Alt det, aula-agent husker mellem kørsler: sete items, pending items, synced entries, event history, hvad brugeren har fjernet, hvilke todos der er krydset af, og run-loggen.
_Avoid_: State, database

**Event history**:
De items, der har oprettet, rettet eller aflyst et event, hvert med hvad det gjorde og hvornår. Et aflyst event har stadig sin event history.
_Avoid_: Log, source (et event har én source ad gangen; historikken har dem alle)

**Plan**:
Det, en synkronisering vil gøre for hvert event og barn: opret, opdatér, slet eller spring over. Den vises ved en prøvekørsel.

## Overblikket

**Past**:
Et item, der ikke længere er aktuelt. Det gælder et arrangement, en frist eller en periode, hvis span er slut, og en orientering, et svar og et item uden classification 14 dage efter, de kom. Stående info bliver aldrig past. Past items skjules ikke, de dæmpes kun.
_Avoid_: Overstået (kun i brugertekst), udløbet (det er Aulas expireAt), arkiveret

**Overview**:
Web-siden, hvor arkivets items, events og todos vises efter, hvornår de gælder, med fritekstsøgning og et item ad gangen i fuld længde. Forsiden har Skal gøres (de åbne todos), I dag og i morgen (events, items og todos på de to dage), Gælder nu (perioder og arrangementer over flere dage, der er i gang, orienteringer, der ikke er past, og opslag med Aulas vigtig-markering) og Kommende (dag for dag fra i dag: items, hvis span begynder, events og todos med frist). Stående info (efter topic) og Tidligere (til og med i går, bagud) har hver deres side. Kørsler, logins og kalendere hører ikke til overblikket.
_Avoid_: Overblik (kun i brugertekst), dashboard, arkiv-side

**Aulas vigtig-markering**:
Et opslag, Aula har markeret som vigtigt fra–til. Det står under Gælder nu i den periode; uden slutdato i 14 dage fra startdatoen, uden startdato fra opslagets dato.
_Avoid_: Fremhævet, prioriteret

## Kørsler

**Run**:
En kørsel af en kommando (fetch, apply, login). Hver kørsel ender med et udfald i run-loggen.

**Outcome**:
Hvordan en kørsel endte: ok, Aula-login mangler, Google-login mangler, events document afvist eller fejl.
