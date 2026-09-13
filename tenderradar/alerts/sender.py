"""Email delivery.

Three backends, chosen by EMAIL_BACKEND:

* ``file``    writes a .eml into storage/outbox. The default, so the whole
              digest pipeline is testable end to end with no mail account and
              no risk of a stray send to a real contractor during development.
* ``console`` prints a summary.
* ``smtp``    actually sends. Brevo and Resend both have free tiers (§5).

Nothing here retries. A failed digest is recorded on the alerts row and picked
up by the next run; hammering a mail provider on failure is how a free tier
becomes a blocked sender.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from tenderradar.config import settings

log = logging.getLogger(__name__)


def build_message(
    *,
    to: str,
    subject: str,
    html: str,
    text: str,
    unsubscribe_url: str = "",
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="tenderradar.local")

    if unsubscribe_url:
        # Gmail and Outlook surface a native unsubscribe control when these
        # are present, which keeps complaints away from the spam button --
        # and spam reports are what get a young sending domain blocked.
        message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # Plain text first: a client that cannot render HTML shows this, and a
    # digest with no text part looks like spam to most filters.
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


class EmailBackend:
    def send(self, message: EmailMessage) -> None:  # pragma: no cover - iface
        raise NotImplementedError


class FileBackend(EmailBackend):
    """Write the message to disk instead of sending it."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or settings.email_outbox_dir)

    def send(self, message: EmailMessage) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        # Message-ID is unique per message, so digests never overwrite.
        stem = message["Message-ID"].strip("<>").split("@")[0][:40]
        path = self.directory / f"{stem}.eml"
        path.write_bytes(bytes(message))
        log.info("digest written to %s", path)


class ConsoleBackend(EmailBackend):
    def send(self, message: EmailMessage) -> None:
        print(f"--- email to {message['To']}: {message['Subject']}")


class SmtpBackend(EmailBackend):
    def send(self, message: EmailMessage) -> None:
        if not settings.smtp_host:
            raise RuntimeError("SMTP_HOST is not set but EMAIL_BACKEND=smtp")
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
        log.info("digest sent to %s", message["To"])


def get_backend(name: str | None = None) -> EmailBackend:
    backend = (name or settings.email_backend).lower()
    if backend == "smtp":
        return SmtpBackend()
    if backend == "console":
        return ConsoleBackend()
    if backend == "file":
        return FileBackend()
    raise ValueError(f"unknown EMAIL_BACKEND {backend!r}")
