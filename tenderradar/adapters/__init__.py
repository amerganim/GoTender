"""Source adapters. All source-specific logic lives in here and nowhere else (§9)."""

from tenderradar.adapters.base import (
    FetchResult,
    PayloadKind,
    SourceAdapter,
    get_adapter,
    register_adapter,
    registry,
)

__all__ = [
    "FetchResult",
    "PayloadKind",
    "SourceAdapter",
    "get_adapter",
    "register_adapter",
    "registry",
]
