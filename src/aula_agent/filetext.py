"""The text in a stored file, for the archive's free-text index: PDF (with ``pypdf``) and docx (zip + XML).

Other kinds of files have no text here (they are found by their name). A file that cannot be read
(broken, encrypted, not what its name says) has none either; it never stops the save.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree

MAX_CHARS = 200_000  # more than any letter from school; a huge file is cut off here
_MAX_XML_BYTES = 20_000_000  # word/document.xml read at most this far (a zip can unpack to far more)
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

logging.getLogger("pypdf").setLevel(logging.ERROR)  # a sloppy PDF gives warnings, not errors


def extract(path: Path) -> str | None:
    """The text in the file at ``path``, or None when it is not a PDF or docx or cannot be read."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            text = _pdf(path)
        elif suffix == ".docx":
            text = _docx(path)
        else:
            return None
    except Exception:  # noqa: BLE001 - whatever a broken file raises, it just has no text
        return None
    return text[:MAX_CHARS]


def _pdf(path: Path) -> str:
    """The text page by page, stopping once there is :data:`MAX_CHARS` of it."""
    from pypdf import PdfReader

    pages: list[str] = []
    size = 0
    for page in PdfReader(path).pages:
        pages.append(page.extract_text() or "")
        size += len(pages[-1]) + 1
        if size >= MAX_CHARS:
            break
    return "\n".join(pages)


def _docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z, z.open("word/document.xml") as f:
        xml = f.read(_MAX_XML_BYTES)
    root = ElementTree.fromstring(xml)
    return "\n".join("".join(t.text or "" for t in p.iter(f"{_W}t")) for p in root.iter(f"{_W}p"))
