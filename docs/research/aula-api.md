# Aula-API'et for en forælderkonto (via `aula` v1.15.0)

Et opslagskort over hvad `aula`-biblioteket fra PyPI kan hente for en forælder, som grundlag for nye
funktioner i aula-agent. aula-agent bruger i dag kun `get_profile`, `get_posts`,
`get_all_message_threads_result` / `get_all_messages_for_thread_result`, `download_file` og
`get_calendar_events` (se `src/aula_agent/aula_source.py`).

Undersøgt 2026-11-05.

## Kilder

| Kilde | Hvad | Tillid |
|---|---|---|
| **[lib]** `.venv/lib/python3.14/site-packages/aula/` (v1.15.0): `api_client.py`, `widgets/client.py`, `models/`, `const.py`, `cli.py`, `agent_skill.py`, `utils/download.py` | Kaldene, parametrene og modellerne | Primær |
| **[rel]** Release notes v1.11–v1.15 og issues i [nickknissen/aula](https://github.com/nickknissen/aula) (`gh release view`, `gh issue list`) | Ændringer og kendte fejl | Primær |
| **[live]** Få read-only-kald 2026-11-05 med `aula_source.open_client()` på én forælderkonto (få børn, én institution) | Hvad der faktisk svarer | Primær, men kun én konto |
| **[re]** [eisbaw/aulalibre](https://github.com/eisbaw/aulalibre) `re/api_endpoints.md`, `re/data_models.md`, `re/esdh_journaling_analysis.md` | Metodenavne og enums fundet ved dekompilering af mobilappen (v2.15.4). REST-stierne i den fil er gættede; kun metodenavnene er brugbare. | Sekundær, ikke verificeret |

Henvisninger som `api_client.py:1564` peger på linjenumre i den installerede v1.15.0.

## Fælles mekanik

- **Transport.** Alt går til `https://www.aula.dk/api/v25?method=<modul>.<metode>`. På HTTP 410 skifter
  biblioteket selv til næste version. v1.14.0 skiftede til v25, fordi Aula 2.17 afviser v24. [lib `const.py`, `api_client.py:405`; rel v1.14.0]
- **Svarets kuvert.** `{"status": {"code", "subCode", "message"}, "data": ...}`. Biblioteket kaster
  `HttpRequestError` når `code != 0` (`api_client.py:290`). Koder set live:
  - `10` (HTTP 404): **ukendt metode**. En opdigtet metode giver det samme. [live]
  - `40` (HTTP 400): metoden findes, men parametrene er forkerte eller mangler. [live]
  - `403`: adgang nægtet, eller datointervallet er for langt (kalender). [live]
  - `subCode` 8 = step-up til MitID niveau 3 kræves, 9 = ugyldigt token, 13 = session udløbet. [lib `api_client.py:148`]
- **Tre slags id'er**, og de må ikke blandes:
  - `Child.id` = barnets *institution profile id*. Det bruges af kalender, presence, ferie, grupper og beskedsøgning.
  - `Profile.institution_profile_ids` = forælderens egne institutionsprofiler **plus** børnenes. Det bruges af opslag, galleri og dokumenter.
    Opslag med kun børnenes id'er gav 0 opslag live; aula-agent sender allerede begge. [live; `aula_source.py`]
  - `child._raw["userId"]` (UniLogin) og `institutionProfile.institutionCode` bruges af widgets, sammen med
    `getProfileContext → data.userId` som `sessionUUID`. [lib `cli.py:589` `_get_widget_context`]
- **Step-up.** Sessionen er logget ind med scope `aula-sensitive` (niveau 3), og live var `isSteppedUp` sand.
  Følsomme tråde og dokumenter bør derfor være tilgængelige uden ekstra login. [lib `const.py`; live]
- **Moduler og widgets.** `get_profile_context()` → `data.pageConfiguration.moduleConfigurations` siger hvilke
  moduler institutionen har slået til, og `widgetConfigurations` hvilke widgets. `get_widgets()` returnerer
  kun dem med `aggregatedDisplayMode == "Shown"`. [lib `api_client.py:527`] Live: modulerne kalender, kontakter,
  dokumenter, galleri, beskeder, overblik og presence, og kun Meebook-widgets (0003, 0004).
- **Paginering.** `*_result()`-metoderne returnerer `PaginationResult(items, complete, termination_reason)` med
  grunde som `exhausted`, `cutoff`, `no_progress`, `max_pages` (højst 100 sider) og `inconsistent_total`. [lib `utils/pagination.py`; rel v1.13.0]
- **Filer.** `download_file(url)` henter bytes fra en signeret URL. Kun `Attachment.file.url`
  (fil eller billede) kan hentes. Sikre dokumenter har ingen URL. [lib `models/attachment.py`]

Forkortelser: **R** = read-only (kan bruges), **W** = skriver eller ændrer noget (må aldrig kaldes af agenten).

---

## 1. Opslag og kommentarer

| Metode | Aula-metode | Returnerer |
|---|---|---|
| `get_posts(ipids, page, limit)` R | `posts.getAllPosts` | `list[Post]` |
| `get_post(post_id)` R | `posts.getById` | `Post` eller `None` |
| `get_comments(parent_type, parent_id, limit=100)` R | `comments.getComments` | `list[Comment]` (`id`, `content_html`, `creator_name`, `created_at`) |

- **Post-felter** [lib `models/post.py`]: `title`, `content_html` (og `content_markdown`), `timestamp`,
  `publish_at`, `edited_at`, `expire_at`, `is_expired`, `is_important`, `important_from`/`important_to`,
  `shared_with_groups`, `attachments`, `comment_count`, `owner`. Rå felter derudover (live): `isBookmarked`,
  `relatedProfiles`, `pendingMedia`, `canCurrentUserReport`.
- **Paginering er forkert i biblioteket (live 2026-11-05):** `index` er et element-offset (`index=1, limit=5` gav opslag
  2–6), men biblioteket sender `index = page-1`, så side 2 overlapper side 1. Hele kontoen havde 9 opslag
  (7/8–28/10 2026, `hasMorePosts=false`). `expireAt` lå fra 6/11 2026 til 4/11 2027.
- **Grænser.** Siderne er 0-indekserede internt (`index = page-1`), og der er ingen `totalSize`. Live havde
  **alle** opslag en `expireAt`. Opslag kan altså udløbe og forsvinde fra Aula. [live] De ældste opslag
  der kunne nås, var få måneder gamle. Det kan skyldes udløb eller kontoens alder; det kan ikke afgøres.
- **Vigtige opslag.** `is_important` med `important_from`/`important_to` blev set live (2 på første side).
- **Filer.** Vedhæftede filer og billeder kan hentes. `parent_type` til kommentarer er `"post"` i CLI'en.
  Der var ingen opslag med kommentarer live, så kommentarer er ikke prøvet.
- **Bogmærke** (`BookmarkPost`) findes i appen, men ikke i biblioteket, og det er W. [re]
- **For aula-agent:** `is_important` + `important_to` kan vise "vigtigt lige nu" i et overblik, og
  `expire_at` siger at opslag skal arkiveres lokalt, før de udløber.

## 2. Beskeder og beskedsøgning

| Metode | Aula-metode | Returnerer |
|---|---|---|
| `get_message_threads(filter_on=None)` R | `messaging.getThreads` side 0 | `list[MessageThread]` (`thread_id`, `subject`, `_raw`) |
| `get_all_message_threads_result(cutoff_date)` R | samme, alle sider til cutoff | `PaginationResult[dict]` |
| `get_messages_for_thread(id, limit=5)` / `get_all_messages_for_thread_result(id)` R | `messaging.getMessagesForThread` | `Message` / rå dicts |
| `search_messages_result(child_ids, inst_codes, text, from_date, to_date, has_attachments, limit)` R | `search.findMessage` (POST) | `PaginationResult[Message]` |
| `get_message_folders(include_deleted)` R | `messaging.getFolders` | `list[MessageFolder]` |
| `get_common_inboxes(ipids)` R | `messaging.getCommonInboxes` | rå liste |
| `get_threads_in_bundle(bundle_id)` R | `messaging.getThreadsInBundle` | `list[MessageThread]` |
| `get_message_info(thread_id, message_id)` R | `messaging.getMessageInfoLight` | rå dict |
| `get_auto_reply()` R | `messaging.getAutoReply` | `AutoReply` |
| `mark_thread_read(thread_id)` **W** | `messaging.setLastReadMessage` | må ikke bruges |

- **Tråd-felter (rå, live):** `subject`, `startedTime`, `latestMessage{id, sendDateTime, text}`, `read`,
  `marked`, `muted`, `sensitive`, `isArchived`, `folderId`/`folderType`, `regardingChildren`, `recipients`,
  `creator`, `mailBoxOwner`, `numberOfBundleItems`, `threadEntityLinkDto`. Der er ingen `totalSize`;
  `moreMessagesExist` og `page` styrer pagineringen.
- **Message** [lib `models/message.py`]: `id`, `content_html`, `attachments`, `sender_name`, `send_datetime`.
  `messageType` kan også være `RecipientsRemoved`, `SystemReply`, `Forward`, `AutoReply`, `MessageDeleted`
  (set live). aula-agent bruger kun `Message`/`MessageEdited`.
- **Søgning** virker (live). Tom tekst gav alle beskeder (komplet, `exhausted`), og fritekst gav et
  delsæt. Hver hit har `searchMessage{id, messageType, sendDateTime, senderDisplayName, text, unread}`
  plus `threadId`, `thread`, `regardingChildren`, `sensitivityLevel`, `stepUpRequired` og `score`.
  Profil-id'erne skal **kun** være børn; en forælders id kan få Aula til at afvise kaldet. [lib `api_client.py:1866`]
  Kaldet gav HTTP 403 før v1.13.0. [rel v1.13.0]
- **Grænser.** `getThreads` er sorteret efter seneste aktivitet; `get_all_message_threads_result` stopper ved
  cutoff. Live gik postkassen kun nogle måneder tilbage (ca. 30 tråde, alle i mappen `normal`, ingen
  arkiverede, ingen følsomme). Fælles indbakker gav code 40 for forælderen. Det ventes, da de er for ansatte.
- **Filer.** Fil- og billedvedhæftninger kan hentes. I de undersøgte tråde var der kun `file` og `media`;
  ingen `document`- eller `link`-vedhæftninger.
- **For aula-agent:** `search_messages_result(text="")` er den enkleste måde at hente *hele* postkassen
  som flad liste (til arkiv), og `regardingChildren` på hits giver barnet direkte.

## 3. Sikre dokumenter

| Metode | Aula-metode | Status live |
|---|---|---|
| `get_secure_documents(ipids)` R | `secureDocument.getSecureDocuments` | **Virker ikke**: code 10 (ukendt metode) |
| `get_common_files(ipids)` R | `secureDocument.getCommonFiles` | **Virker ikke**: code 10 (ukendt metode) |

- **Modellen** `SecureDocument` [lib `models/document.py`]: `id`, `title`, `document_type`, `description`,
  `created_at`, `updated_at`, `owner_name`, `institution_code`, `is_read`. Der er **ingen fil-URL og ingen
  download-metode** i biblioteket. Også på upstream `main` bruger biblioteket de samme metodenavne. [lib; rel]
- **Det rigtige metodenavn** er efter alt at dømme `documents.getSecureDocuments` (i mobilappen: "URL ID 1201").
  [re `esdh_journaling_analysis.md`] Live findes metoden: GET med gættede parametre gav code 40, og POST med
  et gættet filter-body gav 403. De rigtige parametre er ikke fundet.
- **Download findes i appen** som eksport til PDF: `documents.createPDFForSingleDocument` og
  `documents.trackCreatePDFForSingleDocument`, og som ZIP: `documents.createArchiveForMultipleSecureDocuments`.
  Desuden `GetInternalDocumentDetails`/`GetExternalDocumentDetails` og `GetDocumentRevisions`. [re] Intet af det
  er i biblioteket eller verificeret. Eksporten *opretter* et job på serveren, så det skal vurderes, om
  det er acceptabelt.
- **Enums** [re `data_models.md`]: `DocumentTypeEnum` = External, Internal, Note, Richdocument;
  `DocumentCategoryEnum` = Agenda, PlanOfAction, Observation, EducationalNote, Summary, Note m.fl.
- **Vedhæftede sikre dokumenter** i opslag og beskeder kommer som `Attachment.document`
  (`id`, `title`, `document_type`, `can_access`, `is_deleted`), uden indhold. aula-agent skriver i dag
  "Sikkert dokument – kan kun ses i Aula".
- **For aula-agent:** Sikre dokumenter kan i dag kun *nævnes* i arkivet, ikke gemmes. Fuld understøttelse
  kræver reverse engineering af web-klientens kald og en ændring upstream.

## 4. Fælles filer

- `get_common_files` bruger samme forkerte metodenavn som ovenfor (code 10). Live findes `commonFiles.getCommonFiles`
  (code 40 = forkerte parametre), mens `documents.getCommonFiles` ikke findes (code 10). [live]
- Appen sorterer fælles filer efter `Title` eller `UpdatedTime` (`CommonFileSortEnum`), og notifikationerne
  `NewCommonFile`/`NewOrUpdatedCommonFile` findes. [re]
- Om filerne kan hentes, er ukendt; biblioteket har ingen model med URL.
- **For aula-agent:** Fælles filer (fx skoleårets kalender, ferieplan, ordensregler) er typisk "generel info"
  og ville passe i et arkiv, men det kræver at kaldet først findes og rettes upstream.

## 5. Kalender, vigtige datoer og fødselsdage

| Metode | Aula-metode | Status live |
|---|---|---|
| `get_calendar_events(child_ids, start, end)` R | `calendar.getEventsByProfileIdsAndResourceIds` (POST) | Virker; **45 dage OK, 60 dage → 403** |
| `get_calendar_event(event_id, occurrence_datetime)` R | `calendar.getEventById` | Virker; har `description` og `attachments` |
| `get_meeting_bookings(child_id, start, end)` R | samme som events, i bidder på 49 dage | Virker (0 møder i perioden) |
| `get_important_dates(limit, include_today)` R | `calendar.getImportantDates` | Virker (0 datoer) |
| `get_birthday_events(inst_codes, start, end)` R | `calendar.getBirthdayEventsForInstitutions` | Virker |
| `get_birthday_events_for_group(group_id, start, end)` R | `calendar.getBirthdayEventsForGroup` | Ikke prøvet |
| `get_event_types(inst_codes)` R | `calendar.getEventTypes` | Virker (~10 typer) |
| `get_events_for_institutions(inst_codes, start, end)` R | `calendar.getEventsForInstitutions` | 403 for forælder |
| `get_daily_aggregated_events(ipids, start, end)` R | `calendar.getDailyAggregatedEvents` | 403 for forælder |
| `get_events_by_group(group_id, start, end)` / `get_daily_event_count_for_group` R | `calendar.geteventsbygroupid` / `getDailyEventCountForGroup` | Ikke prøvet |

- **CalendarEvent** [lib `models/calendar_event.py`]: `id`, `title`, `start_datetime`, `end_datetime`,
  `teacher_name(s)`, `has_substitute`, `substitute_name(s)`, `location`, `belongs_to`. Lektioner med flere
  voksne flettes sammen (v1.11.0).
- **Rå felter i listen (live):** `type` (`lesson`, `event`, ...), `allDay`, `hasAttachments`,
  `responseRequired`, `responseDeadline`, `responseStatus`, `requiresNewAnswer`, `invitedGroups`,
  `oldStartDateTime`/`oldEndDateTime` (flyttet event), `repeating`, `private`, `addedToInstitutionCalendar`.
- **Detaljer** (`getEventById`, live): `description`, `attachments`, `invitees`, `inviteeGroups`,
  `isDeadlineExceeded`, `isDeleted`, `maximumNumberOfParticipants`, `eventClass`, `occurrenceDate`.
  Listen har kun titel og tid; beskrivelse og filer kræver ét kald pr. event.
- **Grænser.** Kalenderkaldet afvises over ca. en måned. aula-agent bruger bidder på 28 dage, og
  møde-metoden bruger 49 dage, fordi grænsen er "50 dage". [lib `api_client.py:1253`; live: 45 OK, 60 → 403]
  Live var der kun lektioner og ingen andre events i de næste 4 uger, og 0 events et år tilbage.
- **Fødselsdage** returnerer `name`, `birthday`, `mainGroupName`, `relatedChildrenIds`, altså andre børns navne.
  Ifølge AGENTS.md må andre børns navne ikke skrives i kalenderen.
- **For aula-agent:** `responseRequired`/`responseDeadline` på Aula-events er frister, som allerede står i Aula,
  men som kan vises i et overblik. `getEventById` giver beskrivelse og vedhæftninger, som kan arkiveres.

## 6. Ugeplaner og widgets

Alle widgets henter først et token med `aulaToken.getAulaToken&widgetId=<id>` (R) og kalder så leverandørens
eget API. Parametrene er `child_filter` = børnenes `userId`, `institution_filter` = institutionskoder og
`session_uuid` = `getProfileContext → data.userId`. [lib `widgets/client.py:235`, `cli.py:589`]
De gamle metoder på `AulaApiClient` (`get_mu_tasks`, `get_ugeplan`, `get_meebook_weekplan`, `get_easyiq_weekplan`,
`get_momo_courses`, `get_library_status`) er **deprecated**; brug `client.widgets.*`. [lib `api_client.py:1680`]

| Område | Metode (`client.widgets.`) | Widget-id | Returnerer |
|---|---|---|---|
| Min Uddannelse ugebrev | `get_ugeplan(widget_id, child_filter, inst_filter, week, session_uuid)` | 0029, ellers 0023 (SSO) | `MUWeeklyPerson → institutions → letters[group_name, content_html, week_number]` |
| Min Uddannelse opgaver | `get_mu_tasks(...)` | 0030, ellers 0023 | `MUTask`: `title`, `task_type`, `due_date`, `weekday`, `week_number`, `is_completed`, `course`, `classes`, `deep_link` |
| Meebook ugeplan | `get_meebook_weekplan(child_filter, inst_filter, week, session_uuid)` | 0004 | `MeebookStudentPlan → week_plan[date, tasks[type, title, content, pill, link_text]]` |
| Docendo ugeplan | `get_docendo_weekplan(week)` | 0057 | `DocendoWeekPlan(name=klasse, note, lessons[title, start/end, description, learning_goal, notes_for_students])`, ikke filtreret pr. barn |
| EasyIQ ugeplan/kalender | `get_easyiq_weekplan`, `get_easyiq_calendar_events` | 0128, 0001, 0142 | `Appointment` / `EasyIQCalendarEvent` |
| EasyIQ lektier | `get_easyiq_homework`, `get_easyiq_homework_events` | 0142 | `EasyIQHomework(title, description, due_date, subject, is_completed)` |
| MoMo forløb | `get_momo_courses(children, institutions, session_uuid)` | 0062 | `MomoUserCourses → courses[title]` |
| MoMo huskeliste | `get_momo_reminders(..., from_date, due_no_later_than)` | 0062 | `UserReminders → team_reminders[due_date, reminder_text, subject_name], assignment_reminders[...]` |
| Bibliotek (Cicero) | `get_library_status(widget_id, children, institutions, session_uuid)` | 0019 | `LibraryStatus(loans[title, author, due_date], longterm_loans, reservations)` |

- **Uge-format** er `YYYY-Wnn`. Meebook og Docendo kræver foranstillet nul (biblioteket retter selv). [lib `widgets/client.py:109`]
- **EasyIQ** har en skrøbelig session pr. barn (`SwitchChild`). Biblioteket nægter hellere end at returnere
  et forkert barns uge (#68, #73). Børnehavebørn har ingen EasyIQ-identitet. [lib; rel v1.13.0]
- **Meebook** fornyer selv sit JWT, når det udløber (v1.11.0).
- **Live:** Kun Meebook var vist. Meebook svarede med en plan med 5 dage, men 0 opgaver i både denne uge og
  6 uger tilbage, så skolen bruger den tilsyneladende ikke aktivt. Tokens kunne udstedes for MU- og
  MoMo-widgets, men leverandørernes API svarede 403, da widgets ikke er slået til.
- **Filer.** Ugeplaner er HTML eller tekst med links. Der er ingen fil-download i biblioteket.
- **For aula-agent:** Ugebreve og ugeplaner er **"info for en given uge"** i ren form. De kan hentes pr.
  `week` og arkiveres pr. uge, og opgaver og lektier med `due_date` kan blive frister. Det kræver at
  aula-agent først tjekker hvilke widgets kontoen har (`get_widgets()`).

## 7. Galleri og medier

| Metode | Aula-metode | Status live |
|---|---|---|
| `get_gallery_albums(ipids, limit=1000, index, sort_on, order_direction, filter_by)` R | `gallery.getAlbums` | Virker (~10 album, få måneder tilbage) |
| `get_album_pictures(ipids, album_id, limit)` R | `gallery.getMedia` | Virker med et rigtigt album-id (se nedenfor) |
| `get_media_by_id(media_id)` R | `gallery.getMediaById` | Virker: `file.url` (kan hentes), thumbnails, `tags` |
| `get_media_by_profile(child_id, limit)` R | `gallery.getMediaByInstitutionProfileId` | **code 10** (ukendt metode) |

- **Album-felter (live):** `id`, `title`, `description`, `creationDate`, `creator`, `sharedWithGroups`,
  `thumbnailsUrls`, `regardingInstitutionProfileId`.
- **Rettet 2026-11-05 (live):** code 40 kom af, at `getAlbums` som første album returnerer et album med `id: null`.
  Med et rigtigt album-id svarer `gallery.getMedia` med `data{album, results, totalSize, mediaCount, limit, startIndex}`.
  Null-albummet er barnets eget album: `userSpecificAlbum=true` uden `albumId` gav de billeder, barnet er tagget i.
  Hvert billede har `tags` (`id` = barnets institution profile id, plus `profileId`, `name`, `institutionCode`, ...).
  `index` er et element-offset, ikke et sidenummer, og `totalSize` passede ikke med antallet (21 mod 29).
  `filterBy="tagged"` på `getAlbums` filtrerer ikke. Et billede i fuld størrelse var ca. 0,5 MB.
- `utils/download.py` har `download_gallery_images`, `download_post_images` og `download_message_images` med et
  manifest (`.aula-archive.json`), der genkender filer på stabile id'er, selvom URL'erne skifter. [lib]
  Galleri-delen afhænger af `get_album_pictures`, som fejlede live.
- **For aula-agent:** Galleriet hører ikke til arrangementer. Det er kun relevant for et billedarkiv, og
  biblioteket har allerede en arkiv-funktion, man kan genbruge.

## 8. Fremmøde, afhentning og ferie (presence / Komme-Gå)

| Metode | Aula-metode | R/W |
|---|---|---|
| `get_daily_overview(child_id)` | `presence.getDailyOverview` | R (live: virker) |
| `get_presence_configuration(child_ids)` | `presence.getPresenceConfigurationByChildIds` | R (live: virker) |
| `get_presence_templates(ipids, from, to)` | `presence.getPresenceTemplates` | R (live: virker) |
| `get_presence_registrations(ipids, from, to)` / `get_presence_registration_detail(id)` | `presence.getPresenceRegistrations` / `...Detail` | R |
| `get_presence_states(ipids)` | `presence.getPresenceStates` | R |
| `get_activity_overview(ipids, week, year)` | `presence.getActivityOverview` | R |
| `get_pickup_responsibles(child_ids)` | `presence.getPickupResponsibles` | R (live: virker; indeholder navne) |
| `get_vacation_registrations(child_ids)` | `presence.getVacationRegistrationsByChildren` | R (live: 0) |
| `update_presence_template`, `update_presence_status` | `presence.updatePresenceTemplate`, `presence.updateStatusByInstitutionProfileIds` | **W**, må aldrig kaldes |

- **DailyOverview:** `status`, `check_in_time`/`check_out_time`, `entry_time`/`exit_time`, `exit_with`,
  `location`, `sleep_intervals`, `comment`, `main_group`.
- **VacationRegistration** er en *ferieforespørgsel*, som institutionen beder forælderen svare på:
  `title`, `start_date`, `end_date`, `response_deadline`, `is_missing_answer`, `note_to_guardian`. Kun Child.id
  må bruges. [lib `api_client.py:2393`]
- `get_presence_registrations` prøver ét barn ad gangen, hvis et samlet kald fejler (skolebarn + institution
  uden presence). [lib `api_client.py:797`]
- **For aula-agent:** Ferieforespørgsler med `response_deadline` er oplagte **frister** ("Frist: svar på
  ferieregistrering"). Resten er dagligdag og ikke arrangementer.

## 9. Grupper og kontakter

| Metode | Aula-metode | Bemærkning |
|---|---|---|
| `get_groups(child_institution_profile_ids=...)` R | `groups.getGroupsByContext` | Forælder skal filtrere på børn; begge filtre → HTTP 400. Live: ~7 grupper (`id`, `name`, `institutionCode`, `showAsDefault`; `groupType` tom) |
| `get_group(group_id)` / `get_group_members(group_id, portal_roles)` R | `groups.getGroupById` / `groups.getMembershipsLight` | Ikke prøvet |
| `get_contact_list(group_id, page, order, profile_type, field)` R | `profiles.getContactlist` | Alle parametre påkrævet; side 1-baseret, 20 pr. side |
| `get_contact_parents(page, order)` R | `profiles.getContactParents` | 403 for konti uden adgang |
| `search_groups(text)` / `find_profiles_and_groups(text)` / `find_recipients(text)` R | `search.searchGroups` / `findProfilesAndGroups` / `findRecipients` | Typeahead |
| `get_profile_master_data(ipid)` R | `profiles.getProfileMasterData` | E-mail, telefon, adresse |

- Barnets `institutionProfile` (fra `get_profile`) har allerede `groups` og `mainGroup`. [live]
- **For aula-agent:** Med gruppenavnene fra `get_groups` kan et opslags `shared_with_groups` kobles til et
  bestemt barn, når der er flere børn. I dag gætter agenten ud fra teksten.

## 10. Notifikationer

| Metode | Aula-metode |
|---|---|
| `get_notifications_for_active_profile(children_ids, institution_codes, offset, limit=50, module)` R | `notifications.getNotificationsForActiveProfile` |
| `get_notification_settings()` R | `notifications.getNotificationSettingsForActiveProfile` |

- **Felter:** `id`, `title`, `module` (`notificationArea`), `event_type` (`notificationEventType`),
  `created_at` (`triggered`), `expires_at`, `related_child_name`, `post_id`, `album_id`, `media_id`,
  `institution_profile_id`. Rå felter derudover: `relatedChildInstitutionProfileId`. [lib; live]
- **Rettet 2026-11-05 (live):** Uden filtre kom kun `NewMedia`. Med `children_ids` og `institution_codes` kom også
  `TaggedInMedia`, men de har **ingen** `mediaId`/`albumId`, kun `relatedChildInstitutionProfileId`. `NewMedia` har
  begge. `offset` ignoreres (samme 50 hver gang).
- **Live:** ~75 notifikationer, alle fra galleriet (`NewMedia`, `TaggedInMedia`). Notifikationerne ser ud til
  kun at være de ulæste/aktuelle, og Aula giver ikke en historik.
- **Mulige event-typer** [re]: bl.a. `InvitedToEventResponseRequired`, `EventChangedResponseRequired`,
  `EventCancelledNoReason`, `InvitedToParentalMeeting`, `PostSharedWithMe`, `NewOrUpdatedSecureDocument`,
  `NewCommonFile`, `VacationResponseRequired`, `InvitedToSurvey`, `NewPostComment`, `LessonNoteChanged`.
- **For aula-agent:** Notifikationer er en billig "hvad er nyt"-kilde. `VacationResponseRequired` og
  `...ResponseRequired` peger direkte på ting med svarfrist, og `relatedChildInstitutionProfileId` giver barnet.

## 11. Global søgning

- `search(text, doc_type, limit, offset)` R → `search.findGeneric`. **Live: HTTP 500 ("intern fejl")** med
  alle prøvede parametervarianter. Den virker altså ikke for denne konto med biblioteket i dag.
- Ifølge appen dækker søgningen Profile, Group, Child, Employee, Guardian, InternalSecureFile,
  ExternalSecureFile, CommonFile, Event, Post, CommonInbox, Message, Thread, ThreadMessage og Media. [re `domain_concepts.md`]
- Beskedsøgning (`search.findMessage`, afsnit 2) virker.
- **For aula-agent:** Aulas egen søgning kan ikke bruges som søgning i et arkiv. Arkivet må have sin egen
  søgning over det, aula-agent har gemt.

## 12. Andet

| Metode | Aula-metode | Bemærkning |
|---|---|---|
| `get_consent_responses(ipids)` R | `consents.getConsentResponses` | `title`, `status`, `responded_at`. Live: et par stykker |
| `get_profile_context()` R | `profiles.getProfileContext` | Moduler, widgets, `userId`, `isSteppedUp`, institutioner, børn med udvidet adgang (v1.15.0) |
| `keep_alive()` R | `profiles.keepAlive` | Forlænger sessionen |
| `is_logged_in()` R | `get_profile` | |

Fundet i appen, men ikke i biblioteket [re]: Personal Reference Data, Onboarding, spørgeskemaer (`InvitedToSurvey`),
`calendar.addVacation` (W; upstream issue #64) og `GetFutureVacationRequest`.

---

## Til designbeslutning (a): et arkiv over al Aula-information

- **Arkivér selv, og gør det fremad.** Opslag har `expireAt` (alle, live), og hverken opslag, beskeder eller
  notifikationer gik mere end nogle måneder tilbage live. aula-agent kan ikke regne med at hente historikken
  fra Aula senere, så arkivet skal fyldes løbende fra den natlige `fetch`, som i forvejen ser alle items.
- **Opslag:** `get_posts` (alle sider) + `get_post(id)` til opdateringer. Fuld HTML, metadata og filer kan
  gemmes. Kommentarer kræver ét kald pr. opslag med `comment_count > 0`.
- **Beskeder:** Hele postkassen kan hentes enten via tråde (`get_all_message_threads_result` med en gammel
  cutoff) eller fladt via `search_messages_result(text="")`, som returnerede alt og sagde `complete`. Filer
  kan hentes. Medtag `messageType`, så videresendte beskeder og systembeskeder kan skelnes.
- **Dokumenter:** Sikre dokumenter og fælles filer kan **ikke** hentes med v1.15.0, fordi metodenavnene er
  forkerte (code 10). De rigtige metoder findes (`documents.getSecureDocuments`, `commonFiles.getCommonFiles`),
  men parametrene kendes ikke, og download sker i appen via en PDF/ZIP-eksport, som opretter et job. Arkivet
  kan foreløbig kun gemme at et dokument findes (titel og type fra `Attachment.document`).
- **Kalender:** `getEventById` giver beskrivelse og vedhæftninger til Aula-events. Listen kan kun hentes i
  intervaller på højst ca. en måned.
- **Ugeplaner** (MU, Meebook, Docendo, EasyIQ) hentes pr. uge. Det er naturligt at arkivere dem pr. uge og
  barn, men kun for de widgets kontoen har.
- **Søgning i arkivet** skal laves lokalt; `search.findGeneric` svarede HTTP 500.
- **Stabile nøgler** findes overalt: post-id, tråd-id + besked-id, event-id, album-id, `Attachment.id`/`file.id`.
  Fil-URL'er er signerede og skifter, så filer skal kendes på id, ikke på URL
  (sådan gør bibliotekets `_Archive` i `utils/download.py`).

## Til designbeslutning (b): overblik delt i generel info, arrangementer og uge/periode

| Kategori | Bedste kilder | Felter der hjælper |
|---|---|---|
| **Generel info** (uden dato) | Opslag uden arrangement, fælles filer (ikke tilgængelige), samtykker | `is_important` + `important_from/to` siger hvor længe et opslag er "vigtigt"; `expire_at` hvornår det udløber |
| **Arrangementer og frister** | aula-agents egne events, Aula-kalenderen (`type != lesson`), `responseRequired`/`responseDeadline`, ferieforespørgsler (`response_deadline`, `is_missing_answer`), MU-opgaver/EasyIQ-lektier/MoMo-huskeliste (`due_date`), notifikationer med `...ResponseRequired` | Alle har en dato, der kan placeres i et forløb |
| **Info for en uge eller periode** | MU-ugebrev (`week_number`), Meebook/Docendo/EasyIQ-ugeplan (pr. `YYYY-Wnn`), `get_activity_overview(week, year)`, kalenderen for ugen | Ugeplanerne er allerede skåret pr. uge; ugebrevene har `group_name` til at koble til barnets klasse |

- Opslag og beskeder har ingen kategori i Aula. Opdelingen i generel info, arrangement og uge må laves af
  agenten (LLM'en), som det sker i dag for arrangementer.
- `is_important` er det eneste tegn fra Aula selv på, at et opslag er vigtigt.
- Der skal tjekkes pr. konto, hvilke ugeplan-kilder der findes. Live havde kontoen kun Meebook, og den var tom.

## Kendte fejl i `aula` v1.15.0 (set live, kan meldes upstream)

1. `get_secure_documents` og `get_common_files` kalder `secureDocument.*`, som Aula ikke kender (code 10).
2. `get_media_by_profile` (`gallery.getMediaByInstitutionProfileId`) er ukendt for Aula (code 10).
3. ~~`get_album_pictures` afvises med code 40~~: virker; fejlen var albummet med `id: null` (afsnit 7). Men
   `get_posts` (og sandsynligvis galleriet) sender sidenummer som `index`, der er et element-offset (afsnit 1).
4. `search` (`search.findGeneric`) giver HTTP 500.
5. `get_events_for_institutions` og `get_daily_aggregated_events` giver 403 for en forælder.
