"""Async connection pool. One Postgres, no other datastore (§5)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from tenderradar.config import settings

_pool: AsyncConnectionPool | None = None


def _require_url() -> str:
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
        )
    return settings.database_url


async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        pool = AsyncConnectionPool(
            _require_url(),
            min_size=1,
            # A solo-operator VPS and a Neon free tier both dislike large pools.
            max_size=5,
            open=False,
            kwargs={"row_factory": dict_row},
        )
        try:
            await pool.open(wait=True)
        except Exception:
            # Never cache a pool that failed to open. Doing so turns one
            # startup failure into every later request raising PoolClosed,
            # which hides the real cause completely.
            await pool.close()
            raise
        _pool = pool
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def connection() -> AsyncIterator[AsyncConnection]:
    pool = await get_pool()
    async with pool.connection() as conn:
        yield conn
