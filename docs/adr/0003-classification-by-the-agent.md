# Agenten klassificerer items i den samme natkørsel, og koden står for fakta

Aula inddeler ikke opslag og beskeder selv. Det eneste signal er `isImportant` med en fra–til-periode. Overblikket (#2) skal vise items efter category, span og topic. Det kræver forståelse af teksten, og agenten læser allerede hvert item i natkørslen. Derfor skriver agenten en **classification** pr. item version (category, span, topics, resumé og børn, hvis Aula ikke har sat dem) i events document. Koden står for alt, den selv kan vide sikkert: kilde, afsender, dato, børn fra Aula, Aulas vigtig-markering og koblingen til events. Har Aula sat børnene, vinder de over agentens.

Agent-kontrakten udvides:
- Inbox får `to_classify` (arkiv-items, hvis nyeste version mangler classification, højst 100 pr. nat) og `topics` (de eksisterende topics med beskrivelse).
- Events document får `classifications` og `new_topics`.
- Et processed item skal have en classification. For `to_classify` er den valgfri: et item uden bliver stående i køen.
- Items i `to_classify` giver aldrig events.
- Classification gemmes i arkivet pr. item version ved `apply`, også når Google-login mangler.

## Todo er et felt på classification, og listen styres af afkrydsning

Efter den første vurdering (gate #28) manglede der en måde at se, hvad man selv skal gøre: en betaling uden dato landede i stående info, og "tilmeld og betal senest …" i et arrangement stod kun i resuméet. En classification kan derfor have en **todo** (`{text, due}`). Den er et felt og ikke en syvende category, fordi opgaver findes i alle categories, og en category er kun den vigtigste slags information i item'et.

En todo forsvinder kun, når forælderen krydser den af (i Ledger, pr. item og item version, ikke pr. barn), også når datoen er passeret: så står den som overskredet. En ny item version giver en ny, åben todo. Koden, ikke agenten, afgør, om en todo kommer på listen: kun hvis den var aktuel, da classification'en blev gemt (`due` samme dag eller senere, eller ingen `due` på et item højst 14 dage gammelt). Andre starter som klaret, så køen og backfill ikke fylder listen, og kan genåbnes med "fortryd". `aula-agent reclassify` sletter alle classifications, så hele arkivet klassificeres igen med todo.

Fravalgt: todo som category (en frist-category findes, men en betaling i stående info eller en tilmelding i et arrangement ville så miste sin egentlige category), og at todos forsvinder af sig selv, når datoen er passeret (så ser man ikke det, man har glemt).

## Considered Options

- **Koden udleder inddelingen** (events → arrangement, `isImportant` → periode, ellers info): Billig og forudsigelig, men forkert, så snart agenten ikke har lavet et event (gammelt arrangement, noget der står i Aulas kalender, en periode der kun har ét event). Den giver hverken topics eller resumé.
- **En særskilt kommando og kørsel til inddeling:** Den ville klare backlog'en fra backfill, men er en ekstra kørsel at planlægge og huske. Køen i natkørslen gør det samme af sig selv, og en rettelse efter cutoff kommer automatisk i køen igen.
- **Classification pr. tråd:** Passer ikke med, at item-nøgler og versioner gælder pr. besked. En tråd kan rumme både en indbydelse og et referat.

## Consequences

- Classification hører til en item version. Når et item får en ny version, kommer det i køen igen.
- Loftet på 100 beskytter kalender-delen af natkørslen mod en stor backfill. Ved de nuværende mængder (cirka 5 items om ugen) slår det ikke til.
