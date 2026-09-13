"""Daily digest (§4 Phase 2).

One email per user per day carrying their matches, never one email per tender.
Cost trap #1 is about SMS, but the same arithmetic governs attention: five
separate emails a day is how a useful service becomes a filtered one.

Every tender in every digest carries thumbs up and thumbs down. §4 requires
this in the very first email, and it is not a nicety -- match precision is
thumbs-up divided by alerts sent, and it is the gate the whole phase is judged
on. Without feedback in the first send there is nothing to measure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from psycopg import AsyncConnection

from tenderradar.alerts import tokens
from tenderradar.config import settings

log = logging.getLogger(__name__)

# Enough to be worth opening, few enough to read on a phone over breakfast.
MAX_TENDERS_PER_DIGEST = 10


@dataclass(slots=True)
class DigestItem:
    tender: dict[str, Any]
    score: float
    matched_by: str
    similarity: float | None
    up_url: str
    down_url: str
    tender_url: str


@dataclass(slots=True)
class Digest:
    user: dict[str, Any]
    items: list[DigestItem] = field(default_factory=list)
    alert_id: int | None = None
    open_pixel_url: str = ""
    unsubscribe_url: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def subject(self) -> str:
        count = len(self.items)
        if count == 1:
            return f"1 new tender for {self.user.get('company_name') or 'you'}"
        return f"{count} new tenders for {self.user.get('company_name') or 'you'}"


async def unsent_matches(
    conn: AsyncConnection, user_id: int, limit: int = MAX_TENDERS_PER_DIGEST
) -> list[dict[str, Any]]:
    """Best matches this user has not already been sent.

    Re-sending a tender someone already saw is the fastest way to train them
    to ignore the digest, so anything in a previous alert is excluded even if
    it still scores well.
    """
    cur = await conn.execute(
        """
        SELECT m.tender_id, m.score, m.layer2_score, m.reasons,
               t.title, t.package_no, t.organization_path, t.district_name,
               t.procurement_nature, t.procurement_method, t.tender_security,
               t.closing_at, t.detail_url
          FROM matches m
          JOIN tenders t ON t.id = m.tender_id
         WHERE m.user_id = %(user_id)s
           AND t.status = 'live'
           AND t.closing_at > now()
           AND NOT EXISTS (
                 SELECT 1 FROM alerts a
                  WHERE a.user_id = %(user_id)s
                    AND a.sent_at IS NOT NULL
                    AND m.tender_id = ANY(a.tender_ids)
               )
         ORDER BY m.score DESC, t.closing_at ASC
         LIMIT %(limit)s
        """,
        {"user_id": user_id, "limit": limit},
    )
    return list(await cur.fetchall())


async def build_digest(
    conn: AsyncConnection, user: dict[str, Any]
) -> Digest:
    """Assemble a digest without sending or recording anything."""
    rows = await unsent_matches(conn, user["id"])
    digest = Digest(user=user)
    if not rows:
        return digest

    base = settings.site_base_url.rstrip("/")
    for row in rows:
        reasons = row.get("reasons") or {}
        digest.items.append(
            DigestItem(
                tender=row,
                score=float(row["score"]),
                matched_by=reasons.get("matched_by", "semantic"),
                similarity=row.get("layer2_score"),
                up_url=f"{base}/f/{tokens.feedback_token(user['id'], row['tender_id'], 'up')}",
                down_url=f"{base}/f/{tokens.feedback_token(user['id'], row['tender_id'], 'down')}",
                tender_url=f"{base}/tenders/{row['tender_id']}",
            )
        )
    digest.unsubscribe_url = (
        f"{base}/unsubscribe/{tokens.unsubscribe_token(user['id'])}"
    )
    return digest


async def record_alert(
    conn: AsyncConnection, digest: Digest, *, channel: str = "email"
) -> int:
    """Create the alerts row BEFORE sending, so a send can never go untracked.

    An alert recorded but not sent is a visible error. An email sent with no
    row is invisible, and would quietly corrupt both the open rate and the
    precision denominator.
    """
    cur = await conn.execute(
        """
        INSERT INTO alerts (user_id, channel, tender_ids)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (
            digest.user["id"],
            channel,
            [item.tender["tender_id"] for item in digest.items],
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    digest.alert_id = int(row["id"])
    base = settings.site_base_url.rstrip("/")
    digest.open_pixel_url = f"{base}/o/{tokens.open_token(digest.alert_id)}.gif"
    return digest.alert_id


async def mark_sent(conn: AsyncConnection, alert_id: int) -> None:
    await conn.execute(
        "UPDATE alerts SET sent_at = now() WHERE id = %s", (alert_id,)
    )


async def mark_failed(conn: AsyncConnection, alert_id: int, error: str) -> None:
    await conn.execute(
        "UPDATE alerts SET error = %s WHERE id = %s", (error[:1000], alert_id)
    )


async def digest_recipients(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Users who should get a digest: verified, subscribed, with an email."""
    cur = await conn.execute(
        """
        SELECT id, phone, email, name, company_name
          FROM users
         WHERE unsubscribed_at IS NULL
           AND email IS NOT NULL
           AND verified_at IS NOT NULL
         ORDER BY id
        """
    )
    return list(await cur.fetchall())
