"""Read-only queries for the public directory (Phase 1).

Everything here is server-rendered and must stay fast on a 3G connection
(Gate 1), so queries are indexed, paginated and never N+1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection

PER_PAGE = 25
MAX_PER_PAGE = 100


@dataclass(slots=True)
class TenderFilters:
    """Everything the browse page can filter on (§4, Phase 1)."""

    q: str | None = None
    district: str | None = None
    organization: str | None = None
    nature: str | None = None
    method: str | None = None
    closing: str | None = None  # today | week | open
    status: str = "live"
    page: int = 1
    per_page: int = PER_PAGE

    def normalized(self) -> TenderFilters:
        self.page = max(1, self.page)
        self.per_page = min(max(1, self.per_page), MAX_PER_PAGE)
        self.q = (self.q or "").strip() or None
        return self

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    @property
    def is_filtered(self) -> bool:
        return any(
            [self.q, self.district, self.organization, self.nature,
             self.method, self.closing]
        )

    def query_params(self, **overrides: Any) -> dict[str, Any]:
        """Current filters as querystring params, for building links."""
        params = {
            "q": self.q,
            "district": self.district,
            "organization": self.organization,
            "nature": self.nature,
            "method": self.method,
            "closing": self.closing,
        }
        params.update(overrides)
        return {k: v for k, v in params.items() if v}


def _where(filters: TenderFilters) -> tuple[list[str], dict[str, Any]]:
    clauses = ["t.status = %(status)s"]
    params: dict[str, Any] = {"status": filters.status}

    if filters.q:
        # websearch_to_tsquery understands quoted phrases and "-word", and
        # never raises on malformed input the way to_tsquery does.
        clauses.append(
            "(t.search_vector @@ websearch_to_tsquery('simple', %(q)s)"
            " OR t.package_no ILIKE %(qlike)s"
            " OR t.reference_no ILIKE %(qlike)s)"
        )
        params["q"] = filters.q
        params["qlike"] = f"%{filters.q}%"
    if filters.district:
        clauses.append("t.district_name = %(district)s")
        params["district"] = filters.district
    if filters.organization:
        clauses.append("t.organization_path = %(organization)s")
        params["organization"] = filters.organization
    if filters.nature:
        clauses.append("t.procurement_nature = %(nature)s")
        params["nature"] = filters.nature
    if filters.method:
        clauses.append("t.procurement_method = %(method)s")
        params["method"] = filters.method

    if filters.closing == "today":
        clauses.append(
            "t.closing_at >= now() AND t.closing_at < now() + interval '1 day'"
        )
    elif filters.closing == "week":
        clauses.append(
            "t.closing_at >= now() AND t.closing_at < now() + interval '7 days'"
        )
    elif filters.closing == "open":
        clauses.append("t.closing_at >= now()")

    return clauses, params


async def list_tenders(
    conn: AsyncConnection, filters: TenderFilters
) -> tuple[list[dict[str, Any]], int]:
    """One page of tenders plus the total count."""
    filters = filters.normalized()
    clauses, params = _where(filters)
    where = " AND ".join(clauses)

    # Relevance first when searching, otherwise soonest-closing first: a
    # contractor cares most about what is about to shut.
    order = (
        "ts_rank(t.search_vector, websearch_to_tsquery('simple', %(q)s)) DESC,"
        " t.closing_at ASC"
        if filters.q
        else "t.closing_at ASC NULLS LAST"
    )

    params["limit"] = filters.per_page
    params["offset"] = filters.offset

    cur = await conn.execute(
        f"""
        SELECT t.id, t.external_ref, t.title, t.package_no, t.reference_no,
               t.organization_path, t.district_name, t.procurement_nature,
               t.procurement_method, t.procurement_type, t.tender_security,
               t.document_price, t.published_at, t.closing_at, t.status,
               t.detail_url
          FROM tenders t
         WHERE {where}
         ORDER BY {order}
         LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    rows = await cur.fetchall()

    cur = await conn.execute(
        f"SELECT count(*) AS n FROM tenders t WHERE {where}", params
    )
    total_row = await cur.fetchone()
    return list(rows), int(total_row["n"]) if total_row else 0


async def get_tender(conn: AsyncConnection, tender_id: int) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM tenders WHERE id = %s", (tender_id,))
    return await cur.fetchone()


# Changes the procuring entity actually made. 'enrichment' is our own
# ingestion filling in fields a list sweep could not carry, and 'new' is the
# tender first appearing; neither is an amendment and neither belongs in a
# history a contractor reads.
AMENDMENT_TYPES = ("corrigendum", "cancellation", "extension")


async def get_tender_versions(
    conn: AsyncConnection, tender_id: int, *, amendments_only: bool = True
) -> list[dict[str, Any]]:
    """Amendment history -- the corrigenda competitors make you hunt for.

    amendments_only excludes our own enrichment steps. Pass False for the
    operator view, where seeing the full ingestion trail is the point.
    """
    clause = "AND change_type = ANY(%(types)s)" if amendments_only else ""
    cur = await conn.execute(
        f"""
        SELECT version_no, change_type, changed_fields, observed_at
          FROM tender_versions
         WHERE tender_id = %(id)s {clause}
         ORDER BY version_no DESC
        """,
        {"id": tender_id, "types": list(AMENDMENT_TYPES)},
    )
    return list(await cur.fetchall())


@dataclass(slots=True)
class SiteStats:
    """The freshness claim, which is the whole positioning (§3)."""

    live_tenders: int = 0
    last_crawl_at: datetime | None = None
    closing_today: int = 0
    closing_week: int = 0
    added_today: int = 0
    sources: list[dict[str, Any]] = field(default_factory=list)

    @property
    def minutes_since_crawl(self) -> int | None:
        if self.last_crawl_at is None:
            return None
        delta = datetime.now(UTC) - self.last_crawl_at
        return max(0, int(delta.total_seconds() // 60))

    @property
    def is_fresh(self) -> bool:
        """Under the 30-minute claim we make on the homepage."""
        minutes = self.minutes_since_crawl
        return minutes is not None and minutes <= 30


async def site_stats(conn: AsyncConnection) -> SiteStats:
    cur = await conn.execute(
        """
        SELECT
            count(*) FILTER (WHERE status = 'live')                AS live_tenders,
            count(*) FILTER (WHERE status = 'live'
                             AND closing_at >= now()
                             AND closing_at < now() + interval '1 day')
                                                                    AS closing_today,
            count(*) FILTER (WHERE status = 'live'
                             AND closing_at >= now()
                             AND closing_at < now() + interval '7 days')
                                                                    AS closing_week,
            count(*) FILTER (WHERE first_seen_at > now() - interval '1 day')
                                                                    AS added_today
          FROM tenders
        """
    )
    row = await cur.fetchone() or {}

    cur = await conn.execute("SELECT max(last_success_at) AS at FROM sources")
    crawl = await cur.fetchone()

    cur = await conn.execute(
        """
        SELECT s.name, s.adapter_key, s.last_success_at,
               count(t.id) FILTER (WHERE t.status = 'live') AS live_tenders
          FROM sources s LEFT JOIN tenders t ON t.source_id = s.id
         GROUP BY s.id, s.name, s.adapter_key, s.last_success_at
         ORDER BY s.name
        """
    )

    return SiteStats(
        live_tenders=int(row.get("live_tenders") or 0),
        last_crawl_at=crawl["at"] if crawl else None,
        closing_today=int(row.get("closing_today") or 0),
        closing_week=int(row.get("closing_week") or 0),
        added_today=int(row.get("added_today") or 0),
        sources=list(await cur.fetchall()),
    )


async def facets(conn: AsyncConnection, limit: int = 20) -> dict[str, list[Any]]:
    """Browse-by options, commonest first."""
    async def top(column: str, n: int) -> list[dict[str, Any]]:
        cur = await conn.execute(
            f"""
            SELECT {column} AS value, count(*) AS n
              FROM tenders
             WHERE status = 'live' AND {column} IS NOT NULL
             GROUP BY {column}
             ORDER BY n DESC
             LIMIT %s
            """,
            (n,),
        )
        return list(await cur.fetchall())

    return {
        "districts": await top("district_name", limit),
        "organizations": await top("organization_path", limit),
        "natures": await top("procurement_nature", 10),
        "methods": await top("procurement_method", 10),
    }


async def recent_tenders(
    conn: AsyncConnection, limit: int = 10
) -> list[dict[str, Any]]:
    """Newest arrivals -- the visible proof of the 30-minute claim."""
    cur = await conn.execute(
        """
        SELECT id, title, package_no, organization_path, district_name,
               procurement_nature, closing_at, first_seen_at
          FROM tenders
         WHERE status = 'live'
         ORDER BY first_seen_at DESC
         LIMIT %s
        """,
        (limit,),
    )
    return list(await cur.fetchall())


# ----------------------------------------------------- programmatic SEO

# Slug built in SQL so a URL can be matched without a lookup table. Bangla
# organization names collapse to their ASCII fragments here; the full name is
# always rendered on the page itself, so nothing is lost to the reader.
ORG_SLUG_SQL = "regexp_replace(lower(organization_path), '[^a-z0-9]+', '-', 'g')"

# §4: a landing page needs enough tenders to be worth indexing. Thin pages
# earn nothing and risk looking like doorway spam.
MIN_TENDERS_FOR_PAGE = 5


async def district_pages(conn: AsyncConnection) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"""
        SELECT district_name AS name, count(*) AS n
          FROM tenders
         WHERE district_name IS NOT NULL
         GROUP BY district_name
        HAVING count(*) >= {MIN_TENDERS_FOR_PAGE}
         ORDER BY n DESC
        """
    )
    return list(await cur.fetchall())


async def organization_pages(conn: AsyncConnection) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"""
        SELECT organization_path AS name,
               {ORG_SLUG_SQL} AS slug,
               count(*) AS n
          FROM tenders
         WHERE organization_path IS NOT NULL
         GROUP BY organization_path
        HAVING count(*) >= {MIN_TENDERS_FOR_PAGE}
         ORDER BY n DESC
        """
    )
    return list(await cur.fetchall())


async def organization_by_slug(
    conn: AsyncConnection, slug: str
) -> str | None:
    cur = await conn.execute(
        f"""
        SELECT organization_path AS name
          FROM tenders
         WHERE organization_path IS NOT NULL AND {ORG_SLUG_SQL} = %s
         LIMIT 1
        """,
        (slug,),
    )
    row = await cur.fetchone()
    return row["name"] if row else None


async def sitemap_entries(conn: AsyncConnection, limit: int = 40000) -> dict[str, Any]:
    """Everything worth indexing, newest tenders first."""
    cur = await conn.execute(
        """
        SELECT id, greatest(first_seen_at, last_seen_at) AS updated
          FROM tenders
         WHERE status = 'live'
         ORDER BY first_seen_at DESC
         LIMIT %s
        """,
        (limit,),
    )
    tenders = list(await cur.fetchall())
    return {
        "tenders": tenders,
        "districts": await district_pages(conn),
        "organizations": await organization_pages(conn),
    }
