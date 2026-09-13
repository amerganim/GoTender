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
from urllib.parse import quote, urlencode
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


@app.get("/browse", response_class=HTMLResponse)
async def browse(request: Request) -> Response:
    """Index of every landing page -- the crawlable hub for the SEO surface."""
    async with connection() as conn:
        stats = await queries.site_stats(conn)
        districts = await queries.district_pages(conn)
        organizations = await queries.organization_pages(conn)
        facet = await queries.facets(conn, limit=50)

    return TEMPLATES.TemplateResponse(
        request,
        "browse.html",
        {
            "stats": stats,
            "districts": districts,
            "organizations": organizations,
            "facets": facet,
        },
    )


@app.get("/tenders/district/{district}", response_class=HTMLResponse)
async def district_page(
    request: Request, district: str, page: int = Query(1, ge=1)
) -> Response:
    return await _landing(
        request,
        TenderFilters(district=district, page=page),
        heading=f"Tenders in {district}",
        blurb=(
            f"Live government tender notices for {district} district, "
            f"updated every 30 minutes from the Bangladesh e-GP portal."
        ),
        canonical=f"/tenders/district/{district}",
    )


@app.get("/tenders/organization/{slug}", response_class=HTMLResponse)
async def organization_page(
    request: Request, slug: str, page: int = Query(1, ge=1)
) -> Response:
    async with connection() as conn:
        name = await queries.organization_by_slug(conn, slug)
    if name is None:
        async with connection() as conn:
            stats = await queries.site_stats(conn)
        return TEMPLATES.TemplateResponse(
            request, "not_found.html", {"stats": stats}, status_code=404
        )

    return await _landing(
        request,
        TenderFilters(organization=name, page=page),
        heading=f"Tenders from {name}",
        blurb=(
            f"Live tender notices published by {name}, updated every 30 "
            f"minutes from the Bangladesh e-GP portal."
        ),
        canonical=f"/tenders/organization/{slug}",
    )


@app.get("/tenders/type/{nature}", response_class=HTMLResponse)
async def nature_page(
    request: Request, nature: str, page: int = Query(1, ge=1)
) -> Response:
    return await _landing(
        request,
        TenderFilters(nature=nature, page=page),
        heading=f"{nature.title()} tenders in Bangladesh",
        blurb=(
            f"Live {nature} procurement notices from Bangladesh government "
            f"buyers, updated every 30 minutes."
        ),
        canonical=f"/tenders/type/{nature}",
    )


async def _landing(
    request: Request,
    filters: TenderFilters,
    *,
    heading: str,
    blurb: str,
    canonical: str,
) -> Response:
    """Shared renderer for the programmatic landing pages."""
    filters = filters.normalized()
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
            "heading": heading,
            "blurb": blurb,
            "canonical": canonical,
        },
    )


# Registered after the literal /tenders/* routes on purpose. Starlette
# matches in declaration order, so a catch-all declared earlier would
# capture /tenders/district/Dhaka and fail int validation with a 422.
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


@app.get("/sitemap.xml")
async def sitemap() -> Response:
    async with connection() as conn:
        data = await queries.sitemap_entries(conn)

    base = ""  # relative URLs; set SITE_BASE_URL when a domain exists
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"<url><loc>{base}/</loc><changefreq>hourly</changefreq>"
        f"<priority>1.0</priority></url>",
        f"<url><loc>{base}/tenders</loc><changefreq>hourly</changefreq>"
        f"<priority>0.9</priority></url>",
        f"<url><loc>{base}/browse</loc><changefreq>daily</changefreq>"
        f"<priority>0.8</priority></url>",
    ]
    for d in data["districts"]:
        parts.append(
            f"<url><loc>{base}/tenders/district/{quote(d['name'])}</loc>"
            f"<changefreq>daily</changefreq><priority>0.7</priority></url>"
        )
    for o in data["organizations"]:
        parts.append(
            f"<url><loc>{base}/tenders/organization/{quote(o['slug'])}</loc>"
            f"<changefreq>daily</changefreq><priority>0.6</priority></url>"
        )
    for t in data["tenders"]:
        updated = t["updated"]
        lastmod = f"<lastmod>{updated:%Y-%m-%d}</lastmod>" if updated else ""
        parts.append(
            f"<url><loc>{base}/tenders/{t['id']}</loc>{lastmod}"
            f"<changefreq>daily</changefreq><priority>0.5</priority></url>"
        )
    parts.append("</urlset>")
    return Response("\n".join(parts), media_type="application/xml")


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    # We ask e-GP's crawler etiquette of ourselves (§8), so we state ours.
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /healthz\n"
        "\n"
        "Sitemap: /sitemap.xml\n"
    )


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    async with connection() as conn:
        stats = await queries.site_stats(conn)
    return f"ok live={stats.live_tenders} fresh={stats.is_fresh}"
