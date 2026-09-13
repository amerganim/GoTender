"""Persistence for the crawl pipeline: sources, runs, raw docs, tenders.

The one subtle rule in here is in upsert_tender(). A list page carries a subset
of a tender's fields; the detail page carries all of them. If a list record
overwrote a detail record, district and security would be nulled on every sweep
and the canonical hash would flip back and forth, manufacturing a fresh
"corrigendum" version every 30 minutes. So incoming records are MERGED over the
stored row -- a None never overwrites a known value -- and the hash is computed
on the merged result.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from tenderradar.adapters.base import FetchResult, SourceAdapter
from tenderradar.crawl.archive import ArchivedPayload
from tenderradar.models import ChangeType, TenderRecord

log = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    """Coerce adapter meta into something json-serializable."""
    return json.loads(json.dumps(value, default=str))


class UpsertResult(StrEnum):
    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


@dataclass(slots=True)
class UpsertOutcome:
    tender_id: int
    result: UpsertResult
    changed_fields: dict[str, Any]


async def ensure_source(conn: AsyncConnection, adapter: type[SourceAdapter]) -> int:
    """Register an adapter as a source row, returning its id."""
    cur = await conn.execute(
        """
        INSERT INTO sources (name, base_url, adapter_key, expected_yield_per_run)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (adapter_key) DO UPDATE
            SET name = EXCLUDED.name, base_url = EXCLUDED.base_url
        RETURNING id
        """,
        (
            adapter.name,
            adapter.base_url,
            adapter.key,
            adapter.expected_yield_per_run,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row["id"])


async def start_run(conn: AsyncConnection, source_id: int) -> int:
    cur = await conn.execute(
        "INSERT INTO crawl_runs (source_id) VALUES (%s) RETURNING id", (source_id,)
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row["id"])


async def finish_run(
    conn: AsyncConnection,
    run_id: int,
    *,
    status: str,
    items_found: int = 0,
    items_new: int = 0,
    items_changed: int = 0,
    pages_fetched: int = 0,
    parse_failures: int = 0,
    yield_anomaly: bool = False,
    error: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE crawl_runs
           SET finished_at = now(), status = %s, items_found = %s, items_new = %s,
               items_changed = %s, pages_fetched = %s, parse_failures = %s,
               yield_anomaly = %s, error = %s
         WHERE id = %s
        """,
        (
            status, items_found, items_new, items_changed, pages_fetched,
            parse_failures, yield_anomaly, error, run_id,
        ),
    )
    if status == "ok":
        await conn.execute(
            """
            UPDATE sources SET last_success_at = now()
             WHERE id = (SELECT source_id FROM crawl_runs WHERE id = %s)
            """,
            (run_id,),
        )


async def record_raw(
    conn: AsyncConnection,
    source_id: int,
    run_id: int | None,
    result: FetchResult,
    archived: ArchivedPayload,
) -> int:
    """Insert the raw_documents row for an already-archived payload."""
    cur = await conn.execute(
        """
        INSERT INTO raw_documents
            (source_id, crawl_run_id, url, kind, fetched_at,
             content_hash, storage_path, content_type, meta)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            source_id,
            run_id,
            result.url,
            str(result.kind),
            result.fetched_at,
            archived.content_hash,
            archived.storage_path,
            result.content_type,
            Jsonb(_jsonable(result.meta)),
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row["id"])


async def record_parse_failure(
    conn: AsyncConnection,
    *,
    raw_document_id: int,
    crawl_run_id: int | None,
    adapter_key: str,
    error: str,
    traceback: str | None = None,
) -> None:
    """§8.6: keep the raw_document_id so the failure can be replayed."""
    await conn.execute(
        """
        INSERT INTO parse_failures
            (raw_document_id, crawl_run_id, adapter_key, error, traceback)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (raw_document_id, crawl_run_id, adapter_key, error[:2000], traceback),
    )


def _row_to_record(row: dict[str, Any], source_key: str) -> TenderRecord:
    """Rebuild enough of a TenderRecord from a stored row to diff against."""
    return TenderRecord(
        source_key=source_key,
        external_ref=row["external_ref"],
        title=row.get("title"),
        title_bn=row.get("title_bn"),
        package_no=row.get("package_no"),
        reference_no=row.get("reference_no"),
        description=row.get("description"),
        district=row.get("district_name"),
        upazila=row.get("upazila_name"),
        procurement_nature=row["procurement_nature"],
        procurement_type=row.get("procurement_type"),
        procurement_method=row.get("procurement_method"),
        estimated_value=row.get("estimated_value"),
        tender_security=row.get("tender_security"),
        document_price=row.get("document_price"),
        published_at=row.get("published_at"),
        closing_at=row.get("closing_at"),
        opening_at=row.get("opening_at"),
        document_last_selling_at=row.get("document_last_selling_at"),
        status=row["status"],
        categories=list(row.get("categories") or []),
        eligibility_text=row.get("eligibility_text"),
        project_name=row.get("project_name"),
        app_id=row.get("app_id"),
        detail_url=row.get("detail_url"),
    )


def merge_records(
    stored: TenderRecord, incoming: TenderRecord, *, fill_only: bool = False
) -> TenderRecord:
    """Overlay incoming onto stored; a None or empty value never erases data.

    This is what lets a thin list row and a rich detail row describe the same
    tender without fighting each other.

    fill_only makes the overlay strictly additive: incoming may populate fields
    that are still empty, but may not change any field that already holds a
    value. That is how a list sweep is prevented from overwriting authoritative
    detail data. It is not enough to skip None values, because the two views
    genuinely disagree on some fields -- the list renders a title as
    "Procurement of surgical equipment" where the detail page says "Procurement
    of Surgical Equipment" -- and each sweep would otherwise rewrite the other,
    producing an endless stream of corrigenda.
    """
    updates: dict[str, Any] = {}
    for name in TenderRecord.model_fields:
        if name in ("source_key", "external_ref"):
            continue
        value = getattr(incoming, name)
        if value is None:
            continue
        if isinstance(value, (list, str)) and len(value) == 0:
            continue
        if fill_only:
            current = getattr(stored, name)
            has_value = current is not None and not (
                isinstance(current, (list, str)) and len(current) == 0
            )
            if has_value:
                continue
        updates[name] = value
    return stored.model_copy(update=updates)


async def upsert_tender(
    conn: AsyncConnection,
    source_id: int,
    source_key: str,
    incoming: TenderRecord,
    *,
    raw_document_id: int | None = None,
    is_detail: bool = False,
) -> UpsertOutcome:
    """Insert, version, or touch a tender. See module docstring for the merge rule."""
    cur = await conn.execute(
        "SELECT * FROM tenders WHERE source_id = %s AND external_ref = %s FOR UPDATE",
        (source_id, incoming.external_ref),
    )
    existing = await cur.fetchone()

    if existing is None:
        return await _insert_tender(
            conn, source_id, incoming, raw_document_id, is_detail
        )

    stored = _row_to_record(existing, source_key)
    # Detail pages are authoritative. Once one has been seen, a later list
    # sweep may only fill gaps, never contradict it.
    seen_detail = existing.get("detail_fetched_at") is not None
    merged = merge_records(
        stored, incoming, fill_only=seen_detail and not is_detail
    )
    new_hash = merged.canonical_hash()

    if new_hash == existing["canonical_hash"]:
        await conn.execute(
            """
            UPDATE tenders
               SET last_seen_at = now(),
                   detail_fetched_at = CASE WHEN %s THEN now() ELSE detail_fetched_at END
             WHERE id = %s
            """,
            (is_detail, existing["id"]),
        )
        return UpsertOutcome(int(existing["id"]), UpsertResult.UNCHANGED, {})

    diff = merged.changed_fields(stored)
    tender_id = int(existing["id"])

    if not diff:
        # The hash moved but no field did. That is always a serialization bug
        # (a Decimal scale, a timezone, a normalization form), never a real
        # corrigendum. Writing a version here would spam every subscriber with
        # phantom amendments, so store the corrected hash to self-heal and say
        # so loudly instead.
        log.warning(
            "hash changed with no field diff for %s/%s; healing stored hash",
            source_key,
            incoming.external_ref,
        )
        await conn.execute(
            """
            UPDATE tenders
               SET canonical_hash = %s, last_seen_at = now(),
                   detail_fetched_at = CASE WHEN %s THEN now() ELSE detail_fetched_at END
             WHERE id = %s
            """,
            (new_hash, is_detail, tender_id),
        )
        return UpsertOutcome(tender_id, UpsertResult.UNCHANGED, {})

    change_type = merged.infer_change_type(stored)

    cur = await conn.execute(
        "SELECT COALESCE(MAX(version_no), 0) AS v FROM tender_versions WHERE tender_id = %s",
        (tender_id,),
    )
    row = await cur.fetchone()
    next_version = int(row["v"]) + 1 if row else 1

    cur = await conn.execute(
        """
        INSERT INTO tender_versions
            (tender_id, version_no, changed_fields, canonical_hash,
             raw_document_id, change_type)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            tender_id,
            next_version,
            Jsonb(diff),
            new_hash,
            raw_document_id,
            str(change_type),
        ),
    )
    version_row = await cur.fetchone()
    assert version_row is not None

    await _update_tender_columns(
        conn, tender_id, merged, new_hash, int(version_row["id"]), is_detail
    )
    return UpsertOutcome(tender_id, UpsertResult.CHANGED, diff)


async def _insert_tender(
    conn: AsyncConnection,
    source_id: int,
    record: TenderRecord,
    raw_document_id: int | None,
    is_detail: bool,
) -> UpsertOutcome:
    cur = await conn.execute(
        """
        INSERT INTO tenders
            (source_id, external_ref, reference_no, package_no, title, title_bn,
             description, organization_path, district_name, upazila_name,
             procurement_nature, procurement_type, procurement_method,
             estimated_value, tender_security, document_price,
             published_at, closing_at, opening_at, document_last_selling_at,
             status, categories, lots, eligibility_text, project_name, app_id,
             detail_url, canonical_hash, detail_fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            source_id,
            record.external_ref,
            record.reference_no,
            record.package_no,
            record.title,
            record.title_bn,
            record.description,
            record.organization.deepest,
            record.district,
            record.upazila,
            str(record.procurement_nature),
            record.procurement_type,
            record.procurement_method,
            record.estimated_value,
            record.tender_security,
            record.document_price,
            record.published_at,
            record.closing_at,
            record.opening_at,
            record.document_last_selling_at,
            str(record.status),
            record.categories,
            Jsonb([lot.model_dump(mode="json") for lot in record.lots]),
            record.eligibility_text,
            record.project_name,
            record.app_id,
            record.detail_url,
            record.canonical_hash(),
            datetime.now(UTC) if is_detail else None,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    tender_id = int(row["id"])

    cur = await conn.execute(
        """
        INSERT INTO tender_versions
            (tender_id, version_no, changed_fields, canonical_hash,
             raw_document_id, change_type)
        VALUES (%s, 1, '{}'::jsonb, %s, %s, %s)
        RETURNING id
        """,
        (tender_id, record.canonical_hash(), raw_document_id, str(ChangeType.NEW)),
    )
    version_row = await cur.fetchone()
    assert version_row is not None
    await conn.execute(
        "UPDATE tenders SET current_version_id = %s WHERE id = %s",
        (int(version_row["id"]), tender_id),
    )
    return UpsertOutcome(tender_id, UpsertResult.NEW, {})


async def _update_tender_columns(
    conn: AsyncConnection,
    tender_id: int,
    merged: TenderRecord,
    new_hash: str,
    version_id: int,
    is_detail: bool,
) -> None:
    await conn.execute(
        """
        UPDATE tenders
           SET reference_no = %s, package_no = %s, title = %s, title_bn = %s,
               description = %s, organization_path = %s, district_name = %s,
               upazila_name = %s, procurement_nature = %s, procurement_type = %s,
               procurement_method = %s, estimated_value = %s, tender_security = %s,
               document_price = %s, published_at = %s, closing_at = %s,
               opening_at = %s, document_last_selling_at = %s, status = %s,
               categories = %s, lots = %s, eligibility_text = %s,
               project_name = %s, app_id = %s, detail_url = %s,
               canonical_hash = %s, current_version_id = %s, last_seen_at = now(),
               detail_fetched_at = CASE WHEN %s THEN now() ELSE detail_fetched_at END
         WHERE id = %s
        """,
        (
            merged.reference_no,
            merged.package_no,
            merged.title,
            merged.title_bn,
            merged.description,
            merged.organization.deepest,
            merged.district,
            merged.upazila,
            str(merged.procurement_nature),
            merged.procurement_type,
            merged.procurement_method,
            merged.estimated_value,
            merged.tender_security,
            merged.document_price,
            merged.published_at,
            merged.closing_at,
            merged.opening_at,
            merged.document_last_selling_at,
            str(merged.status),
            merged.categories,
            Jsonb([lot.model_dump(mode="json") for lot in merged.lots]),
            merged.eligibility_text,
            merged.project_name,
            merged.app_id,
            merged.detail_url,
            new_hash,
            version_id,
            is_detail,
            tender_id,
        ),
    )


async def tenders_needing_detail(
    conn: AsyncConnection, source_id: int, limit: int = 200
) -> list[str]:
    """External refs whose detail page has never been fetched, newest first.

    Detail pages are pulled only for tenders we have not seen in full, which
    keeps a 30-minute sweep to a few dozen extra requests instead of ~3,800.
    """
    cur = await conn.execute(
        """
        SELECT external_ref
          FROM tenders
         WHERE source_id = %s AND detail_fetched_at IS NULL AND status = 'live'
         ORDER BY published_at DESC NULLS LAST
         LIMIT %s
        """,
        (source_id, limit),
    )
    return [row["external_ref"] for row in await cur.fetchall()]


async def trailing_yield(
    conn: AsyncConnection, source_id: int, runs: int = 10
) -> Decimal | None:
    """Mean items_found over recent successful runs, for §8.5 anomaly checks."""
    cur = await conn.execute(
        """
        SELECT AVG(items_found)::numeric AS avg_found
          FROM (
              SELECT items_found
                FROM crawl_runs
               WHERE source_id = %s AND status = 'ok' AND items_found > 0
               ORDER BY started_at DESC
               LIMIT %s
          ) recent
        """,
        (source_id, runs),
    )
    row = await cur.fetchone()
    return row["avg_found"] if row and row["avg_found"] is not None else None


async def live_tender_count(conn: AsyncConnection, source_id: int) -> int:
    cur = await conn.execute(
        "SELECT COUNT(*) AS n FROM tenders WHERE source_id = %s AND status = 'live'",
        (source_id,),
    )
    row = await cur.fetchone()
    return int(row["n"]) if row else 0
