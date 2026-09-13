"""In-process crawl scheduler (§5: APScheduler, no Celery, no broker).

The 30-minute interval is the product claim, so it lives in the sources table
per source rather than being hardcoded here.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

import tenderradar.adapters.egp  # noqa: F401
import tenderradar.adapters.egp_offline  # noqa: F401
from tenderradar.adapters import registry
from tenderradar.crawl.runner import CrawlRunner
from tenderradar.db.pool import close_pool, connection

log = logging.getLogger(__name__)


async def load_schedule() -> dict[str, int]:
    """Interval per enabled adapter, from the sources table."""
    async with connection() as conn:
        cur = await conn.execute(
            "SELECT adapter_key, crawl_interval_min FROM sources WHERE enabled"
        )
        rows = await cur.fetchall()
    return {r["adapter_key"]: int(r["crawl_interval_min"]) for r in rows}


async def run_forever(default_interval_min: int = 30) -> None:
    runner = CrawlRunner()
    scheduler = AsyncIOScheduler(timezone="UTC")
    configured = await load_schedule()

    for key in registry:
        interval = configured.get(key, default_interval_min)
        scheduler.add_job(
            runner.run,
            trigger=IntervalTrigger(minutes=interval),
            args=[key],
            id=f"crawl:{key}",
            # A slow sweep must never stack up behind itself against a
            # government host.
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            next_run_time=None,
        )
        log.info("scheduled %s every %d min", key, interval)

    scheduler.start()
    log.info("scheduler running; crawling %d source(s)", len(registry))

    # Crawl once at startup so a restart does not leave a 30-minute gap.
    for key in registry:
        await runner.run(key)

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("shutting down")
    finally:
        scheduler.shutdown(wait=False)
        await close_pool()
