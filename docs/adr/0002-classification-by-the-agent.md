# Agenten klassificerer items i den samme natkørsel, og koden står for fakta

Aula inddeler ikke opslag og beskeder selv. Det eneste signal er `isImportant` med en fra–til-periode. Overblikket (#2) skal vise items efter category, span og topic. Det kræver forståelse af teksten, og agenten læser allerede hvert item i natkørslen. Derfor skriver agenten en **classification** pr. item version (category, span, topics, resumé og børn, hvis Aula ikke har sat dem) i events document. Koden står for alt, den selv kan vide sikkert: kilde, afsender, dato, børn fra Aula, Aulas vigtig-markering og koblingen til events. Har Aula sat børnene, vinder de over agentens.

Agent-kontrakten udvides:
- Inbox får `to_classify` (arkiv-items, hvis nyeste version mangler classification, højst 100 pr. nat) og `topics` (de eksisterende topics med beskrivelse).
- Events document får `classifications` og `new_topics`.
- Et processed item skal have en classification. For `to_classify` er den valgfri: et item uden bliver stående i køen.
- Items i `to_classify` giver aldrig events.
- Classification gemmes i arkivet pr. item version ved `apply`, også når Google-login mangler.

## Considered Options

- **Koden udleder inddelingen** (events → arrangement, `isImportant` → periode, ellers info): Billig og forudsigelig, men forkert, så snart agenten ikke har lavet et event (gammelt arrangement, noget der står i Aulas kalender, en periode der kun har ét event). Den giver hverken topics eller resumé.
- **En særskilt kommando og kørsel til inddeling:** Den ville klare backlog'en fra backfill, men er en ekstra kørsel at planlægge og huske. Køen i natkørslen gør det samme af sig selv, og en rettelse efter cutoff kommer automatisk i køen igen.
- **Classification pr. tråd:** Passer ikke med, at item-nøgler og versioner gælder pr. besked. En tråd kan rumme både en indbydelse og et referat.

## Consequences

- Classification hører til en item version. Når et item får en ny version, kommer det i køen igen.
- Loftet på 100 beskytter kalender-delen af natkørslen mod en stor backfill. Ved de nuværende mængder (cirka 5 items om ugen) slår det ikke til.
