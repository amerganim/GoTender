"""Crawl orchestration.

Order of operations per payload, and it matters:

    fetch -> ARCHIVE -> record raw_documents row -> parse -> upsert

Archiving before parsing (§8.4) is what makes a parser bug recoverable: the
bytes are already on disk, so a fixed parser replays them without touching the
source again.

Yield monitoring (§8.5) is the other load-bearing piece. Scrapers do not crash,
they quietly return zero, and users never complain about tenders they never
heard about -- they just stop paying.
"""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from tenderradar.adapters.base import (
    FetchResult,
    PayloadKind,
    SourceAdapter,
    get_adapter,
)
from tenderradar.crawl.archive import RawArchive
from tenderradar.db import repo
from tenderradar.db.pool import connection
from tenderradar.db.repo import UpsertResult

log = logging.getLogger(__name__)

# §8.5: a run yielding less than this fraction of its trailing average is
# treated as a silent failure, not a quiet day.
YIELD_ANOMALY_RATIO = Decimal("0.5")
# Below this many runs of history the average is not yet meaningful.
MIN_RUNS_FOR_BASELINE = 3


@dataclass(slots=True)
class RunReport:
    source_key: str
    run_id: int | None = None
    pages_fetched: int = 0
    items_found: int = 0
    items_new: int = 0
    items_changed: int = 0
    items_unchanged: int = 0
    parse_failures: int = 0
    bytes_archived: int = 0
    yield_anomaly: bool = False
    baseline: Decimal | None = None
    # Only a full sweep is a valid yield yardstick (§8.5).
    is_full_sweep: bool = True
    status: str = "running"
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def duration_sec(self) -> float:
        return (datetime.now(UTC) - self.started_at).total_seconds()

    def summary(self) -> str:
        parts = [
            f"{self.source_key}: {self.status}",
            f"{self.items_found} found",
            f"{self.items_new} new",
            f"{self.items_changed} changed",
            f"{self.pages_fetched} pages",
            f"{self.duration_sec:.1f}s",
        ]
        if self.parse_failures:
            parts.append(f"{self.parse_failures} PARSE FAILURES")
        if not self.is_full_sweep:
            parts.append("partial (not a baseline)")
        if self.yield_anomaly:
            parts.append(f"YIELD ANOMALY (baseline {self.baseline})")
        return " | ".join(parts)


class CrawlRunner:
    def __init__(self, archive: RawArchive | None = None) -> None:
        self.archive = archive or RawArchive()

    async def run(
        self,
        adapter_key: str,
        *,
        fetch_details: bool = True,
        detail_limit: int = 200,
        **fetch_kwargs: object,
    ) -> RunReport:
        adapter_cls = get_adapter(adapter_key)
        # A page-capped run covers only part of the source, so it is neither
        # judged against the baseline nor allowed to become part of it.
        is_full_sweep = fetch_kwargs.get("max_pages") is None
        report = RunReport(source_key=adapter_key, is_full_sweep=is_full_sweep)

        async with connection() as conn:
            source_id = await repo.ensure_source(conn, adapter_cls)
            run_id = await repo.start_run(
                conn, source_id, is_full_sweep=is_full_sweep
            )
            await conn.commit()
        report.run_id = run_id

        adapter = adapter_cls()
        try:
            await self._sweep(adapter, source_id, run_id, report, fetch_kwargs)

            if fetch_details:
                await self._detail_pass(
                    adapter, source_id, run_id, report, detail_limit
                )

            report.status = "partial" if report.parse_failures else "ok"
        except Exception as exc:  # noqa: BLE001 - a run must never crash the scheduler
            report.status = "failed"
            report.error = f"{type(exc).__name__}: {exc}"
            log.exception("crawl of %s failed", adapter_key)
        finally:
            await adapter.aclose()

        await self._finalize(source_id, run_id, report)
        log.info("%s", report.summary())
        return report

    async def _sweep(
        self,
        adapter: SourceAdapter,
        source_id: int,
        run_id: int,
        report: RunReport,
        fetch_kwargs: dict[str, object],
    ) -> None:
        async for result in adapter.fetch(**fetch_kwargs):
            report.pages_fetched += 1
            await self._ingest(adapter, source_id, run_id, result, report)

    async def _detail_pass(
        self,
        adapter: SourceAdapter,
        source_id: int,
        run_id: int,
        report: RunReport,
        limit: int,
    ) -> None:
        """Pull detail pages only for tenders we have never seen in full."""
        async with connection() as conn:
            pending = await repo.tenders_needing_detail(conn, source_id, limit)

        if not pending:
            return

        log.info("fetching %d detail pages for %s", len(pending), adapter.key)
        async for result in adapter.fetch(detail_ids=pending):
            report.pages_fetched += 1
            await self._ingest(adapter, source_id, run_id, result, report)

    async def _ingest(
        self,
        adapter: SourceAdapter,
        source_id: int,
        run_id: int,
        result: FetchResult,
        report: RunReport,
    ) -> None:
        # 1. Archive raw bytes BEFORE anything tries to understand them (§8.4).
        archived = self.archive.store(adapter.key, result)
        report.bytes_archived += archived.bytes_written

        async with connection() as conn:
            raw_id = await repo.record_raw(
                conn, source_id, run_id, result, archived
            )
            await conn.commit()

        result.raw_document_id = raw_id
        is_detail = result.kind is PayloadKind.DETAIL

        # 2. Parse. A failure is recorded against the raw document so it can be
        #    replayed after a parser fix (§8.6), and never aborts the run.
        try:
            records = adapter.parse(result)
        except Exception as exc:  # noqa: BLE001
            report.parse_failures += 1
            log.error("parse failed for raw_document %d: %s", raw_id, exc)
            async with connection() as conn:
                await repo.record_parse_failure(
                    conn,
                    raw_document_id=raw_id,
                    crawl_run_id=run_id,
                    adapter_key=adapter.key,
                    error=str(exc),
                    traceback=traceback.format_exc(),
                )
                await conn.commit()
            return

        # 3. Upsert, with dedup and versioning handled in the repo.
        async with connection() as conn:
            for record in records:
                report.items_found += 1
                outcome = await repo.upsert_tender(
                    conn,
                    source_id,
                    adapter.key,
                    record,
                    raw_document_id=raw_id,
                    is_detail=is_detail,
                )
                if outcome.result is UpsertResult.NEW:
                    report.items_new += 1
                elif outcome.result is UpsertResult.CHANGED:
                    report.items_changed += 1
                else:
                    report.items_unchanged += 1
            await conn.commit()

    async def _finalize(self, source_id: int, run_id: int, report: RunReport) -> None:
        async with connection() as conn:
            report.baseline = await repo.trailing_yield(conn, source_id)
            report.yield_anomaly = self._is_anomalous(report)
            await repo.finish_run(
                conn,
                run_id,
                status=report.status,
                items_found=report.items_found,
                items_new=report.items_new,
                items_changed=report.items_changed,
                pages_fetched=report.pages_fetched,
                parse_failures=report.parse_failures,
                yield_anomaly=report.yield_anomaly,
                error=report.error,
            )
            await conn.commit()

        if report.yield_anomaly:
            # §8.5: this is the alert that matters. Wire it to Sentry/email
            # before running unattended.
            log.error(
                "YIELD ANOMALY on %s: %d items against a baseline of %s",
                report.source_key, report.items_found, report.baseline,
            )

    @staticmethod
    def _is_anomalous(report: RunReport) -> bool:
        """Compare this run's yield against its trailing average (§8.5)."""
        if report.status == "failed":
            return True
        # A successful sweep that found nothing is always suspicious: the live
        # pool is never empty. True of partial runs too.
        if report.items_found == 0:
            return True
        # A capped run is expected to be small; comparing it to a full-sweep
        # baseline would fire on every dev run.
        if not report.is_full_sweep:
            return False
        if report.baseline is None:
            return False
        return Decimal(report.items_found) < report.baseline * YIELD_ANOMALY_RATIO
