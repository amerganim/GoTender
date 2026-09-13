"""Configuration. Secrets come from env vars only (§9)."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = ""
    storage_dir: Path = Path("storage/raw")

    # Rule §8.1: identify the bot with a real contact email.
    crawler_user_agent: str = "TenderRadarBot/0.1"
    crawler_contact_email: str = ""

    # Rule §8.7: never retry aggressively against a government host.
    crawler_request_delay_sec: float = 1.0
    crawler_max_concurrency: int = 2
    crawler_timeout_sec: float = 60.0

    # Signs one-click feedback links in digest emails. Must be secret and
    # stable: rotating it invalidates every link in every email already sent.
    alert_token_secret: str = ""
    # Absolute base for links in emails; relative URLs do not work in a mail
    # client. Set to the real domain before any digest goes out.
    site_base_url: str = "http://127.0.0.1:8020"
    # Where digests go in dev. "console" prints, "file" writes .eml, "smtp" sends.
    email_backend: str = "file"
    email_from: str = "TenderRadar <alerts@example.com>"
    email_outbox_dir: Path = Path("storage/outbox")
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    # Web Push (VAPID). Free and unlimited, unlike SMS (cost trap #1).
    # Rotating these invalidates every existing browser subscription.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:you@example.com"

    # Only these addresses may open the admin dashboard.
    admin_emails: str = ""

    log_level: str = "INFO"

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)

    @property
    def user_agent(self) -> str:
        """User-Agent with the real contact email appended, per §8.1.

        crawler_contact_email is authoritative: any mailto already present in
        crawler_user_agent is stripped first. Otherwise a placeholder left in
        the base string silently wins and the bot identifies itself to a
        government host with an address nobody reads, which is worse than
        giving no address at all.
        """
        base = re.sub(r"\s*\(\+?mailto:[^)]*\)", "", self.crawler_user_agent).strip()
        if self.crawler_contact_email:
            return f"{base} (+mailto:{self.crawler_contact_email})"
        return base


settings = Settings()
