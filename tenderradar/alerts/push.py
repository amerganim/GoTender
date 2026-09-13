"""Web Push notifications (§4 Phase 2).

Free and unlimited, which is precisely why this exists while SMS is a named
cost trap. On Android Chrome -- what this market carries -- it arrives like a
normal notification.

Push complements the digest rather than replacing it. The digest is the daily
summary; push is for the one case where a day's delay actually costs money: a
tender that matches strongly and closes soon.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from tenderradar.config import settings

log = logging.getLogger(__name__)

# A push payload has a hard size limit around 4KB once encrypted. Keep well
# clear of it: titles are truncated rather than risking a rejected send.
MAX_TITLE = 80
MAX_BODY = 160

# Retire a subscription after this many consecutive failures that were not
# already an explicit "gone" response.
MAX_FAILURES = 5


@dataclass(slots=True)
class PushReport:
    sent: int = 0
    retired: int = 0
    failed: int = 0

    def summary(self) -> str:
        return f"{self.sent} sent, {self.retired} retired, {self.failed} failed"


async def save_subscription(
    conn: AsyncConnection,
    user_id: int,
    subscription: dict[str, Any],
    user_agent: str | None = None,
) -> bool:
    """Store a browser subscription. Re-subscribing simply revives the row."""
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return False

    await conn.execute(
        """
        INSERT INTO push_subscriptions
            (user_id, endpoint, p256dh, auth, user_agent)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (endpoint) DO UPDATE
            SET user_id = EXCLUDED.user_id,
                p256dh = EXCLUDED.p256dh,
                auth = EXCLUDED.auth,
                user_agent = EXCLUDED.user_agent,
                retired_at = NULL,
                failure_count = 0,
                last_error = NULL
        """,
        (user_id, endpoint, keys["p256dh"], keys["auth"], (user_agent or "")[:300]),
    )
    return True


async def delete_subscription(conn: AsyncConnection, endpoint: str) -> None:
    await conn.execute(
        "DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,)
    )


async def active_subscriptions(
    conn: AsyncConnection, user_id: int
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT * FROM push_subscriptions
         WHERE user_id = %s AND retired_at IS NULL
        """,
        (user_id,),
    )
    return list(await cur.fetchall())


def _send_one(subscription: dict[str, Any], payload: dict[str, Any]) -> None:
    """Blocking send. Raises WebPushException on failure."""
    from pywebpush import webpush

    webpush(
        subscription_info={
            "endpoint": subscription["endpoint"],
            "keys": {
                "p256dh": subscription["p256dh"],
                "auth": subscription["auth"],
            },
        },
        data=json.dumps(payload),
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_subject},
        timeout=15,
    )


async def push_to_user(
    conn: AsyncConnection,
    user_id: int,
    *,
    title: str,
    body: str,
    url: str,
    tag: str | None = None,
) -> PushReport:
    """Send to every live subscription a user has."""
    report = PushReport()
    if not settings.push_enabled:
        log.warning("push skipped: VAPID keys are not configured")
        return report

    payload = {
        "title": title[:MAX_TITLE],
        "body": body[:MAX_BODY],
        "url": url,
        # Same tag replaces an earlier notification instead of stacking, so a
        # phone never shows five near-identical alerts.
        "tag": tag or "tenderradar",
    }

    for subscription in await active_subscriptions(conn, user_id):
        try:
            _send_one(subscription, payload)
        except Exception as exc:  # noqa: BLE001 - one dead endpoint must not stop the rest
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                # The browser told us this subscription is gone. Retiring it is
                # correct, not an error: the user cleared site data or
                # uninstalled. Retrying it forever would be the bug.
                await conn.execute(
                    "UPDATE push_subscriptions SET retired_at = now(), "
                    "last_error = %s WHERE id = %s",
                    (f"gone ({status})", subscription["id"]),
                )
                report.retired += 1
            else:
                await conn.execute(
                    """
                    UPDATE push_subscriptions
                       SET failure_count = failure_count + 1,
                           last_error = %s,
                           retired_at = CASE WHEN failure_count + 1 >= %s
                                             THEN now() ELSE retired_at END
                     WHERE id = %s
                    """,
                    (str(exc)[:500], MAX_FAILURES, subscription["id"]),
                )
                report.failed += 1
                log.warning("push failed for subscription %s: %s", subscription["id"], exc)
            continue

        await conn.execute(
            "UPDATE push_subscriptions SET last_success_at = now(), "
            "failure_count = 0, last_error = NULL WHERE id = %s",
            (subscription["id"],),
        )
        report.sent += 1

    return report


async def push_urgent_matches(
    conn: AsyncConnection, *, within_hours: int = 48, min_score: float = 0.0
) -> list[tuple[int, PushReport]]:
    """Push only matches that close soon enough for the digest to be too late.

    Anything closing further out belongs in tomorrow's email. Pushing every
    match would train people to swipe the notification away, and a dismissed
    channel is worth less than no channel.
    """
    cur = await conn.execute(
        """
        SELECT m.user_id, m.tender_id, m.score, t.title, t.closing_at
          FROM matches m
          JOIN tenders t ON t.id = m.tender_id
          JOIN users u ON u.id = m.user_id
         WHERE t.status = 'live'
           AND u.unsubscribed_at IS NULL
           AND t.closing_at BETWEEN now() AND now() + make_interval(hours => %s)
           AND m.score >= %s
           AND NOT EXISTS (
                 SELECT 1 FROM alerts a
                  WHERE a.user_id = m.user_id AND a.channel = 'push'
                    AND m.tender_id = ANY(a.tender_ids)
               )
         ORDER BY m.user_id, m.score DESC
        """,
        (within_hours, min_score),
    )
    rows = list(await cur.fetchall())

    by_user: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_user.setdefault(row["user_id"], []).append(row)

    base = settings.site_base_url.rstrip("/")
    reports = []
    for user_id, matches in by_user.items():
        top = matches[0]
        count = len(matches)
        title = (
            "Closing soon: 1 tender for you"
            if count == 1
            else f"Closing soon: {count} tenders for you"
        )
        report = await push_to_user(
            conn,
            user_id,
            title=title,
            body=top["title"] or "Tap to view",
            url=f"{base}/tenders/{top['tender_id']}",
            tag="closing-soon",
        )
        if report.sent:
            await conn.execute(
                """
                INSERT INTO alerts (user_id, channel, tender_ids, sent_at)
                VALUES (%s, 'push', %s, now())
                """,
                (user_id, [m["tender_id"] for m in matches]),
            )
        reports.append((user_id, report))
    return reports
