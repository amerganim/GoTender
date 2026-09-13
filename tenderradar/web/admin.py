"""Admin dashboard (§4 Phase 2) — the Gate 2 scoreboard.

Shows only the numbers §10 says to track. Site visits and signup counts are
named there as vanity metrics, so signups appear as context for the ratios and
never as the headline.

The three that decide Phase 2:
  * week-3 digest open rate >= 40%   (everyone opens week 1; week 3 is truth)
  * match precision >= 30%           (thumbs-up over rated)
  * crawl freshness < 30 min         (the product claim)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tenderradar.config import settings
from tenderradar.db.pool import connection
from tenderradar.web import auth, queries

router = APIRouter()


def _templates():
    from tenderradar.web.app import TEMPLATES

    return TEMPLATES


async def _require_admin(conn, request: Request) -> dict[str, Any] | None:
    user = await auth.current_user(conn, request)
    if user is None or not user.get("email"):
        return None
    if user["email"].strip().lower() not in settings.admin_email_set:
        return None
    return user


async def gather_metrics(conn) -> dict[str, Any]:
    async def one(sql: str, params: tuple = ()) -> dict[str, Any]:
        cur = await conn.execute(sql, params)
        return await cur.fetchone() or {}

    users = await one(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE verified_at IS NOT NULL) AS verified,
               count(*) FILTER (WHERE unsubscribed_at IS NOT NULL) AS unsubscribed,
               count(*) FILTER (WHERE created_at > now() - interval '7 days') AS last_7d
          FROM users
        """
    )

    # Open rate overall and, separately, for digests sent to accounts at least
    # three weeks old. Week 1 opens are curiosity; week 3 is the real signal.
    opens = await one(
        """
        SELECT count(*) FILTER (WHERE a.sent_at IS NOT NULL) AS sent,
               count(*) FILTER (WHERE a.opened_at IS NOT NULL) AS opened,
               count(*) FILTER (WHERE a.sent_at IS NOT NULL
                                 AND u.created_at < now() - interval '21 days')
                    AS sent_week3,
               count(*) FILTER (WHERE a.opened_at IS NOT NULL
                                 AND u.created_at < now() - interval '21 days')
                    AS opened_week3
          FROM alerts a JOIN users u ON u.id = a.user_id
         WHERE a.channel = 'email'
        """
    )

    feedback = await one(
        """
        SELECT count(*) FILTER (WHERE verdict = 'up') AS up,
               count(*) FILTER (WHERE verdict = 'down') AS down,
               count(*) AS rated
          FROM feedback
        """
    )

    # Precision denominator note: §7 defines it as thumbs-up over alerts sent,
    # but an unrated alert is silence, not a rejection. Both are shown, because
    # conflating them would flatter or damn the engine depending on which is
    # quoted.
    alerts_sent = int(opens.get("sent") or 0)
    rated = int(feedback.get("rated") or 0)
    up = int(feedback.get("up") or 0)

    matches = await one(
        """
        SELECT count(*) AS total,
               count(DISTINCT user_id) AS users_with_matches,
               count(*) FILTER (WHERE reasons->>'matched_by' = 'both') AS both,
               count(*) FILTER (WHERE reasons->>'matched_by' = 'semantic') AS semantic,
               count(*) FILTER (WHERE reasons->>'matched_by' = 'keyword') AS keyword
          FROM matches
        """
    )

    push = await one(
        """
        SELECT count(*) FILTER (WHERE retired_at IS NULL) AS active,
               count(*) FILTER (WHERE retired_at IS NOT NULL) AS retired
          FROM push_subscriptions
        """
    )

    crawl = await one(
        """
        SELECT max(last_success_at) AS last_success,
               (SELECT count(*) FROM crawl_runs
                 WHERE yield_anomaly AND started_at > now() - interval '7 days')
                    AS anomalies_7d,
               (SELECT count(*) FROM parse_failures WHERE resolved_at IS NULL)
                    AS unresolved_failures
          FROM sources
        """
    )

    stats = await queries.site_stats(conn)

    def ratio(numerator: int, denominator: int) -> float | None:
        return (numerator / denominator) if denominator else None

    return {
        "users": users,
        "alerts_sent": alerts_sent,
        "opened": int(opens.get("opened") or 0),
        "open_rate": ratio(int(opens.get("opened") or 0), alerts_sent),
        "open_rate_week3": ratio(
            int(opens.get("opened_week3") or 0), int(opens.get("sent_week3") or 0)
        ),
        "feedback": feedback,
        "precision_of_rated": ratio(up, rated),
        "precision_of_sent": ratio(up, alerts_sent),
        "matches": matches,
        "push": push,
        "crawl": crawl,
        "stats": stats,
    }


@router.get("/admin", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    async with connection() as conn:
        user = await _require_admin(conn, request)
        if user is None:
            # Same answer for "not signed in" and "not an admin", so the
            # existence of the dashboard reveals nothing.
            return RedirectResponse("/signin", status_code=303)
        metrics = await gather_metrics(conn)

    return _templates().TemplateResponse(
        request, "admin.html",
        {"m": metrics, "user": user, "stats": metrics["stats"]},
    )
