"""Layer 1 — hard filters in SQL (§7).

Cheap filters first. This runs before any embedding maths and eliminates the
large majority of candidates for almost no cost. Its job is not to rank; its
job is to guarantee we never surface a Sylhet tender to a Rajshahi-only
contractor, whatever the semantic similarity says.

Every clause here is a statement about eligibility or intent that the user made
explicitly. Nothing is inferred, because a false negative at this layer is
invisible -- the user simply never hears about a tender they could have won.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection


@dataclass(slots=True)
class Layer1Profile:
    """The hard constraints a user stated. Empty means "no constraint"."""

    user_id: int | None = None
    district_names: list[str] = field(default_factory=list)
    procurement_natures: list[str] = field(default_factory=list)
    procurement_methods: list[str] = field(default_factory=list)
    # e-GP publishes no estimated value; these bound tender_security, which
    # runs about 2-2.5% of the estimate.
    min_security: Decimal | None = None
    max_security: Decimal | None = None
    # Don't surface something closing sooner than the user could realistically
    # prepare a bid for.
    min_hours_to_closing: int = 24
    max_days_to_closing: int = 90

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Layer1Profile:
        return cls(
            user_id=row.get("user_id"),
            district_names=list(row.get("district_names") or []),
            procurement_natures=list(row.get("procurement_natures") or []),
            procurement_methods=list(row.get("procurement_methods") or []),
            min_security=row.get("min_security"),
            max_security=row.get("max_security"),
        )


def build_clauses(profile: Layer1Profile) -> tuple[list[str], dict[str, Any]]:
    """SQL fragments for a profile. Exposed separately so it can be tested."""
    clauses = ["t.status = 'live'"]
    params: dict[str, Any] = {}

    # Never alert on something already closed, nor on something closing so
    # soon the user cannot act. Both are noise that trains people to ignore us.
    clauses.append(
        "t.closing_at >= now() + make_interval(hours => %(min_hours)s)"
    )
    params["min_hours"] = profile.min_hours_to_closing
    clauses.append(
        "t.closing_at <= now() + make_interval(days => %(max_days)s)"
    )
    params["max_days"] = profile.max_days_to_closing

    if profile.district_names:
        # Tenders with no district yet are kept deliberately: district arrives
        # with the detail page, and dropping them would hide brand new tenders
        # -- the freshest ones, which are the entire product claim.
        clauses.append(
            "(t.district_name = ANY(%(districts)s) OR t.district_name IS NULL)"
        )
        params["districts"] = profile.district_names

    if profile.procurement_natures:
        clauses.append("t.procurement_nature = ANY(%(natures)s)")
        params["natures"] = profile.procurement_natures

    if profile.procurement_methods:
        clauses.append("t.procurement_method = ANY(%(methods)s)")
        params["methods"] = profile.procurement_methods

    # Value bounds only apply where we actually know the security. A NULL is
    # ignorance, not a zero, and treating it as out-of-range would silently
    # drop every tender whose detail page we have not fetched yet.
    if profile.min_security is not None:
        clauses.append(
            "(t.tender_security IS NULL OR t.tender_security >= %(min_sec)s)"
        )
        params["min_sec"] = profile.min_security
    if profile.max_security is not None:
        clauses.append(
            "(t.tender_security IS NULL OR t.tender_security <= %(max_sec)s)"
        )
        params["max_sec"] = profile.max_security

    return clauses, params


async def candidates(
    conn: AsyncConnection, profile: Layer1Profile, *, limit: int = 500
) -> list[dict[str, Any]]:
    """Tenders that pass every hard constraint, newest first."""
    clauses, params = build_clauses(profile)
    params["limit"] = limit

    exclude = ""
    if profile.user_id is not None:
        # Never re-surface a tender the user already saw or judged.
        exclude = """
          AND NOT EXISTS (SELECT 1 FROM matches m
                           WHERE m.user_id = %(user_id)s AND m.tender_id = t.id)
          AND NOT EXISTS (SELECT 1 FROM feedback f
                           WHERE f.user_id = %(user_id)s AND f.tender_id = t.id)
        """
        params["user_id"] = profile.user_id

    cur = await conn.execute(
        f"""
        SELECT t.id, t.title, t.description, t.package_no, t.district_name,
               t.organization_path, t.procurement_nature, t.procurement_method,
               t.tender_security, t.closing_at, t.published_at
          FROM tenders t
         WHERE {" AND ".join(clauses)}
           {exclude}
         ORDER BY t.published_at DESC NULLS LAST
         LIMIT %(limit)s
        """,
        params,
    )
    return list(await cur.fetchall())


async def count_candidates(conn: AsyncConnection, profile: Layer1Profile) -> int:
    """How much Layer 1 actually eliminated -- worth watching (§7)."""
    clauses, params = build_clauses(profile)
    cur = await conn.execute(
        f"SELECT count(*) AS n FROM tenders t WHERE {' AND '.join(clauses)}",
        params,
    )
    row = await cur.fetchone()
    return int(row["n"]) if row else 0
