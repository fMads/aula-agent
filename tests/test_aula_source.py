import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest
from aula.models.calendar_event import CalendarEvent
from aula.models.pagination import PaginationResult
from aula.models.post import Post

from aula_agent import archive, aula_source, store
from aula_agent.ledger import Ledger

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
        return PaginationResult(items=self.threads, complete=True, termination_reason="cutoff")

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
def aula(monkeypatch):
    holder = {}

    async def open_client():
        return holder["client"]

    monkeypatch.setattr(aula_source, "open_client", open_client)
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
