import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest
from aula.models.calendar_event import CalendarEvent
from aula.models.pagination import PaginationResult
from aula.models.post import Post

from aula_agent import archive, aula_source, gcal, store
from aula_agent.credentials import GoogleLoginRequired
from aula_agent.ledger import Ledger
from conftest import FakeGoogle

NOW = dt.datetime.now(aula_source.TZ)


def iso(days_ago: float) -> str:
    return (NOW - dt.timedelta(days=days_ago)).isoformat()


def post(pid, title, days_ago, *, html="<p>Hej</p>", attachments=(), edited=None):
    return Post.from_dict(
        {
            "id": pid,
            "title": title,
            "content": {"html": html},
            "timestamp": iso(days_ago),
            "editedAt": edited,
            "ownerProfile": {"id": 3, "profileId": 4, "fullName": "Lærer Hansen"},
            "sharedWithGroups": [{"name": "2.A"}],
            "attachments": list(attachments),
        }
    )


def file_att(aid, name, url="https://files/x"):
    return {"id": aid, "name": name, "file": {"id": aid, "name": name, "url": url}}


GUARDIAN_PROFILE = 999


class FakeAula:
    """Behaves like Aula: posts only come back when the guardian's profile is in the request."""

    def __init__(self, children, posts, threads, messages):
        self.children = children
        self.posts = posts
        self.threads = threads
        self.messages = messages
        self.downloads = []
        self.post_requests = []
        self.calendar_ranges = []
        self.incomplete_threads = set()
        self.broken_urls = set()
        self.thread_cutoffs = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_profile(self):
        return SimpleNamespace(children=self.children, institution_profile_ids=[GUARDIAN_PROFILE, *(c.id for c in self.children)])

    async def get_posts(self, ids, page=1, limit=10):
        self.post_requests.append(list(ids))
        if GUARDIAN_PROFILE not in ids:
            return []
        offset = page - 1  # the library sends page-1 as index, which Aula reads as an element offset
        return self.posts[offset : offset + limit]

    async def get_all_message_threads_result(self, cutoff):
        self.thread_cutoffs.append(cutoff)
        listed = [t for t in self.threads if dt.datetime.fromisoformat(t["lastMessageDate"]).date() >= cutoff]
        return PaginationResult(items=listed, complete=True, termination_reason="cutoff")

    async def get_all_messages_for_thread_result(self, thread_id):
        complete = thread_id not in self.incomplete_threads
        return PaginationResult(items=self.messages[thread_id], complete=complete, termination_reason="exhausted" if complete else "error")

    async def get_calendar_events(self, ids, start, end):
        self.calendar_ranges.append((start.date(), end.date()))
        if (end - start).days > 31:
            raise RuntimeError("Aula rejected calendar (status code 403)")
        mk = lambda eid, title, raw: CalendarEvent(  # noqa: E731
            id=eid, title=title, start_datetime=NOW, end_datetime=NOW, teacher_name=None,
            has_substitute=False, substitute_name=None, location=None, belongs_to=ids[0], _raw=raw,
        )
        return [mk(1, "Dansk", {"type": "lesson"}), mk(2, "Motionsdag", {"type": "event"})]

    async def download_file(self, url):
        self.downloads.append(url)
        if url in self.broken_urls:
            raise RuntimeError("503")
        return b"%PDF-1.4"


def child(cid, name):
    return SimpleNamespace(id=cid, name=name, institution_name="Skolen")


@pytest.fixture
def aula(monkeypatch, google):
    """``holder["client"]`` is the Aula fetch talks to; ``holder["google"]`` the Google it backs up to."""
    holder = {"google": google}

    async def open_client():
        return holder["client"]

    def connect_google():
        if isinstance(holder["google"], Exception):
            raise holder["google"]
        return holder["google"]

    monkeypatch.setattr(aula_source, "open_client", open_client)
    monkeypatch.setattr(gcal.Google, "connect", staticmethod(connect_google))
    return holder


def run_fetch():
    return asyncio.run(aula_source.fetch())


def test_first_fetch_collects_recent_items_with_attachments(aula):
    thread = {"id": 77, "subject": "Juleklip", "lastMessageDate": iso(1)}
    msgs = {
        "77": [
            {"id": "m1", "messageType": "Message", "text": {"html": "<p>Fredag d. 4. er der juleklip</p>"},
             "sendDateTime": iso(1), "sender": {"fullName": "Lærer Hansen"},
             "attachments": [file_att(5, "indbydelse.pdf")]},
            {"id": "e1", "messageType": "RecipientAdded"},
        ]
    }
    aula["client"] = FakeAula(
        [child(1, "Emma Jensen")],
        [post(10, "Ny post", 2), post(11, "Gammel post", 90)],
        [thread],
        msgs,
    )
    summary = run_fetch()

    assert sorted(summary["new"]) == ["message:77:m1", "post:10"]
    inbox = json.loads(store.INBOX_FILE.read_text())
    msg = next(i for i in inbox["items"] if i["type"] == "message")
    assert msg["child_ids"] == [1]  # only one child -> it's about that child
    assert msg["attachments"][0]["path"] == "attachments/message_77_m1/5-indbydelse.pdf"
    assert (store.DATA_DIR / msg["attachments"][0]["path"]).read_bytes() == b"%PDF-1.4"
    assert [e["title"] for e in inbox["aula_calendar"]] == ["Motionsdag"]  # lessons dropped
    assert inbox["children"] == [{"id": 1, "name": "Emma Jensen", "institution": "Skolen"}]
    assert aula["client"].post_requests == [[GUARDIAN_PROFILE, 1]]
    assert Ledger.load().pending_item("post:10")["child_ids"] == [1]
    # Aula's own calendar is read in chunks Aula accepts
    ranges = aula["client"].calendar_ranges
    assert len(ranges) == 4 and all((b - a).days < 31 for a, b in ranges)
    assert store.load_config()["children"]["1"]["name"] == "Emma Jensen"


def test_second_fetch_only_returns_new_or_changed(aula):
    client = FakeAula([child(1, "Emma")], [post(10, "Post", 1)], [], {})
    aula["client"] = client
    run_fetch()
    assert run_fetch()["new"] == []

    client.posts = [post(10, "Post", 1, html="<p>Rettet: torsdag</p>", edited=iso(0))]
    assert run_fetch()["new"] == ["post:10"]
    assert Ledger.load().pending_item("post:10")["body"].startswith("Rettet")


def test_posts_with_two_children_are_left_for_claude_with_groups(aula):
    aula["client"] = FakeAula([child(1, "Emma"), child(2, "Oscar")], [post(10, "Motionsdag", 1)], [], {})
    run_fetch()
    item = Ledger.load().pending_item("post:10")
    assert item["child_ids"] == [] and item["groups"] == ["2.A"]


def test_old_post_seen_for_first_time_later_is_skipped(aula):
    client = FakeAula([child(1, "Emma")], [], [], {})
    aula["client"] = client
    run_fetch()
    client.posts = [post(20, "Gammelt opslag", 60), post(21, "Gammelt men rettet", 60, edited=iso(0))]
    assert run_fetch()["new"] == ["post:21"]


def test_message_child_unknown_with_two_children_unless_hinted(aula):
    threads = [
        {"id": 1, "subject": "a", "lastMessageDate": iso(1)},
        {"id": 2, "subject": "b", "lastMessageDate": iso(1), "regardingChildren": [{"displayName": "Oscar"}]},
    ]
    m = lambda mid: [{"id": mid, "messageType": "Message", "text": {"html": "x"}, "sendDateTime": iso(1)}]  # noqa: E731
    aula["client"] = FakeAula([child(1, "Emma"), child(2, "Oscar")], [], threads, {"1": m("a"), "2": m("b")})
    run_fetch()
    ledger = Ledger.load()
    assert ledger.pending_item("message:1:a")["child_ids"] == []  # the agent decides from the content
    assert ledger.pending_item("message:2:b")["child_ids"] == [2]


def image(i):
    return {"id": i, "media": {"mediaType": "image", "file": {"id": i, "name": f"{i}.jpg", "url": f"https://files/u{i}"}}}


def test_photo_gallery_is_archived_but_only_noted_in_the_inbox(aula):
    secure = {"id": 90, "document": {"id": 9, "title": "Samtykke", "documentType": "Generic"}}
    link = {"id": 91, "link": {"service": "OneDrive", "name": "Mappe", "url": "https://onedrive/x"}}
    attachments = [*(image(i) for i in range(5)), secure, link]
    client = FakeAula([child(1, "Emma")], [post(10, "Billeder fra turen", 1, attachments=attachments)], [], {})
    aula["client"] = client
    run_fetch()

    assert sorted(client.downloads) == sorted(f"https://files/u{i}" for i in range(5))
    files = {f.name: f for f in archived("post:10").files}
    assert files["3.jpg"].kind == "image" and files["3.jpg"].path == "attachments/post_10/3-3.jpg"
    assert (store.DATA_DIR / files["3.jpg"].path).read_bytes() == b"%PDF-1.4"
    assert (files["Samtykke"].kind, files["Samtykke"].detail, files["Samtykke"].path) == ("secure_document", "Generic", None)
    assert (files["Mappe"].kind, files["Mappe"].detail) == ("link", "https://onedrive/x")

    atts = Ledger.load().pending_item("post:10")["attachments"]
    images = [a for a in atts if a["kind"] == "image"]
    assert len(images) == 5 and all("path" not in a and "billedgalleri" in a["note"] for a in images)
    assert {a["kind"] for a in atts} == {"image", "secure_document", "link"}


def test_expired_session_never_starts_mitid(monkeypatch, tmp_path):
    token_file = tmp_path / "tokens.json"
    token_file.write_text("{}")
    monkeypatch.setattr("aula.config.DEFAULT_TOKEN_FILE", token_file)
    monkeypatch.setattr("aula.config.load_config", lambda: {"mitid_username": "x"})

    async def fake_auth(username, storage, on_login_required):
        on_login_required()  # what the library does right before MitID
        raise AssertionError("MitID login must not start")

    monkeypatch.setattr(aula_source, "authenticate_and_create_client", fake_auth)
    with pytest.raises(aula_source.LoginRequired):
        asyncio.run(aula_source.open_client())


def message(mid, days_ago=1, **kw):
    return {"id": mid, "messageType": "Message", "text": {"html": f"<p>{mid}</p>"}, "sendDateTime": iso(days_ago), **kw}


def test_incompletely_read_thread_is_read_again(aula):
    thread = {"id": 5, "subject": "Tur", "lastMessageDate": iso(1)}
    client = FakeAula([child(1, "Emma")], [], [thread], {"5": [message("a")]})
    client.incomplete_threads = {"5"}
    aula["client"] = client
    first = run_fetch()
    assert first["new"] == ["message:5:a"] and any("Tråd 5" in w for w in first["warnings"])

    client.incomplete_threads = set()
    client.messages["5"].append(message("b"))  # the rest of the thread
    assert run_fetch()["new"] == ["message:5:b"]
    assert run_fetch()["new"] == []  # now it is read completely


def test_failed_download_is_retried_and_handed_over_again_once_it_works(aula, data_dir):
    p = post(10, "Indbydelse", 1, attachments=[file_att(5, "indbydelse.pdf", url="https://files/broken")])
    client = FakeAula([child(1, "Emma")], [p], [], {})
    client.broken_urls = {"https://files/broken"}
    aula["client"] = client

    assert run_fetch()["new"] == ["post:10"]  # handed over without the file, with a warning
    ledger = Ledger.load()
    ledger.mark_processed(["post:10"])  # the agent handled it
    ledger.save()
    assert run_fetch()["new"] == []  # still broken: not handed over again
    assert len(client.downloads) == 2  # but it was tried again

    client.broken_urls = set()
    assert run_fetch()["new"] == ["post:10"]  # now with the file
    assert Ledger.load().pending_item("post:10")["attachments"][0]["path"].endswith("indbydelse.pdf")
    assert run_fetch()["new"] == []


def test_all_40_new_posts_reach_the_inbox_once(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(i, f"Opslag {i}", 0.1 * i) for i in range(1, 41)], [], {})
    summary = run_fetch()
    inbox_keys = [i["key"] for i in json.loads(store.INBOX_FILE.read_text())["items"]]
    assert sorted(inbox_keys) == sorted(f"post:{i}" for i in range(1, 41))
    assert summary["warnings"] == []


def test_posts_older_than_the_cutoff_are_read_but_not_handed_over(aula):
    posts = [post(i, f"Nyt {i}", 0.1 * i) for i in range(1, 36)] + [post(100 + i, f"Gammelt {i}", 60 + i) for i in range(30)]
    client = FakeAula([child(1, "Emma")], posts, [], {})
    aula["client"] = client
    summary = run_fetch()
    assert sorted(summary["new"]) == sorted(f"post:{i}" for i in range(1, 36))
    assert len(client.post_requests) == 3  # read past the old posts to the short last page


def test_paging_stops_at_an_empty_page(aula):
    client = FakeAula([child(1, "Emma")], [post(i, f"Opslag {i}", 0.1 * i) for i in range(1, 61)], [], {})
    aula["client"] = client
    summary = run_fetch()
    assert len(summary["new"]) == 60 and summary["warnings"] == []
    assert len(client.post_requests) == 3  # two full pages, then an empty one


def test_paging_warns_when_the_page_limit_is_reached(aula, monkeypatch):
    monkeypatch.setattr(aula_source, "MAX_POST_PAGES", 2)
    client = FakeAula([child(1, "Emma")], [post(i, f"Opslag {i}", 0.1 * i) for i in range(1, 91)], [], {})
    aula["client"] = client
    summary = run_fetch()
    assert len(summary["new"]) == 60 and len(client.post_requests) == 2
    assert any("flere end 60 opslag" in w for w in summary["warnings"])


def test_failed_download_in_a_message_is_retried(aula):
    thread = {"id": 8, "subject": "Indbydelse", "lastMessageDate": iso(1)}
    msg = message("a", attachments=[file_att(6, "invitation.pdf", url="https://files/broken")])
    client = FakeAula([child(1, "Emma")], [], [thread], {"8": [msg]})
    client.broken_urls = {"https://files/broken"}
    aula["client"] = client
    assert run_fetch()["new"] == ["message:8:a"]

    client.broken_urls = set()  # the thread itself has not changed
    assert run_fetch()["new"] == ["message:8:a"]
    assert Ledger.load().pending_item("message:8:a")["attachments"][0]["path"].endswith("invitation.pdf")
    assert run_fetch()["new"] == [] and Ledger.load().threads_to_retry() == {}


def test_thread_that_never_reads_completely_is_given_up(aula):
    thread = {"id": 9, "subject": "Lang", "lastMessageDate": iso(1)}
    client = FakeAula([child(1, "Emma")], [], [thread], {"9": [message("a")]})
    client.incomplete_threads = {"9"}
    aula["client"] = client
    warnings = [run_fetch()["warnings"] for _ in range(aula_source.MAX_THREAD_ATTEMPTS)]
    assert "prøver igen" in warnings[0][0] and "giver op" in warnings[-1][0]
    assert run_fetch()["warnings"] == []  # given up: no warning every night


def test_thread_whose_file_never_downloads_is_given_up_with_a_warning(aula):
    thread = {"id": 8, "subject": "Indbydelse", "lastMessageDate": iso(1)}
    msg = message("a", sender={"fullName": "Lærer Hansen"}, attachments=[file_att(6, "invitation.pdf", url="https://files/broken")])
    client = FakeAula([child(1, "Emma")], [], [thread], {"8": [msg]})
    client.broken_urls = {"https://files/broken"}
    aula["client"] = client

    warnings = [run_fetch()["warnings"] for _ in range(aula_source.MAX_THREAD_ATTEMPTS)]

    assert not any("giver op" in w for w in warnings[0])
    [given_up] = [w for w in warnings[-1] if "giver op" in w]
    assert "Tråd 8" in given_up and "Hansen" not in given_up
    assert run_fetch()["warnings"] == []  # given up: not read again, no warning every night


def test_forwarded_message_is_an_item_but_system_messages_are_not(aula):
    thread = {"id": 30, "subject": "Fwd: Skolefest", "lastMessageDate": iso(1)}
    system = [
        {"id": f"s{i}", "messageType": kind, "sendDateTime": iso(1)}
        for i, kind in enumerate(["RecipientsAdded", "RecipientsRemoved", "SystemReply", "AutoReply", "MessageDeleted"])
    ]
    forward = message("f1", messageType="Forward", attachments=None)
    aula["client"] = FakeAula([child(1, "Emma")], [], [thread], {"30": [*system, forward]})

    summary = run_fetch()

    assert summary["new"] == ["message:30:f1"] and summary["warnings"] == []
    inbox = json.loads(store.INBOX_FILE.read_text())
    item = next(i for i in inbox["items"] if i["key"] == "message:30:f1")
    assert item["type"] == "message" and item["body"] == "f1" and item["attachments"] == []


# ------------------------------------------------------------------ the archive


def archived(key):
    with archive.open() as a:
        return a.item(key)


def test_archive_gets_every_post_but_the_inbox_only_the_new_ones(aula):
    posts = [post(1, "Nyt", 1, html="<p>Juleklip <b>fredag</b></p>"), post(2, "Gammelt", 90)]
    aula["client"] = FakeAula([child(1, "Emma")], posts, [], {})

    assert run_fetch()["new"] == ["post:1"]

    with archive.open() as a:
        assert sorted(i.key for i in a.items()) == ["post:1", "post:2"]
    item = archived("post:1")
    assert item.kind == "post" and len(item.versions) == 1
    version = item.versions[0]
    assert version.title == "Nyt" and version.body == "Juleklip **fredag**" and version.author == "Lærer Hansen"
    assert version.groups == ["2.A"] and version.child_ids == [1]
    assert version.raw["content"]["html"] == "<p>Juleklip <b>fredag</b></p>"  # Aula's own JSON


def test_a_post_gets_a_new_version_only_when_its_content_changes(aula):
    def raw_post(html, url, comments=0):
        p = post(10, "Skolefest", 1, html=html, attachments=[file_att(5, "program.pdf", url=url)])
        p._raw["commentCount"] = comments
        return p

    client = FakeAula([child(1, "Emma")], [raw_post("<p>Fredag kl. 17</p>", "https://files/a?sig=1")], [], {})
    aula["client"] = client
    run_fetch()

    client.posts = [raw_post("<p>Fredag kl. 17</p>", "https://files/a?sig=2", comments=3)]  # new signed URL, a comment
    run_fetch()
    item = archived("post:10")
    assert len(item.versions) == 1
    assert item.versions[0].raw["attachments"][0]["file"]["url"] == "https://files/a?sig=1"  # raw JSON untouched

    client.posts = [raw_post("<p>Rettet: torsdag kl. 17</p>", "https://files/a?sig=3")]
    run_fetch()
    item = archived("post:10")
    assert [v.body for v in item.versions] == ["Fredag kl. 17", "Rettet: torsdag kl. 17"]

    client.posts = [raw_post("<p>Rettet: torsdag kl. 17</p>", "https://files/a?sig=3")]
    client.posts[0]._raw["attachments"].append(file_att(6, "kort.pdf"))
    client.posts = [Post.from_dict(client.posts[0]._raw)]  # a file was added
    run_fetch()
    assert len(archived("post:10").versions) == 3


def test_a_file_that_failed_is_downloaded_into_the_archive_on_the_next_fetch(aula):
    old = post(20, "Gammelt opslag", 90, attachments=[file_att(7, "plan.pdf", url="https://files/broken")])
    client = FakeAula([child(1, "Emma")], [old], [], {})
    client.broken_urls = {"https://files/broken"}
    aula["client"] = client

    assert any("plan.pdf" in w for w in run_fetch()["warnings"])
    [file] = archived("post:20").files
    assert (file.name, file.path) == ("plan.pdf", None)  # registered, but not stored

    client.broken_urls = set()
    assert run_fetch()["new"] == []  # still old news for the agent
    assert archived("post:20").files[0].path == "attachments/post_20/7-plan.pdf"
    run_fetch()
    assert client.downloads.count("https://files/broken") == 2  # not downloaded again once it is stored


def test_a_thread_is_archived_raw_with_its_messages_as_items(aula):
    thread = {"id": 40, "subject": "Skolefest", "lastMessageDate": iso(1), "recipients": [{"fullName": "Forælder"}]}
    msgs = [
        message("m1", sender={"fullName": "Lærer Hansen"}, attachments=[file_att(5, "program.pdf")]),
        {"id": "s1", "messageType": "RecipientsAdded", "sendDateTime": iso(1)},
        message("m2", messageType="MessageEdited"),
        message("f1", 100, messageType="Forward", attachments=None),  # older than the cutoff
        {"id": "s2", "messageType": "AutoReply", "sendDateTime": iso(1)},
    ]
    aula["client"] = FakeAula([child(1, "Emma")], [], [thread], {"40": msgs})

    run_fetch()

    with archive.open() as a:
        assert sorted(i.key for i in a.items()) == ["message:40:f1", "message:40:m1", "message:40:m2"]
        raw = a.thread("40")
    assert raw.raw["recipients"] == [{"fullName": "Forælder"}]  # the thread as Aula sent it
    assert [m["id"] for m in raw.messages] == ["m1", "s1", "m2", "f1", "s2"]  # system messages too

    item = archived("message:40:m1")
    assert (item.kind, item.thread_id, item.sensitive) == ("message", "40", False)
    [version] = item.versions
    assert (version.title, version.author, version.body, version.child_ids) == ("Skolefest", "Lærer Hansen", "m1", [1])
    assert version.raw["messageType"] == "Message"
    assert [(f.name, f.path) for f in item.files] == [("program.pdf", "attachments/message_40_m1/5-program.pdf")]
    assert archived("message:40:f1").files == []


def test_items_from_a_sensitive_thread_are_marked_sensitive(aula):
    threads = [
        {"id": 50, "subject": "Fortroligt", "lastMessageDate": iso(1), "sensitive": True},
        {"id": 51, "subject": "Tur", "lastMessageDate": iso(1), "sensitive": False},
    ]
    aula["client"] = FakeAula([child(1, "Emma")], [], threads, {"50": [message("a")], "51": [message("b")]})
    run_fetch()
    assert archived("message:50:a").sensitive is True
    assert archived("message:51:b").sensitive is False


def test_an_edited_message_gets_a_new_version_an_unchanged_one_does_not(aula):
    def thread(days_ago):
        return {"id": 60, "subject": "Juleklip", "lastMessageDate": iso(days_ago)}

    def msg(html, url):
        return message("a", text={"html": html}, attachments=[file_att(5, "seddel.pdf", url=url)])

    client = FakeAula([child(1, "Emma")], [], [thread(2)], {"60": [msg("<p>Fredag</p>", "https://files/s?sig=1")]})
    aula["client"] = client
    run_fetch()

    client.threads = [thread(1)]  # someone answered: the thread is read again
    client.messages["60"] = [msg("<p>Fredag</p>", "https://files/s?sig=2"), message("b")]
    run_fetch()
    item = archived("message:60:a")
    assert len(item.versions) == 1
    assert item.versions[0].raw["attachments"][0]["file"]["url"] == "https://files/s?sig=1"  # raw JSON untouched

    client.threads = [thread(0)]
    client.messages["60"] = [msg("<p>Rettet: torsdag</p>", "https://files/s?sig=3"), message("b")]
    client.messages["60"][0]["messageType"] = "MessageEdited"
    run_fetch()
    assert [v.body for v in archived("message:60:a").versions] == ["Fredag", "Rettet: torsdag"]
    assert len(archived("message:60:b").versions) == 1
    with archive.open() as a:
        assert [m["id"] for m in a.thread("60").messages] == ["a", "b"]


def test_a_file_that_failed_in_an_old_message_is_downloaded_into_the_archive_later(aula):
    thread = {"id": 70, "subject": "Plan", "lastMessageDate": iso(1)}
    old = message("a", 90, attachments=[file_att(8, "plan.pdf", url="https://files/broken")])  # older than the cutoff
    client = FakeAula([child(1, "Emma")], [], [thread], {"70": [old, message("b")]})
    client.broken_urls = {"https://files/broken"}
    aula["client"] = client

    assert any("plan.pdf" in w for w in run_fetch()["warnings"])
    assert archived("message:70:a").files[0].path is None

    client.broken_urls = set()  # the thread has not changed, but it is read again
    assert run_fetch()["new"] == []  # old news for the agent
    assert archived("message:70:a").files[0].path == "attachments/message_70_a/8-plan.pdf"
    run_fetch()
    assert client.downloads.count("https://files/broken") == 2 and Ledger.load().threads_to_retry() == {}


def test_a_partial_read_of_a_thread_loses_nothing_in_the_archive(aula):
    thread = {"id": 80, "subject": "Lejrskole", "lastMessageDate": iso(2), "sensitive": True}
    system = {"id": "s1", "messageType": "RecipientsAdded", "sendDateTime": iso(2)}
    client = FakeAula([child(1, "Emma")], [], [thread], {"80": [system, message("a", 2)]})
    aula["client"] = client
    run_fetch()

    client.threads = [{**thread, "lastMessageDate": iso(1)}]
    client.messages["80"] = [message("b")]  # only the newest page could be read
    client.incomplete_threads = {"80"}
    run_fetch()
    with archive.open() as a:
        assert [m["id"] for m in a.thread("80").messages] == ["s1", "a"]
    assert archived("message:80:b").sensitive is True

    client.threads = []  # read again only because the last read was incomplete
    client.incomplete_threads = set()
    client.messages["80"] = [system, message("a", 2), message("b")]
    run_fetch()
    with archive.open() as a:
        stored = a.thread("80")
    assert [m["id"] for m in stored.messages] == ["s1", "a", "b"]
    assert stored.raw["subject"] == "Lejrskole" and stored.raw["sensitive"] is True
    assert archived("message:80:b").sensitive is True


# ------------------------------------------------------------------ backfill


def run_backfill():
    return asyncio.run(aula_source.backfill())


def test_backfill_archives_every_post_and_thread_aula_still_has(aula):
    threads = [
        {"id": 90, "subject": "Skolefest", "lastMessageDate": iso(1)},
        {"id": 91, "subject": "Lejrskole i foråret", "lastMessageDate": iso(200), "sensitive": True},
    ]
    msgs = {
        "90": [message("a", attachments=[file_att(5, "program.pdf")]), {"id": "s1", "messageType": "RecipientsAdded"}],
        "91": [message("b", 200), message("c", 199, messageType="Forward", attachments=None)],
    }
    posts = [post(i, f"Opslag {i}", 3 * i, attachments=[file_att(100 + i, f"{i}.pdf")] if i == 40 else ()) for i in range(1, 41)]
    client = FakeAula([child(1, "Emma")], posts, threads, msgs)
    aula["client"] = client

    run_backfill()

    with archive.open() as a:
        keys = sorted(i.key for i in a.items())
        assert [m["id"] for m in a.thread("90").messages] == ["a", "s1"]
    assert keys == sorted([*(f"post:{i}" for i in range(1, 41)), "message:90:a", "message:91:b", "message:91:c"])
    assert archived("post:40").files[0].path == "attachments/post_40/140-40.pdf"  # 120 days old
    assert archived("message:90:a").files[0].path == "attachments/message_90_a/5-program.pdf"
    assert archived("message:91:b").sensitive is True
    assert archived("post:1").versions[0].child_ids == [1]
    assert client.thread_cutoffs[0] <= dt.date(2000, 1, 1)  # all threads, not just the latest month


def test_backfill_twice_gives_no_duplicates_and_leaves_inbox_and_intake_alone(aula):
    thread = {"id": 95, "subject": "Juleklip", "lastMessageDate": iso(1)}
    msgs = {"95": [message("a", attachments=[file_att(5, "seddel.pdf")])]}
    client = FakeAula([child(1, "Emma")], [post(10, "Nyt", 1, attachments=[file_att(6, "plan.pdf")])], [thread], msgs)
    aula["client"] = client
    run_fetch()
    ledger = Ledger.load()
    ledger.mark_processed(["post:10"])  # the agent handled one of them
    ledger.save()
    state = json.loads(store.STATE_FILE.read_text())
    inbox = store.INBOX_FILE.read_text()

    client.posts.append(post(11, "Nyt, men ikke hentet endnu", 0.5))
    client.downloads.clear()
    first, second = run_backfill(), run_backfill()

    assert (first.posts, first.threads, first.messages, first.new_versions) == (2, 1, 1, 1)  # only post:11 is new
    assert (second.posts, second.new_versions) == (2, 0)
    with archive.open() as a:
        items = a.items()
    assert sorted(i.key for i in items) == ["message:95:a", "post:10", "post:11"]
    assert all(len(i.versions) == 1 for i in items)
    assert [len(i.files) for i in items] == [1, 1, 0]
    assert client.downloads == []  # the files were already archived
    assert store.INBOX_FILE.read_text() == inbox
    assert json.loads(store.STATE_FILE.read_text()) == state  # seen, pending, last_fetch, ...


# ------------------------------------------------------------------ backup


def test_fetch_ends_with_a_backup_of_the_archive(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(10, "Indbydelse", 1, attachments=[file_att(5, "plan.pdf")])], [], {})
    summary = run_fetch()
    assert summary["warnings"] == []
    assert aula["google"].in_folder("Aula-arkiv") == ["archive.sqlite", "attachments/post_10/5-plan.pdf"]


class BrokenDrive(FakeGoogle):
    def create_folder(self, name):
        raise RuntimeError("Drive svarer 500")


@pytest.mark.parametrize("google", [BrokenDrive(), GoogleLoginRequired("Ingen Google-adgang endnu.")], ids=["error", "no-login"])
def test_a_failed_backup_is_a_warning_and_fetch_still_writes_the_inbox(aula, google):
    aula["client"] = FakeAula([child(1, "Emma")], [post(10, "Indbydelse", 1)], [], {})
    aula["google"] = google
    summary = run_fetch()
    assert summary["new"] == ["post:10"]
    [warning] = summary["warnings"]
    assert "Backup" in warning and ("500" in warning or "Google-adgang" in warning)
    inbox = json.loads(store.INBOX_FILE.read_text())
    assert [i["key"] for i in inbox["items"]] == ["post:10"]


def test_backfill_ends_with_a_backup_of_the_archive(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(10, "Indbydelse", 90, attachments=[file_att(5, "plan.pdf")])], [], {})
    assert run_backfill().warnings == []
    assert aula["google"].in_folder("Aula-arkiv") == ["archive.sqlite", "attachments/post_10/5-plan.pdf"]


def test_a_failed_backup_is_a_warning_after_backfill(aula):
    aula["client"] = FakeAula([child(1, "Emma")], [post(10, "Indbydelse", 90)], [], {})
    aula["google"] = GoogleLoginRequired("Ingen Google-adgang endnu.")
    result = run_backfill()
    [warning] = result.warnings
    assert "Backup" in warning and "Google-adgang" in warning
    assert archived("post:10") is not None


# ------------------------------------------------------------------ backfill from a rescue copy

RESCUE_DATE = "2024-11-05"  # not today, so "saved as of the copy" shows


def write_rescue(folder, posts, threads, messages, files):
    """A rescue copy in the format of the one taken 5/11 2026: Aula's raw JSON, and the files
    under ``files/<item folder>/<attachment id>-<name>``."""
    folder.mkdir(parents=True)
    (folder / "posts.json").write_text(json.dumps([p._raw for p in posts]))
    (folder / "threads.json").write_text(json.dumps([{"thread": t, "messages": messages[str(t["id"])]} for t in threads]))
    (folder / "child_albums.json").write_text(json.dumps({"1": [{"id": 900, "mediaType": "image", "tags": []}]}))
    for name, content in files.items():
        (folder / "files" / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / "files" / name).write_bytes(content)
    (folder / "files" / "photos_1").mkdir(parents=True)
    (folder / "files" / "photos_1" / "900-billede.jpeg").write_bytes(b"tagged photo")  # not until tagged photos are built
    return folder


def rescue_data():
    posts = [
        post(10, "Skolefest", 20, attachments=[file_att(5, "program.pdf"), image(7)]),
        post(11, "Samtykke", 200, attachments=[
            {"id": 90, "document": {"id": 9, "title": "Samtykke", "documentType": "Generic"}},
            {"id": 91, "link": {"service": "OneDrive", "name": "Mappe", "url": "https://onedrive/x"}},
        ]),
    ]
    threads = [
        {"id": 40, "subject": "Juleklip", "lastMessageDate": iso(3)},
        {"id": 41, "subject": "Fortroligt", "lastMessageDate": iso(100), "sensitive": True},
    ]
    messages = {
        "40": [
            message("m1.1", 3, sender={"fullName": "Lærer Hansen"}, attachments=[file_att(6, "seddel.docx")]),
            {"id": "s1", "messageType": "RecipientsRemoved", "sendDateTime": iso(2)},
            message("f1", 2, messageType="Forward", attachments=None),
        ],
        "41": [message("a", 100)],
    }
    files = {
        "post_10/5-program.pdf": b"%PDF-1.4",
        "post_10/7-7.jpg": b"%PDF-1.4",
        "message_40_m1.1/6-seddel.docx": b"%PDF-1.4",
    }
    return posts, threads, messages, files


def archive_contents():
    """Everything a reader of the archive sees, except when it was saved."""
    with archive.open() as a:
        items = a.items()
        threads = {i.thread_id: a.thread(i.thread_id) for i in items if i.thread_id}
    return (
        [
            (i.key, i.kind, i.thread_id, i.sensitive,
             [(v.title, v.author, v.date, v.groups, v.child_ids, v.body, v.raw) for v in i.versions],
             [(f.attachment_id, f.kind, f.name, f.detail, f.path, f.path and (store.DATA_DIR / f.path).read_bytes())
              for f in i.files])
            for i in items
        ],
        {tid: (t.raw, t.messages) for tid, t in threads.items()},
    )


def test_backfill_from_a_rescue_copy_archives_the_same_as_from_aula(aula, data_dir, monkeypatch):
    posts, threads, messages, files = rescue_data()
    rescue = write_rescue(data_dir / f"rescue-{RESCUE_DATE}", posts, threads, messages, files)
    store.save_config({"children": {"1": {"name": "Emma", "institution": "Skolen"}}})
    client = FakeAula([child(1, "Emma")], posts, threads, messages)
    aula["client"] = client

    from_rescue = asyncio.run(aula_source.backfill(rescue))

    assert (client.post_requests, client.thread_cutoffs, client.downloads) == ([], [], [])  # nothing from Aula
    assert (from_rescue.posts, from_rescue.threads, from_rescue.messages) == (2, 2, 3)
    rescued = archive_contents()

    monkeypatch.setattr(store, "DATA_DIR", data_dir / "from-aula")
    run_backfill()
    assert len(client.downloads) == 3
    assert archive_contents() == rescued
    assert len(rescued[0]) == 5 and not (data_dir / "attachments" / "photos_1").exists()


def test_a_rescue_copy_is_saved_as_of_its_date_and_aula_later_adds_no_duplicates(aula, data_dir):
    posts, threads, messages, files = rescue_data()
    rescue = write_rescue(data_dir / f"rescue-{RESCUE_DATE}", posts, threads, messages, files)
    store.save_config({"children": {"1": {"name": "Emma", "institution": "Skolen"}}})
    client = FakeAula([child(1, "Emma")], posts, threads, messages)
    aula["client"] = client

    first = asyncio.run(aula_source.backfill(rescue))
    again = asyncio.run(aula_source.backfill(rescue))
    run_backfill()  # the same items, now from Aula (new signed URLs would not matter either)
    run_fetch()

    assert (first.new_versions, again.new_versions) == (5, 0)
    with archive.open() as a:
        items = a.items()
    assert len(items) == 5 and all(len(i.versions) == 1 for i in items)
    assert {v.saved_at[:10] for i in items for v in i.versions} == {RESCUE_DATE}
    assert client.downloads == []  # the files were copied in from the rescue copy
    assert archive.status().last_saved.startswith(RESCUE_DATE)


def test_a_file_missing_from_the_rescue_copy_is_downloaded_from_aula_later(aula, data_dir):
    posts, threads, messages, files = rescue_data()
    del files["post_10/5-program.pdf"]
    rescue = write_rescue(data_dir / f"rescue-{RESCUE_DATE}", posts, threads, messages, files)
    client = FakeAula([child(1, "Emma")], posts, threads, messages)
    aula["client"] = client

    result = asyncio.run(aula_source.backfill(rescue))
    assert [w for w in result.warnings if "program.pdf" in w] and client.downloads == []
    assert [f.path for f in archived("post:10").files] == [None, "attachments/post_10/7-7.jpg"]

    run_backfill()
    assert client.downloads == ["https://files/x"]
    assert archived("post:10").files[0].path == "attachments/post_10/5-program.pdf"


def test_an_older_rescue_copy_after_fetch_neither_replaces_the_latest_version_nor_the_thread(aula, data_dir):
    thread = {"id": 40, "subject": "Juleklip", "lastMessageDate": iso(1)}
    old_thread = {**thread, "lastMessageDate": iso(3)}
    rescued_posts = [post(10, "Skolefest", 20, html="<p>Fredag kl. 17</p>")]
    rescued_messages = {"40": [message("a", 3, text={"html": "<p>Husk saks</p>"})]}
    rescue = write_rescue(data_dir / f"rescue-{RESCUE_DATE}", rescued_posts, [old_thread], rescued_messages, {})
    store.save_config({"children": {"1": {"name": "Emma", "institution": "Skolen"}}})
    newer_messages = {"40": [message("a", 3, text={"html": "<p>Husk saks og lim</p>"}), message("b")]}
    client = FakeAula([child(1, "Emma")], [post(10, "Skolefest", 20, html="<p>Rettet: torsdag kl. 17</p>")],
                      [thread], newer_messages)
    aula["client"] = client
    run_fetch()

    asyncio.run(aula_source.backfill(rescue))  # taken before what fetch already saved
    run_fetch()
    run_backfill()

    assert [v.body for v in archived("post:10").versions] == ["Fredag kl. 17", "Rettet: torsdag kl. 17"]
    assert [v.body for v in archived("message:40:a").versions] == ["Husk saks", "Husk saks og lim"]
    assert len(archived("message:40:b").versions) == 1
    with archive.open() as a:
        stored = a.thread("40")
    assert [m["id"] for m in stored.messages] == ["a", "b"] and stored.raw["lastMessageDate"] == thread["lastMessageDate"]

    asyncio.run(aula_source.backfill(rescue))  # again: the older versions are already there
    assert [len(archived(k).versions) for k in ("post:10", "message:40:a")] == [2, 2]
