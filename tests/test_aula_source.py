import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest
from aula.models.calendar_event import CalendarEvent
from aula.models.pagination import PaginationResult
from aula.models.post import Post

from aula_agent import aula_source, store

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

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_profile(self):
        return SimpleNamespace(children=self.children, institution_profile_ids=[GUARDIAN_PROFILE, *(c.id for c in self.children)])

    async def get_posts(self, ids, page=1, limit=10):
        self.post_requests.append(list(ids))
        return self.posts if GUARDIAN_PROFILE in ids else []

    async def get_all_message_threads_result(self, cutoff):
        return PaginationResult(items=self.threads, complete=True, termination_reason="cutoff")

    async def get_all_messages_for_thread_result(self, thread_id):
        return PaginationResult(items=self.messages[thread_id], complete=True, termination_reason="exhausted")

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
    assert store.load_state()["pending"]["post:10"]["child_ids"] == [1]
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
    assert store.load_state()["pending"]["post:10"]["body"].startswith("Rettet")


def test_posts_with_two_children_are_left_for_claude_with_groups(aula):
    aula["client"] = FakeAula([child(1, "Emma"), child(2, "Oscar")], [post(10, "Motionsdag", 1)], [], {})
    run_fetch()
    item = store.load_state()["pending"]["post:10"]
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
    pending = store.load_state()["pending"]
    assert pending["message:1:a"]["child_ids"] == []  # Claude decides from the content
    assert pending["message:2:b"]["child_ids"] == [2]


def test_photo_gallery_not_downloaded(aula):
    images = [{"id": i, "media": {"mediaType": "image", "file": {"id": i, "name": f"{i}.jpg", "url": f"u{i}"}}} for i in range(5)]
    client = FakeAula([child(1, "Emma")], [post(10, "Billeder fra turen", 1, attachments=images)], [], {})
    aula["client"] = client
    run_fetch()
    assert client.downloads == []
    atts = store.load_state()["pending"]["post:10"]["attachments"]
    assert len(atts) == 5 and all("path" not in a for a in atts)


def test_expired_session_never_starts_mitid(monkeypatch, tmp_path):
    token_file = tmp_path / "tokens.json"
    token_file.write_text("{}")
    monkeypatch.setattr(aula_source, "DEFAULT_TOKEN_FILE", token_file)
    monkeypatch.setattr(aula_source, "load_aula_config", lambda: {"mitid_username": "x"})

    async def fake_auth(username, storage, on_login_required):
        on_login_required()  # what the library does right before MitID
        raise AssertionError("MitID login must not start")

    monkeypatch.setattr(aula_source, "authenticate_and_create_client", fake_auth)
    with pytest.raises(aula_source.LoginRequired):
        asyncio.run(aula_source.open_client())
