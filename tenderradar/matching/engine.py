"""Matching engine (§7): Layer 1 filters, Layer 2 ranks, results persist.

Layer 3 (eligibility) is Phase 5 and deliberately absent.

Match precision -- thumbs-up divided by alerts sent -- is the metric this whole
file exists to move. Below 30% nothing else in the project matters, so results
are stored with their reasons attached: a user who can see why a tender reached
them gives far more useful feedback than one who cannot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from tenderradar.matching import layer1, layer2
from tenderradar.matching.embeddings import profile_text

log = logging.getLogger(__name__)


@dataclass(slots=True)
class MatchReport:
    user_id: int
    candidates: int = 0
    ranked: int = 0
    stored: int = 0
    skipped_no_profile: bool = False

    def summary(self) -> str:
        if self.skipped_no_profile:
            return f"user {self.user_id}: skipped, no usable profile"
        return (
            f"user {self.user_id}: {self.candidates} candidates -> "
            f"{self.ranked} ranked -> {self.stored} stored"
        )


async def load_profile(
    conn: AsyncConnection, user_id: int
) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT * FROM user_profiles WHERE user_id = %s", (user_id,)
    )
    return await cur.fetchone()


async def match_user(
    conn: AsyncConnection, user_id: int, *, limit: int = 50
) -> MatchReport:
    report = MatchReport(user_id=user_id)

    profile_row = await load_profile(conn, user_id)
    if profile_row is None:
        report.skipped_no_profile = True
        return report

    text = profile_text(profile_row)
    if not text:
        # Without a description there is nothing to match semantically. Layer 1
        # alone would return the whole live pool, which is the irrelevant-alert
        # problem this product exists to solve.
        report.skipped_no_profile = True
        return report

    hard = layer1.Layer1Profile.from_row({**profile_row, "user_id": user_id})
    candidates = await layer1.candidates(conn, hard)
    report.candidates = len(candidates)
    if not candidates:
        return report

    scored = await layer2.rank(
        conn, user_id, text, [row["id"] for row in candidates], limit=limit
    )
    report.ranked = len(scored)

    for item in scored:
        await conn.execute(
            """
            INSERT INTO matches
                (user_id, tender_id, score, layer1_pass, layer2_score, reasons)
            VALUES (%s, %s, %s, TRUE, %s, %s)
            ON CONFLICT (user_id, tender_id) DO UPDATE
                SET score = EXCLUDED.score,
                    layer2_score = EXCLUDED.layer2_score,
                    reasons = EXCLUDED.reasons
            """,
            (
                user_id,
                item.tender_id,
                item.score,
                item.vector_similarity,
                Jsonb(item.reasons),
            ),
        )
        report.stored += 1

    return report


async def match_all_users(conn: AsyncConnection, *, limit: int = 50) -> list[MatchReport]:
    cur = await conn.execute(
        """
        SELECT u.id FROM users u
         WHERE u.unsubscribed_at IS NULL
         ORDER BY u.id
        """
    )
    user_ids = [row["id"] for row in await cur.fetchall()]

    reports = []
    for user_id in user_ids:
        report = await match_user(conn, user_id, limit=limit)
        await conn.commit()
        reports.append(report)
        log.info("%s", report.summary())
    return reports


async def match_precision(conn: AsyncConnection) -> dict[str, Any]:
    """The primary metric (§7, §10). Thumbs-up divided by feedback given."""
    cur = await conn.execute(
        """
        SELECT count(*) FILTER (WHERE verdict = 'up')   AS up,
               count(*) FILTER (WHERE verdict = 'down') AS down,
               count(*)                                  AS total
          FROM feedback
        """
    )
    row = await cur.fetchone() or {}
    total = int(row.get("total") or 0)
    up = int(row.get("up") or 0)
    return {
        "thumbs_up": up,
        "thumbs_down": int(row.get("down") or 0),
        "rated": total,
        # Undefined rather than zero until someone actually rates something:
        # reporting 0% on no data would look like failure instead of silence.
        "precision": (up / total) if total else None,
        "target": 0.30,
    }
