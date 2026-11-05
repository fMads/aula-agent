import asyncio
import json

import pytest

from aula.models.attachment import Attachment

from aula_agent import archive, backup, gcal, store
from aula_agent.ledger import Ledger


def archive_post(pid, *file_names):
    """A post with files in the archive, as fetch would have left it."""
    atts = [{"id": 50 + i, "name": n, "file": {"id": 50 + i, "name": n, "url": f"https://files/{n}"}} for i, n in enumerate(file_names)]

    async def download(att):
        return b"%PDF " + att.name.encode()

    with archive.open() as a:
        a.save_post({"id": pid, "title": f"Opslag {pid}", "content": {"html": "<p>x</p>"},
                     "ownerProfile": {"id": 3, "profileId": 4}, "attachments": atts})
        asyncio.run(a.save_files(f"post:{pid}", [Attachment.from_dict(x) for x in atts], download))


def test_backup_puts_the_archive_and_its_files_in_a_private_folder_and_replaces_the_archive(google):
    archive_post(1, "plan.pdf")
    backup.take(google)

    assert google.folders == ["Aula-arkiv"]
    assert google.in_folder("Aula-arkiv") == ["archive.sqlite", "attachments/post_1/50-plan.pdf"]

    archive_post(2, "program.pdf")
    backup.take(google)

    assert google.folders == ["Aula-arkiv"]  # created once
    assert google.in_folder("Aula-arkiv") == [
        "archive.sqlite", "attachments/post_1/50-plan.pdf", "attachments/post_2/50-program.pdf",
    ]  # the archive was replaced, each file uploaded once
    assert len(google.replaced) == 1


@pytest.mark.parametrize("new_file", [False, True], ids=["nothing-new", "a-new-file"])
@pytest.mark.parametrize("remove", ["trash", "delete"])
def test_a_backup_folder_deleted_in_drive_is_made_again_with_everything(google, remove, new_file):
    archive_post(1, "plan.pdf")
    backup.take(google)
    getattr(google, remove)("Aula-arkiv")
    if new_file:
        archive_post(2, "program.pdf")

    backup.take(google)

    assert google.folders == ["Aula-arkiv", "Aula-arkiv"]
    assert google.in_folder("Aula-arkiv") == [
        "archive.sqlite", "attachments/post_1/50-plan.pdf", *(["attachments/post_2/50-program.pdf"] if new_file else []),
    ]
    backup.take(google)
    assert len(google.folders) == 2  # and from then on it is used again


def test_backup_folder_is_not_the_shared_folder_for_calendar_attachments(google, data_dir):
    cfg = store.load_config()
    cfg["children"]["1"] = {"name": "Emma", "institution": "Skolen"}
    store.save_config(cfg)
    ledger = Ledger()
    ledger.add_pending({"key": "post:9", "child_ids": [1], "type": "post"})
    ledger.save()
    (data_dir / "attachments").mkdir()
    (data_dir / "attachments" / "55-indbydelse.pdf").write_bytes(b"%PDF")
    gcal.sync({"processed": ["post:9"], "classifications": [{"item": "post:9", "category": "svar"}], "events": [{"key": "post:9#1", "source": "post:9", "child_ids": [1], "title": "Juleklip",
               "start": "2099-12-04", "attachments": ["attachments/55-indbydelse.pdf"]}]}, google=google)
    archive_post(1, "plan.pdf")

    backup.take(google)
    backup.take(google)

    assert google.folders == ["Aula", "Aula-arkiv"]
    assert google.in_folder("Aula") == ["indbydelse.pdf"]
    assert google.in_folder("Aula-arkiv") == ["archive.sqlite", "attachments/post_1/50-plan.pdf"]


def test_the_archive_in_drive_can_be_restored(google, tmp_path, monkeypatch):
    archive_post(1, "plan.pdf")
    backup.take(google)

    restored = tmp_path / "restored"
    restored.mkdir()
    (restored / "archive.sqlite").write_bytes(next(f["content"] for f in google.drive.values() if f["name"] == "archive.sqlite"))
    monkeypatch.setattr(store, "DATA_DIR", restored)
    with archive.open() as a:
        [item] = a.items()
    assert item.key == "post:1" and item.versions[0].title == "Opslag 1"
    assert [f.path for f in item.files] == ["attachments/post_1/50-plan.pdf"]
