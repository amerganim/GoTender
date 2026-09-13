"""The source adapter contract.

An adapter does exactly two things:

    fetch()  -> raw bytes, yielded as FetchResult
    parse()  -> normalized TenderRecords

The two are deliberately separate so the runner can archive raw bytes before
any parsing happens (§8.4), and so a parser fix can be replayed against the
archive without re-crawling the source (§6).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from tenderradar.crawl.http import PoliteClient
from tenderradar.models import TenderRecord


class PayloadKind(StrEnum):
    """What a fetched payload contains, so parse() can dispatch."""

    LIST = "list"      # a page of search results
    DETAIL = "detail"  # one tender's detail page
    DOCUMENT = "document"


@dataclass(slots=True)
class FetchResult:
    """Raw bytes exactly as the source returned them. Never parsed in place."""

    url: str
    content: bytes
    kind: PayloadKind
    content_type: str = "text/html"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Free-form adapter breadcrumbs (page number, tender id) carried through
    # to the archive so a replay knows what this payload was.
    meta: dict[str, object] = field(default_factory=dict)

    # Set by the runner after the payload is archived; copied onto every
    # record parsed from it so a parser failure can be replayed (§8.6).
    raw_document_id: int | None = None


class SourceAdapter(ABC):
    """Base class for every source. Subclasses declare their own key."""

    key: str
    name: str
    base_url: str
    # Used by yield monitoring (§8.5) as the initial expectation before a
    # trailing average exists.
    expected_yield_per_run: int = 0

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> PoliteClient:
        if self._client is None:
            self._client = PoliteClient(base_url=self.base_url)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> SourceAdapter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    @abstractmethod
    def fetch(self, **kwargs: object) -> AsyncIterator[FetchResult]:
        """Yield raw payloads. Must not parse, and must not touch the database."""
        raise NotImplementedError

    @abstractmethod
    def parse(self, result: FetchResult) -> list[TenderRecord]:
        """Turn one raw payload into normalized records. Must be pure.

        Pure means: same bytes in, same records out, no I/O. That is what makes
        fixture tests and archive replays possible.
        """
        raise NotImplementedError


registry: dict[str, type[SourceAdapter]] = {}


def register_adapter(cls: type[SourceAdapter]) -> type[SourceAdapter]:
    """Class decorator that adds an adapter to the registry."""
    if not getattr(cls, "key", None):
        raise ValueError(f"{cls.__name__} must define a non-empty `key`")
    if cls.key in registry:
        raise ValueError(f"adapter key {cls.key!r} is already registered")
    registry[cls.key] = cls
    return cls


def get_adapter(key: str) -> type[SourceAdapter]:
    try:
        return registry[key]
    except KeyError:
        raise KeyError(
            f"no adapter registered for {key!r}; known: {sorted(registry)}"
        ) from None
