"""Sessions and sign-in.

No passwords. A contractor gets a signed link by email and clicking it signs
them in.

Three reasons, in order of weight. §4 calls this market phone-first, but SMS
costs money and §3 forbids spending before Gate 3 proves anyone pays, so the
phone is the identity while the free channel does the verifying. Passwords
would mean storing hashes, building reset flows and handling the reset mail
anyway -- the same email round trip, with a credential breach added. And the
digest already goes by email, so an address that cannot receive mail is a dead
account regardless.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request, Response
from psycopg import AsyncConnection

from tenderradar.alerts import tokens

SESSION_COOKIE = "tr_session"
# Long-lived on purpose: a contractor checking tenders weekly should not be
# made to re-authenticate, and the cookie grants nothing but their own profile.
SESSION_MAX_AGE = 60 * 60 * 24 * 180

# e-GP users are Bangladeshi; accept 01XXXXXXXXX and +8801XXXXXXXXX alike.
_PHONE_RE = re.compile(r"^(?:\+?880|0)1[3-9]\d{8}$")

# Measured, not guessed: two-word profiles ranked unrelated tenders top, while
# full sentences reached 70% precision@10 (§14). Refusing a too-short
# description is cheaper than sending months of bad matches.
MIN_DESCRIPTION_CHARS = 60


def normalize_phone(value: str) -> str | None:
    """Canonicalize to +8801XXXXXXXXX, or None if it is not a BD mobile."""
    cleaned = re.sub(r"[\s\-()]", "", value or "")
    if not _PHONE_RE.match(cleaned):
        return None
    digits = cleaned.lstrip("+")
    if digits.startswith("880"):
        digits = digits[3:]
    return "+880" + digits.lstrip("0")


def valid_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", (value or "").strip()))


def login_token(user_id: int) -> str:
    return tokens.make_token("li", user_id)


def parse_login_token(token: str) -> int | None:
    parts = tokens.verify_token(token, "li")
    if parts is None or len(parts) != 1:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def set_session(response: Response, user_id: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        tokens.make_token("se", user_id),
        max_age=SESSION_MAX_AGE,
        httponly=True,      # not readable from JavaScript
        samesite="lax",     # survives the click in from an email link
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def session_user_id(request: Request) -> int | None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    parts = tokens.verify_token(cookie, "se")
    if parts is None or len(parts) != 1:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


async def current_user(
    conn: AsyncConnection, request: Request
) -> dict[str, Any] | None:
    user_id = session_user_id(request)
    if user_id is None:
        return None
    cur = await conn.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    return await cur.fetchone()
