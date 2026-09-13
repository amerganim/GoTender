"""Render and send digests.

Order matters: the alerts row is written before the send, so a message can
never leave without a record. An alert recorded but not sent shows up as an
error; an email sent with no row would be invisible and would corrupt both the
open rate and the denominator of match precision.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from psycopg import AsyncConnection

from tenderradar.alerts import digest as digest_mod
from tenderradar.alerts.digest import Digest
from tenderradar.alerts.sender import EmailBackend, build_message, get_backend
from tenderradar.config import settings

log = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "web" / "templates"


def _environment() -> Environment:
    from tenderradar.web.app import countdown, dhaka, taka

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["taka"] = taka
    env.filters["countdown"] = countdown
    env.filters["dhaka"] = dhaka
    return env


def render(digest: Digest) -> tuple[str, str]:
    env = _environment()
    context = {"digest": digest, "base_url": settings.site_base_url.rstrip("/")}
    html = env.get_template("email/digest.html").render(**context)
    text = env.get_template("email/digest.txt").render(**context)
    return html, text


@dataclass(slots=True)
class SendReport:
    user_id: int
    tenders: int = 0
    sent: bool = False
    skipped_reason: str | None = None
    error: str | None = None

    def summary(self) -> str:
        if self.skipped_reason:
            return f"user {self.user_id}: skipped ({self.skipped_reason})"
        if self.error:
            return f"user {self.user_id}: FAILED - {self.error}"
        return f"user {self.user_id}: sent {self.tenders} tenders"


async def send_digest(
    conn: AsyncConnection,
    user: dict,
    *,
    backend: EmailBackend | None = None,
    dry_run: bool = False,
) -> SendReport:
    report = SendReport(user_id=user["id"])

    built = await digest_mod.build_digest(conn, user)
    if built.is_empty:
        # Silence is correct. An empty digest teaches people the mail is
        # worthless, and nothing erodes an open rate faster.
        report.skipped_reason = "no unsent matches"
        return report

    report.tenders = len(built.items)
    if dry_run:
        report.skipped_reason = "dry run"
        return report

    alert_id = await digest_mod.record_alert(conn, built)
    await conn.commit()

    html, text = render(built)
    message = build_message(
        to=user["email"],
        subject=built.subject,
        html=html,
        text=text,
        unsubscribe_url=built.unsubscribe_url,
    )

    try:
        (backend or get_backend()).send(message)
    except Exception as exc:  # noqa: BLE001 - one bad address must not stop the run
        report.error = f"{type(exc).__name__}: {exc}"
        await digest_mod.mark_failed(conn, alert_id, report.error)
        await conn.commit()
        log.exception("digest failed for user %s", user["id"])
        return report

    await digest_mod.mark_sent(conn, alert_id)
    await conn.commit()
    report.sent = True
    return report


async def send_all(
    conn: AsyncConnection, *, dry_run: bool = False
) -> list[SendReport]:
    backend = None if dry_run else get_backend()
    reports = []
    for user in await digest_mod.digest_recipients(conn):
        report = await send_digest(conn, user, backend=backend, dry_run=dry_run)
        reports.append(report)
        log.info("%s", report.summary())
    return reports
