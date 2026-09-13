"""Signed tokens for one-click actions from an email.

Nobody logs in from an email, so a feedback link has to authenticate itself.
Each token is an HMAC over exactly the action it permits, which means a token
for "user 7, tender 42, thumbs up" cannot be edited into anything else.

Tokens deliberately carry no expiry. A contractor who opens last week's digest
and marks a tender irrelevant is giving us the signal we most need, and
refusing it to enforce a lifetime we have no reason to want would be silly.
"""

from __future__ import annotations

import base64
import hmac
from hashlib import sha256

from tenderradar.config import settings

VERDICTS = ("up", "down")


def _secret() -> bytes:
    secret = settings.alert_token_secret
    if not secret:
        raise RuntimeError(
            "ALERT_TOKEN_SECRET is not set. Generate one with "
            "`python -c \"import secrets; print(secrets.token_urlsafe(32))\"` "
            "and put it in .env."
        )
    return secret.encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def sign(payload: str) -> str:
    digest = hmac.new(_secret(), payload.encode("utf-8"), sha256).digest()
    # 16 bytes is ample: forging one requires 2^64 work for the privilege of
    # casting a single vote on one tender.
    return _b64(digest[:16])


def make_token(kind: str, *parts: object) -> str:
    """Build `kind:part:part:signature`."""
    payload = ":".join([kind, *(str(p) for p in parts)])
    return f"{payload}:{sign(payload)}"


def verify_token(token: str, expected_kind: str) -> tuple[str, ...] | None:
    """Return the payload parts, or None if the token is not authentic."""
    if not token or token.count(":") < 2:
        return None
    payload, _, signature = token.rpartition(":")
    parts = payload.split(":")
    if parts[0] != expected_kind:
        return None
    # Constant-time: a timing oracle here would let someone forge votes.
    if not hmac.compare_digest(signature, sign(payload)):
        return None
    return tuple(parts[1:])


def feedback_token(user_id: int, tender_id: int, verdict: str) -> str:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    return make_token("fb", user_id, tender_id, verdict)


def parse_feedback_token(token: str) -> tuple[int, int, str] | None:
    parts = verify_token(token, "fb")
    if parts is None or len(parts) != 3:
        return None
    user_id, tender_id, verdict = parts
    if verdict not in VERDICTS:
        return None
    try:
        return int(user_id), int(tender_id), verdict
    except ValueError:
        return None


def open_token(alert_id: int) -> str:
    return make_token("op", alert_id)


def parse_open_token(token: str) -> int | None:
    parts = verify_token(token, "op")
    if parts is None or len(parts) != 1:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def unsubscribe_token(user_id: int) -> str:
    return make_token("un", user_id)


def parse_unsubscribe_token(token: str) -> int | None:
    parts = verify_token(token, "un")
    if parts is None or len(parts) != 1:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None
