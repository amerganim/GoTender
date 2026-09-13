"""Append-only raw document archive (§6, §8.4).

Every byte a source returns is written here before anything parses it. This is
what makes a parser fix replayable without re-crawling, and it is the reason
raw_documents is never deleted.

Layout: <storage>/<source_key>/<YYYY>/<MM>/<DD>/<sha256[:16]>.gz
Content-addressed, so an unchanged page re-fetched tomorrow costs one stat call
and no new bytes.
"""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path

from tenderradar.adapters.base import FetchResult
from tenderradar.config import settings


@dataclass(slots=True)
class ArchivedPayload:
    content_hash: str
    storage_path: str
    bytes_written: int
    deduplicated: bool  # True when this exact content was already on disk


class RawArchive:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or settings.storage_dir)

    @staticmethod
    def content_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def path_for(self, source_key: str, result: FetchResult, digest: str) -> Path:
        stamp = result.fetched_at
        return (
            self.root
            / source_key
            / f"{stamp:%Y}"
            / f"{stamp:%m}"
            / f"{stamp:%d}"
            / f"{digest[:16]}.gz"
        )

    def store(self, source_key: str, result: FetchResult) -> ArchivedPayload:
        """Write raw bytes to the archive. Never overwrites an existing blob."""
        digest = self.content_hash(result.content)
        path = self.path_for(source_key, result, digest)

        if path.exists():
            return ArchivedPayload(
                content_hash=digest,
                storage_path=str(path),
                bytes_written=0,
                deduplicated=True,
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp name then rename, so a crash mid-write cannot leave a
        # truncated blob that later looks like a valid archived document.
        tmp = path.with_suffix(".gz.part")
        with gzip.open(tmp, "wb", compresslevel=6) as handle:
            handle.write(result.content)
        tmp.replace(path)

        return ArchivedPayload(
            content_hash=digest,
            storage_path=str(path),
            bytes_written=path.stat().st_size,
            deduplicated=False,
        )

    def load(self, storage_path: str | Path) -> bytes:
        """Read a blob back, for replaying a parser against the archive."""
        with gzip.open(storage_path, "rb") as handle:
            return handle.read()
