"""Public tender directory (Phase 1).

Server-rendered on purpose: SEO is a core requirement, so no client-side SPA
(§5). Jinja2 templates, no build step, no JavaScript framework.
"""

from __future__ import annotations

import math
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates

from tenderradar.db.pool import close_pool, connection
from tenderradar.web import queries
from tenderradar.web.queries import TenderFilters

DHAKA = ZoneInfo("Asia/Dhaka")
TEMPLATES = Jinja2Templates(directory=str(__file__.rsplit("app.py", 1)[0] + "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await close_pool()


app = FastAPI(
    title="TenderRadar",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


# ------------------------------------------------------------- formatting


def dhaka(value: datetime | None, fmt: str = "%d %b %Y, %I:%M %p") -> str:
    """All timestamps are stored UTC and displayed Asia/Dhaka (§9)."""
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(DHAKA).strftime(fmt)


def taka(value: Decimal | None) -> str:
    """Format BDT with the lakh/crore grouping this market reads."""
    if value is None:
        return "—"
    whole = int(value)
    text = str(abs(whole))
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        text = ",".join(parts + [tail])
    return f"৳{text}"


def countdown(closing: datetime | None) -> str:
    """How long left to bid -- the number a contractor actually acts on."""
    if closing is None:
        return "—"
    if closing.tzinfo is None:
        closing = closing.replace(tzinfo=UTC)
    delta = closing - datetime.now(UTC)
    seconds = delta.total_seconds()
    if seconds <= 0:
        return "closed"
    days, rem = divmod(int(seconds), 86400)
    hours = rem // 3600
    if days:
        return f"{days}d {hours}h left"
    minutes = (rem % 3600) // 60
    return f"{hours}h {minutes}m left" if hours else f"{minutes}m left"


def urgency(closing: datetime | None) -> str:
    if closing is None:
        return ""
    if closing.tzinfo is None:
        closing = closing.replace(tzinfo=UTC)
    hours = (closing - datetime.now(UTC)).total_seconds() / 3600
    if hours <= 0:
        return "closed"
    if hours <= 24:
        return "urgent"
    if hours <= 72:
        return "soon"
    return ""


def qs(params: dict[str, object]) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return f"?{urlencode(clean)}" if clean else ""


TEMPLATES.env.filters["dhaka"] = dhaka
TEMPLATES.env.filters["taka"] = taka
TEMPLATES.env.filters["countdown"] = countdown
TEMPLATES.env.filters["urgency"] = urgency
TEMPLATES.env.filters["qs"] = qs


# ----------------------------------------------------------------- routes


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> Response:
    async with connection() as conn:
        stats = await queries.site_stats(conn)
        recent = await queries.recent_tenders(conn, limit=8)
        facet = await queries.facets(conn, limit=12)

    return TEMPLATES.TemplateResponse(
        request,
        "home.html",
        {"stats": stats, "recent": recent, "facets": facet},
    )


@app.get("/tenders", response_class=HTMLResponse)
async def tender_list(
    request: Request,
    q: str | None = None,
    district: str | None = None,
    organization: str | None = None,
    nature: str | None = None,
    method: str | None = None,
    closing: str | None = None,
    page: int = Query(1, ge=1),
) -> Response:
    filters = TenderFilters(
        q=q,
        district=district,
        organization=organization,
        nature=nature,
        method=method,
        closing=closing,
        page=page,
    ).normalized()

    async with connection() as conn:
        rows, total = await queries.list_tenders(conn, filters)
        facet = await queries.facets(conn, limit=15)
        stats = await queries.site_stats(conn)

    pages = max(1, math.ceil(total / filters.per_page))
    return TEMPLATES.TemplateResponse(
        request,
        "list.html",
        {
            "tenders": rows,
            "total": total,
            "filters": filters,
            "facets": facet,
            "stats": stats,
            "pages": pages,
        },
    )


@app.get("/tenders/{tender_id}", response_class=HTMLResponse)
async def tender_detail(request: Request, tender_id: int) -> Response:
    async with connection() as conn:
        tender = await queries.get_tender(conn, tender_id)
        if tender is None:
            return TEMPLATES.TemplateResponse(
                request, "not_found.html", {}, status_code=404
            )
        versions = await queries.get_tender_versions(conn, tender_id)
        stats = await queries.site_stats(conn)

    return TEMPLATES.TemplateResponse(
        request,
        "detail.html",
        {"t": tender, "versions": versions, "stats": stats},
    )


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    async with connection() as conn:
        stats = await queries.site_stats(conn)
    return f"ok live={stats.live_tenders} fresh={stats.is_fresh}"
