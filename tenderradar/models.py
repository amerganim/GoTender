"""Normalized records produced by adapters.

These are the contract between an adapter and everything downstream. Source
specific shapes never escape an adapter (§9: "Never write source-specific logic
outside an adapter").
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator


class ProcurementNature(StrEnum):
    GOODS = "goods"
    WORKS = "works"
    SERVICES = "services"
    OTHER = "other"


class TenderStatus(StrEnum):
    LIVE = "live"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    AWARDED = "awarded"


class ChangeType(StrEnum):
    NEW = "new"
    CORRIGENDUM = "corrigendum"
    CANCELLATION = "cancellation"
    EXTENSION = "extension"


def normalize_text(value: str | None) -> str | None:
    """NFC-normalize and collapse whitespace.

    Bangla arrives in several Unicode encodings of the same grapheme; without
    this, the same tender hashes differently on different crawls (§9).
    """
    if value is None:
        return None
    text = unicodedata.normalize("NFC", value)
    text = " ".join(text.split())
    return text or None


class OrganizationRef(BaseModel):
    """The procuring hierarchy as the source states it.

    e-GP nests Ministry > Division > Organization (PA) > Procuring Entity (PE).
    Resolution to organization ids happens on ingest, not in the adapter.
    """

    ministry: str | None = None
    division: str | None = None
    agency: str | None = None
    procuring_entity: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _normalize(cls, v: object) -> object:
        return normalize_text(v) if isinstance(v, str) else v

    @property
    def deepest(self) -> str | None:
        return self.procuring_entity or self.agency or self.division or self.ministry


class TenderLot(BaseModel):
    lot_no: str | None = None
    description: str | None = None
    location: str | None = None
    security_amount: Decimal | None = None
    start_date: datetime | None = None
    completion_date: datetime | None = None


class TenderRecord(BaseModel):
    """One tender, normalized. Adapters return these from parse()."""

    source_key: str
    external_ref: str  # the source's own id; unique within a source

    title: str | None = None
    title_bn: str | None = None
    package_no: str | None = None
    # The procuring entity's own invitation reference, distinct from external_ref.
    reference_no: str | None = None
    description: str | None = None

    organization: OrganizationRef = Field(default_factory=OrganizationRef)
    district: str | None = None
    upazila: str | None = None

    procurement_nature: ProcurementNature = ProcurementNature.OTHER
    procurement_type: str | None = None   # NCT / ICT
    procurement_method: str | None = None  # OTM / LTM / EOI / RFP / ...

    # e-GP does not publish official estimated cost. tender_security is the
    # usable proxy for value-range filtering (it runs ~2-2.5% of the estimate).
    estimated_value: Decimal | None = None
    tender_security: Decimal | None = None
    document_price: Decimal | None = None

    published_at: datetime | None = None
    closing_at: datetime | None = None
    opening_at: datetime | None = None
    document_last_selling_at: datetime | None = None

    status: TenderStatus = TenderStatus.LIVE
    categories: list[str] = Field(default_factory=list)
    lots: list[TenderLot] = Field(default_factory=list)

    # Raw eligibility prose. On e-GP this is on the free detail page, so
    # Phase 5 Layer 3 can start here without touching a PDF (cost trap #2).
    eligibility_text: str | None = None

    project_name: str | None = None
    app_id: str | None = None       # links a tender to its Annual Procurement Plan
    detail_url: str | None = None
    document_urls: list[str] = Field(default_factory=list)

    raw_document_id: int | None = None

    @field_validator(
        "title", "title_bn", "package_no", "reference_no", "description",
        "district", "upazila",
        "procurement_type", "procurement_method", "project_name",
        "eligibility_text", mode="before",
    )
    @classmethod
    def _normalize(cls, v: object) -> object:
        return normalize_text(v) if isinstance(v, str) else v

    # Fields that mean "this tender materially changed". Crawl-time noise
    # (serial numbers, page position) is deliberately excluded.
    HASH_FIELDS: ClassVar[tuple[str, ...]] = (
        "title", "package_no", "reference_no", "description", "district", "upazila",
        "procurement_nature", "procurement_type", "procurement_method",
        "estimated_value", "tender_security", "document_price",
        "published_at", "closing_at", "opening_at", "status",
    )

    def canonical_hash(self) -> str:
        """Stable hash over the fields that define a version (§6)."""
        payload = {}
        for name in self.HASH_FIELDS:
            value = getattr(self, name)
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, Decimal):
                value = str(value)
            elif isinstance(value, StrEnum):
                value = value.value
            payload[name] = value
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def changed_fields(self, other: TenderRecord) -> dict[str, dict[str, object]]:
        """Field-level diff against a previous version, for tender_versions."""
        diff: dict[str, dict[str, object]] = {}
        for name in self.HASH_FIELDS:
            old, new = getattr(other, name), getattr(self, name)
            if old != new:
                diff[name] = {
                    "old": str(old) if old is not None else None,
                    "new": str(new) if new is not None else None,
                }
        return diff

    def infer_change_type(self, previous: TenderRecord) -> ChangeType:
        diff = self.changed_fields(previous)
        if self.status == TenderStatus.CANCELLED:
            return ChangeType.CANCELLATION
        if "closing_at" in diff and self.closing_at and previous.closing_at:
            if self.closing_at > previous.closing_at:
                return ChangeType.EXTENSION
        return ChangeType.CORRIGENDUM
