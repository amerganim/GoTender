"""Forward-only migration runner (§9).

Each .sql file in migrations/ runs once, in filename order, inside its own
transaction. Applied files are recorded with a checksum: editing a migration
that has already run is an error, not a silent divergence.

    python -m tenderradar.db.migrate          # apply pending
    python -m tenderradar.db.migrate --status # show state
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sys
from pathlib import Path

from tenderradar.db.pool import close_pool, connection

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT        PRIMARY KEY,
    checksum    TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def discover() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


async def applied_versions() -> dict[str, str]:
    async with connection() as conn:
        await conn.execute(_BOOTSTRAP)
        await conn.commit()
        cur = await conn.execute("SELECT version, checksum FROM schema_migrations")
        return {row["version"]: row["checksum"] for row in await cur.fetchall()}


async def migrate() -> int:
    applied = await applied_versions()
    pending = []

    for path in discover():
        version = path.stem
        digest = checksum(path)
        if version in applied:
            if applied[version] != digest:
                raise RuntimeError(
                    f"migration {version} changed after it was applied "
                    f"({applied[version]} -> {digest}). Migrations are "
                    f"forward-only: add a new file instead of editing this one."
                )
            continue
        pending.append((version, digest, path))

    if not pending:
        log.info("no pending migrations (%d already applied)", len(applied))
        return 0

    for version, digest, path in pending:
        log.info("applying %s", version)
        async with connection() as conn:
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
                await conn.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (version, digest),
                )
        log.info("applied %s", version)

    return len(pending)


async def status() -> None:
    applied = await applied_versions()
    for path in discover():
        state = "applied" if path.stem in applied else "PENDING"
        print(f"  {state:>7}  {path.stem}")


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Run database migrations")
    parser.add_argument("--status", action="store_true", help="show state and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.status:
            await status()
        else:
            count = await migrate()
            print(f"applied {count} migration(s)")
    finally:
        await close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
