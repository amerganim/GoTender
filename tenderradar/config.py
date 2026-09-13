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

    log_level: str = "INFO"

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
