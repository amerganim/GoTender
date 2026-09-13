"""Raw archive behaviour (§6, §8.4): append-only, content-addressed, replayable."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tenderradar.adapters.base import FetchResult, PayloadKind
from tenderradar.crawl.archive import RawArchive


@pytest.fixture
def archive(tmp_path: Path) -> RawArchive:
    return RawArchive(root=tmp_path)


def payload(content: bytes = b"<tr><td>hello</td></tr>") -> FetchResult:
    return FetchResult(
        url="https://www.eprocure.gov.bd/TenderDetailsServlet",
        content=content,
        kind=PayloadKind.LIST,
        fetched_at=datetime(2026, 9, 13, 7, 0, tzinfo=UTC),
        meta={"page_no": 1},
    )


def test_store_writes_and_reads_back(archive: RawArchive):
    result = payload()
    stored = archive.store("egp_tender", result)

    assert Path(stored.storage_path).exists()
    assert not stored.deduplicated
    assert archive.load(stored.storage_path) == result.content


def test_storage_is_gzipped(archive: RawArchive):
    # A page of tender rows compresses hard; that is the point of gzipping.
    content = b"<tr><td>repeated row</td></tr>" * 500
    stored = archive.store("egp_tender", payload(content))

    assert stored.bytes_written < len(content) / 5
    assert archive.load(stored.storage_path) == content


def test_path_is_partitioned_by_source_and_date(archive: RawArchive):
    stored = archive.store("egp_tender", payload())
    parts = Path(stored.storage_path).parts

    assert "egp_tender" in parts
    assert "2026" in parts and "09" in parts and "13" in parts


def test_identical_content_is_not_rewritten(archive: RawArchive):
    """§8.2: never re-store a page that has not changed."""
    first = archive.store("egp_tender", payload())
    second = archive.store("egp_tender", payload())

    assert second.deduplicated
    assert second.bytes_written == 0
    assert second.storage_path == first.storage_path
    assert second.content_hash == first.content_hash


def test_different_content_gets_a_different_path(archive: RawArchive):
    first = archive.store("egp_tender", payload(b"one"))
    second = archive.store("egp_tender", payload(b"two"))

    assert first.storage_path != second.storage_path
    assert first.content_hash != second.content_hash


def test_no_partial_files_are_left_behind(archive: RawArchive):
    """Blobs are renamed into place, so a crash cannot leave a truncated file."""
    archive.store("egp_tender", payload())
    assert list(archive.root.rglob("*.part")) == []
    assert len(list(archive.root.rglob("*.gz"))) == 1


def test_bangla_content_survives_the_round_trip(archive: RawArchive):
    content = "বৈদ্যুতিক কাজ".encode("utf-8")
    stored = archive.store("egp_tender", payload(content))

    assert archive.load(stored.storage_path).decode("utf-8") == "বৈদ্যুতিক কাজ"
