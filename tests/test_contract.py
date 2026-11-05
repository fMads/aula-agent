import datetime as dt
import json

import pytest

from aula_agent import contract, store
from aula_agent.contract import EventError
from aula_agent.ledger import Ledger, SyncedEntry

CHILDREN = ["1"]


def parse(events, processed=(), pending=(), children=CHILDREN):
    return contract.parse({"processed": list(processed), "events": events}, children=children, pending_keys=pending)


def one(**ev):
    return parse([{"key": "k", "child_ids": [1], "title": "x", **ev}]).events[0]


# ----------------------------------------------------------------------------- events in


def test_all_day_end_is_the_last_day():
    ev = one(start="2026-11-10", end="2026-11-12")
    assert ev.all_day and ev.start == dt.date(2026, 11, 10) and ev.end == dt.date(2026, 11, 12)


def test_all_day_defaults_to_one_day():
    assert one(start="2026-12-04").end == dt.date(2026, 12, 4)


def test_timed_defaults_to_one_hour_and_drops_offset():
    ev = one(start="2026-11-20T17:00+01:00")
    assert not ev.all_day
    assert (ev.start, ev.end) == (dt.datetime(2026, 11, 20, 17), dt.datetime(2026, 11, 20, 18))
    assert (ev.start_text, ev.end_text) == ("2026-11-20T17:00", "2026-11-20T18:00")


def test_cancellation_needs_only_key_and_children():
    (ev,) = parse([{"key": "k", "child_ids": [1], "cancelled": True}]).events
    assert ev.cancelled and ev.start is None


@pytest.mark.parametrize(
    "ev, message",
    [
        ({"key": "k", "child_ids": [1], "start": "2026-11-10"}, "title mangler"),
        ({"key": "k", "child_ids": [1], "title": "x"}, "start mangler"),
        ({"key": "k", "child_ids": [1], "title": "x", "start": "10/11"}, "ugyldig"),
        ({"key": "k", "child_ids": [1], "title": "x", "start": 20261110}, "ugyldig"),
        ({"key": "k", "child_ids": [1], "title": "x", "start": "2026-11-10T10:00", "end": "2026-11-10T09:00"}, "før start"),
        ({"key": "k", "child_ids": [1], "title": "x", "start": "2026-11-10", "end": "2026-11-10T09:00"}, "hel dag"),
        ({"key": "k", "child_ids": [1], "title": "x", "start": "2026-11-10T09:00", "end": "2026-11-10"}, "klokkeslæt"),
        ({"child_ids": [1], "title": "x", "start": "2026-11-10"}, "key mangler"),
        ({"key": "k", "title": "x", "start": "2026-11-10"}, "child_ids mangler"),
        ({"key": "k", "child_ids": [2], "title": "x", "start": "2026-11-10"}, "ukendt barn 2"),
        ({"key": "k", "child_ids": [1], "title": "x", "start": "2026-11-10", "attachments": ["nope.pdf"]}, "findes ikke"),
        ({"key": "k", "child_ids": [1], "title": "x", "start": "2026-11-10", "attachments": ["../../etc/passwd"]}, "data/"),
        ("not an object", "objekt"),
        ({"key": "k", "child_ids": [1], "title": "x", "start": "2026-11-10", "location": {"a": 1}}, "location skal være tekst"),
        ({"key": "k", "child_ids": [1], "cancelled": True, "source": 5}, "source skal være tekst"),
    ],
)
def test_bad_events_rejected(ev, message):
    with pytest.raises(EventError, match=message):
        parse([ev])


def test_every_problem_is_reported_at_once():
    with pytest.raises(EventError) as err:
        parse(
            [
                {"key": "a", "child_ids": [1], "title": "x", "start": "2026-11-10"},
                {"key": "a", "child_ids": [9], "title": "", "start": "i morgen"},
            ],
            pending=["post:1"],
        )
    text = str(err.value)
    for part in ("to gange", "ukendt barn 9", "title mangler", "ugyldig", "processed mangler disse items fra indbakken: post:1"):
        assert part in text


def test_invalid_json_is_an_event_error():
    with pytest.raises(EventError, match="ikke gyldig JSON"):
        contract.parse('{"events": [', children=CHILDREN, pending_keys=[])


def test_processed_must_cover_the_inbox():
    both = [classify("post:1"), classify("post:2")]
    with pytest.raises(EventError, match="post:2"):
        parse_doc(both, processed=["post:1"], pending=["post:1", "post:2"])
    doc = parse_doc(both, processed=["post:1", "post:2", "old:3"], pending=["post:1", "post:2"])
    assert doc.processed == ("post:1", "post:2", "old:3")


def test_attachment_inside_data_is_accepted(data_dir):
    (data_dir / "attachments").mkdir()
    (data_dir / "attachments" / "a.pdf").write_bytes(b"%PDF")
    assert one(start="2026-11-10", attachments=["attachments/a.pdf"]).attachments == ("attachments/a.pdf",)


# ----------------------------------------------------------------------------- inbox out


def test_inbox_groups_existing_events_and_lists_removed_multi_day_events():
    cfg = store.load_config()
    cfg["children"] = {"1": {"name": "Emma", "institution": "Skolen"}, "2": {"name": "Oscar", "institution": "Skolen"}}
    ledger = Ledger()
    for cid, cal in ((1, "c1"), (2, "c2")):
        ledger.record(SyncedEntry("k1", cid, cal, f"e{cid}", "Juleklip", "2099-12-04", "2099-12-04", "post:1"))
    ledger.record(SyncedEntry("old", 1, "c1", "e0", "Gammel", "2001-01-01", "2001-01-01"))
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    ledger.mark_removed("trip", 2, title="Lejrskole", start=yesterday, end="2099-01-01")  # started, not over
    ledger._st["ignored"]["legacy"] = {"title": "Gammel fjernet", "start": "2099-02-01"}  # before removal was per child
    ledger.add_pending({"key": "post:9", "type": "post", "child_ids": [1], "published": "2026-01-01"})

    contract.write_inbox(ledger, cfg, [{"title": "Motionsdag"}], ["advarsel"])
    inbox = json.loads(store.INBOX_FILE.read_text())

    assert inbox["existing_events"] == [
        {"key": "k1", "child_ids": [1, 2], "title": "Juleklip", "start": "2099-12-04", "end": "2099-12-04", "source": "post:1"}
    ]
    assert inbox["removed_by_user"] == [
        {"key": "trip", "child_ids": [2], "title": "Lejrskole", "start": yesterday, "end": "2099-01-01"},
        {"key": "legacy", "child_ids": [1, 2], "title": "Gammel fjernet", "start": "2099-02-01", "end": "2099-02-01"},
    ]
    assert [i["key"] for i in inbox["items"]] == ["post:9"]
    assert inbox["children"] == [{"id": 1, "name": "Emma", "institution": "Skolen"}, {"id": 2, "name": "Oscar", "institution": "Skolen"}]
    assert inbox["aula_calendar"] == [{"title": "Motionsdag"}] and inbox["warnings"] == ["advarsel"]


# ----------------------------------------------------------------------------- classifications in


def classify(item="post:1", **c):
    return {"item": item, "category": "orientering", "topics": ["svømning"], "summary": "Svømning starter mandag.", **c}


def parse_doc(classifications=(), processed=("post:1",), pending=("post:1",), events=(), new_topics=None, **kw):
    doc = {"processed": list(processed), "events": list(events), "classifications": list(classifications)}
    if new_topics is not None:
        doc["new_topics"] = new_topics
    kw.setdefault("topics", ["svømning"])
    return contract.parse(doc, children=CHILDREN, pending_keys=pending, **kw)


def test_processed_item_without_classification_is_rejected():
    with pytest.raises(EventError, match="post:1: classification mangler"):
        parse_doc([])


def test_a_valid_classification_goes_through():
    doc = parse_doc([classify(span={"start": "2026-11-10", "end": "2026-11-12"}, child_ids=[1])])
    c = doc.classifications["post:1"]
    assert c.category == "orientering" and c.topics == ("svømning",) and c.summary == "Svømning starter mandag."
    assert (c.span_start, c.span_end) == (dt.date(2026, 11, 10), dt.date(2026, 11, 12))
    assert c.child_ids == (1,)


@pytest.mark.parametrize(
    "c, message",
    [
        (classify(category="nyhed"), "category skal være en af arrangement, frist, periode, info, orientering, svar"),
        (classify(span={"start": "10/11"}), "span skal være datoer"),
        (classify(span={"start": "2026-11-12", "end": "2026-11-10"}), "span slutter før"),
        (classify(span={"end": "2026-11-10"}), "span skal have start"),
        (classify(topics=[]), "1–3 topics"),
        (classify(topics=["a", "b", "c", "d"]), "1–3 topics"),
        (classify(topics=["lejrskole"]), "ukendt topic 'lejrskole'"),
        (classify(summary="x" * 121), "121 tegn"),
        (classify(summary=""), "summary mangler"),
        (classify(child_ids=[9]), "ukendt barn 9"),
        (classify(item="post:7"), "post:7 er hverken i items eller to_classify"),
        ("ikke et objekt", "objekt"),
    ],
)
def test_bad_classifications_rejected(c, message):
    with pytest.raises(EventError, match=message):
        parse_doc([classify(), c] if isinstance(c, dict) and c.get("item") != "post:1" else [c])


def test_every_classification_problem_is_reported_at_once():
    with pytest.raises(EventError) as err:
        parse_doc(
            [classify(category="nyhed", topics=["lejrskole"], summary="x" * 200, child_ids=[9])],
            processed=["post:1", "post:2"], pending=["post:1", "post:2"],
        )
    text = str(err.value)
    for part in ("category", "ukendt topic", "200 tegn", "ukendt barn 9", "post:2: classification mangler"):
        assert part in text
    assert "post:1: classification mangler" not in text  # it has one, just with errors


def test_a_reply_needs_no_topics_or_summary():
    c = parse_doc([{"item": "post:1", "category": "svar"}]).classifications["post:1"]
    assert c.category == "svar" and c.topics == () and c.summary is None and c.span_start is None


def test_a_sensitive_thread_gets_no_summary():
    key = "message:5:6"
    doc = parse_doc([classify(key, summary=None)], processed=[key], pending=[key], sensitive_keys=[key])
    assert doc.classifications[key].summary is None
    with pytest.raises(EventError, match="følsom tråd – intet resumé"):
        parse_doc([classify(key)], processed=[key], pending=[key], sensitive_keys=[key])


def test_a_new_topic_is_known_when_it_is_in_new_topics():
    doc = parse_doc([classify(topics=["lejrskole", "svømning"])], new_topics={"lejrskole": "Lejrskolen i 5. klasse"})
    assert doc.new_topics == {"lejrskole": "Lejrskolen i 5. klasse"}
    assert doc.classifications["post:1"].topics == ("lejrskole", "svømning")
    with pytest.raises(EventError, match="'lejrskole' mangler en beskrivelse"):
        parse_doc([classify(topics=["lejrskole"])], new_topics={"lejrskole": ""})


def test_an_item_to_classify_may_be_left_out_but_gives_no_events():
    doc = parse_doc([], processed=[], pending=[], to_classify=["post:3"])
    assert doc.classifications == {}
    doc = parse_doc([classify("post:3")], processed=[], pending=[], to_classify=["post:3"])
    assert doc.classifications["post:3"].summary == "Svømning starter mandag."
    event = {"key": "post:3#1", "source": "post:3", "child_ids": [1], "title": "Svømning", "start": "2099-11-10"}
    with pytest.raises(EventError, match="post:3 er fra to_classify og må ikke give events"):
        parse_doc([classify("post:3")], processed=[], pending=[], to_classify=["post:3"], events=[event])
