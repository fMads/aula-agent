# Hvert barn hører til én kalender, og flere børn kan dele den

Nogle forældre vil have én kalenderpost pr. barn, andre én fælles post for alle børn (#16). Vi har ikke lavet en indstilling for "layout". I stedet hører hvert barn til præcis én kalender, og flere børn kan dele den (en joint calendar). Én kalender pr. barn er bare den opsætning, hvor ingen deler. Man kan også blande, fx to børn i én kalender og et tredje i sin egen.

En synced entry er derfor (event, kalender), og den gemmer de af eventets børn, der hører til kalenderen. Er kalenderen en joint calendar, står børnenes short names i titlen og deres fulde navne i beskrivelsen. Det er koden, der skriver dem.

Agent-kontrakten ændres ikke. Agenten skriver stadig `child_ids` pr. event og ser *Removed by user* pr. barn, men den ved ikke, hvordan børnene er fordelt på kalendere. *Removed by user* er stadig pr. barn. I en joint calendar bliver barnet fjernet fra posten, og først når kalenderen ikke har flere af eventets børn, slettes posten.

## Considered Options

- **Et flag for hele installationen (pr. barn / fælles):** Det dækker ikke blandede familier, og det ville have to måder at gemme synced entries på.
- **Fælles kalender i stedet for én pr. barn:** Det fjerner en opsætning, der allerede er i brug.

## Consequences

- Peger flere børn på samme `calendar_id`, var det før en omvej, hvor kun det sidste barn blev husket, og en rettelse uden ét af børnene slettede posten for alle. Nu er det en joint calendar. En gammel entry beholder det ene barn, den kendte, indtil eventet bliver rettet igen.
- Når et barn skifter kalender (`calendars join`/`split`), flytter det sine kommende synced entries med. Fortidige poster bliver liggende, og kalendere slettes aldrig automatisk.
