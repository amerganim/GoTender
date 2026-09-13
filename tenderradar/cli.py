"""Operator CLI.

    python -m tenderradar.cli probe    egp_tender --pages 1   # no DB needed
    python -m tenderradar.cli crawl    egp_tender [--pages N]
    python -m tenderradar.cli schedule                        # every 30 min
    python -m tenderradar.cli health                          # §8.5 dashboard
    python -m tenderradar.cli replay   <raw_document_id>      # §8.6
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

# Importing the adapters package populates the registry.
import tenderradar.adapters.egp  # noqa: F401
from tenderradar.adapters import get_adapter, registry
from tenderradar.adapters.base import FetchResult, PayloadKind
from tenderradar.config import settings
from tenderradar.crawl.archive import RawArchive
from tenderradar.crawl.runner import CrawlRunner
from tenderradar.crawl.scheduler import run_forever
from tenderradar.db.pool import close_pool, connection
from tenderradar.runtime import run as run_async

log = logging.getLogger("tenderradar")


async def cmd_probe(args: argparse.Namespace) -> int:
    """Fetch and parse without writing anything. The first thing to run."""
    adapter_cls = get_adapter(args.adapter)
    adapter = adapter_cls()
    total = 0
    try:
        async for result in adapter.fetch(max_pages=args.pages, page_size=args.size):
            records = adapter.parse(result)
            total += len(records)
            print(
                f"{result.kind} page {result.meta.get('page_no')} -> "
                f"{len(records)} records, {len(result.content)} bytes"
            )
            for record in records[: args.show]:
                print(
                    f"    [{record.external_ref}] {record.procurement_nature:8} "
                    f"{(record.title or '')[:70]}"
                )
                print(
                    f"        {record.organization.deepest} | "
                    f"closes {record.closing_at} | {record.procurement_method}"
                )
    finally:
        await adapter.aclose()

    print(f"\n{total} records parsed from {args.adapter}")
    return 0 if total else 1


async def cmd_crawl(args: argparse.Namespace) -> int:
    runner = CrawlRunner()
    report = await runner.run(
        args.adapter,
        max_pages=args.pages,
        fetch_details=not args.no_details,
        detail_limit=args.detail_limit,
    )
    print(report.summary())
    await close_pool()
    return 0 if report.status in ("ok", "partial") else 1


async def cmd_schedule(args: argparse.Namespace) -> int:
    await run_forever(default_interval_min=args.interval)
    return 0


async def cmd_health(_: argparse.Namespace) -> int:
    """Freshness and yield at a glance -- the numbers Gate 0 is judged on."""
    async with connection() as conn:
        cur = await conn.execute(
            """
            SELECT s.adapter_key,
                   s.last_success_at,
                   COUNT(*) FILTER (WHERE t.status = 'live') AS live_tenders,
                   MAX(t.first_seen_at)                      AS newest_tender
              FROM sources s
              LEFT JOIN tenders t ON t.source_id = s.id
             GROUP BY s.id, s.adapter_key, s.last_success_at
             ORDER BY s.adapter_key
            """
        )
        sources = await cur.fetchall()

        cur = await conn.execute(
            """
            SELECT cr.id, s.adapter_key, cr.started_at, cr.status,
                   cr.items_found, cr.items_new, cr.items_changed,
                   cr.parse_failures, cr.yield_anomaly
              FROM crawl_runs cr JOIN sources s ON s.id = cr.source_id
             ORDER BY cr.started_at DESC
             LIMIT 10
            """
        )
        runs = await cur.fetchall()

        cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM parse_failures WHERE resolved_at IS NULL"
        )
        row = await cur.fetchone()
        unresolved = int(row["n"]) if row else 0

    print("SOURCES")
    for s in sources:
        print(
            f"  {s['adapter_key']:<14} live={s['live_tenders']:<6} "
            f"last_success={s['last_success_at']}"
        )

    print("\nRECENT RUNS")
    for r in runs:
        flag = "  <-- YIELD ANOMALY" if r["yield_anomaly"] else ""
        print(
            f"  {r['started_at']:%Y-%m-%d %H:%M} {r['adapter_key']:<14} "
            f"{r['status']:<8} found={r['items_found']:<6} new={r['items_new']:<5} "
            f"changed={r['items_changed']:<5} fails={r['parse_failures']}{flag}"
        )

    print(f"\nUnresolved parse failures: {unresolved}")
    await close_pool()
    return 0


async def cmd_replay(args: argparse.Namespace) -> int:
    """Re-parse an archived document after a parser fix (§8.6)."""
    async with connection() as conn:
        cur = await conn.execute(
            """
            SELECT rd.*, s.adapter_key
              FROM raw_documents rd JOIN sources s ON s.id = rd.source_id
             WHERE rd.id = %s
            """,
            (args.raw_document_id,),
        )
        row = await cur.fetchone()

    if row is None:
        print(f"no raw_document with id {args.raw_document_id}")
        await close_pool()
        return 1

    content = RawArchive().load(row["storage_path"])
    adapter = get_adapter(row["adapter_key"])()
    records = adapter.parse(
        FetchResult(
            url=row["url"],
            content=content,
            kind=PayloadKind(row["kind"]),
            fetched_at=row["fetched_at"],
            meta=row["meta"] or {},
        )
    )
    print(f"replayed raw_document {args.raw_document_id}: {len(records)} records")
    for record in records[:5]:
        print(f"  [{record.external_ref}] {(record.title or '')[:70]}")

    await adapter.aclose()
    await close_pool()
    return 0


def _force_utf8_output() -> None:
    """Print UTF-8 regardless of the console's codepage (§9).

    Windows consoles default to a legacy codepage (cp1252 here), which cannot
    encode Bangla. Without this, printing a single Bangla tender title raises
    UnicodeEncodeError and kills the command halfway through a crawl. errors
    are replaced rather than raised so operator output can never be the thing
    that breaks a run.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(prog="tenderradar")
    parser.add_argument("--verbose", "-v", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="fetch and parse, write nothing")
    p.add_argument("adapter", choices=sorted(registry))
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--size", type=int, default=10)
    p.add_argument("--show", type=int, default=3, help="records to print per page")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("crawl", help="run one full crawl into the database")
    p.add_argument("adapter", choices=sorted(registry))
    p.add_argument("--pages", type=int, default=None, help="limit pages (dev)")
    p.add_argument("--no-details", action="store_true")
    p.add_argument("--detail-limit", type=int, default=200)
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("schedule", help="crawl on a schedule, forever")
    p.add_argument("--interval", type=int, default=30, help="minutes")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("health", help="freshness, yield and failures")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("replay", help="re-parse an archived document")
    p.add_argument("raw_document_id", type=int)
    p.set_defaults(func=cmd_replay)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    return run_async(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
