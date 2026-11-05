# Arkivet ligger i sin egen SQLite-fil, ikke i Ledger

Aula sletter opslag og beskeder efter nogle måneder (alle opslag har `expireAt`). Derfor gemmer aula-agent selv alt, hvad den henter, i arkivet. Det gemmes i `data/archive.sqlite` med Aulas rå JSON og alle item versions. Vedhæftninger og billeder ligger som filer under `data/`. Hver nat kopieres arkivet til en privat Google Drive-mappe, for originalerne findes ikke længere i Aula.

Ledger (`data/state.json`) husker, hvad der er sket mellem kørsler, og bliver indlæst og skrevet helt hver gang. Indhold, der vokser uden grænse, hører ikke til dér. SQLite giver fritekstsøgning (FTS5) uden ekstra afhængigheder, når søgningen bygges (#2), og ClickHouse og DuckDB kan læse filen direkte.

## Considered Options

- **I Ledger:** Indhold og hukommelse ville blive blandet sammen, og filen ville vokse for evigt.
- **Én JSON-fil pr. item:** Simpelt, men søgning og versioner skulle laves i hånden.
- **Parquet:** Lavet til store mængder analysedata. Her er der få hundrede items om året.
