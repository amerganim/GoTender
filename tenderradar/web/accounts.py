"""Signup, sign-in and profile editing (§4 Phase 2).

Kept out of app.py because this is where the product stops being a directory
and starts being a service: the profile written here is the entire input to
matching, and its quality decides whether Gate 2 is reachable.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tenderradar.alerts.sender import build_message, get_backend
from tenderradar.config import settings
from tenderradar.db.pool import connection
from tenderradar.web import auth, queries

log = logging.getLogger(__name__)

router = APIRouter()

NATURES = ("goods", "works", "services")


def _templates():
    from tenderradar.web.app import TEMPLATES

    return TEMPLATES


async def _render(
    request: Request, name: str, context: dict[str, Any], status: int = 200
) -> Response:
    async with connection() as conn:
        context.setdefault("stats", await queries.site_stats(conn))
        context.setdefault("user", await auth.current_user(conn, request))
    return _templates().TemplateResponse(request, name, context, status_code=status)


def _send_login_email(user: dict[str, Any], *, new_account: bool) -> None:
    base = settings.site_base_url.rstrip("/")
    link = f"{base}/auth/{auth.login_token(user['id'])}"
    verb = "Confirm your email" if new_account else "Sign in"
    text = (
        f"{verb} for TenderRadar:\n\n{link}\n\n"
        "This link signs you in. If you did not ask for it, ignore this email.\n"
    )
    html = (
        f"<p>{verb} for TenderRadar:</p>"
        f'<p><a href="{link}" style="display:inline-block;background:#0b6b5e;'
        f'color:#fff;padding:11px 22px;border-radius:8px;text-decoration:none">'
        f"{verb}</a></p>"
        f'<p style="color:#5d6775;font-size:13px">'
        f"If you did not ask for this, ignore this email.</p>"
    )
    message = build_message(
        to=user["email"],
        subject=f"{verb} — TenderRadar",
        html=html,
        text=text,
    )
    get_backend().send(message)


# ------------------------------------------------------------------ signup


@router.get("/signup", response_class=HTMLResponse)
async def signup_form(request: Request) -> Response:
    return await _render(request, "signup.html", {"values": {}, "errors": {}})


@router.post("/signup", response_class=HTMLResponse)
async def signup_submit(
    request: Request,
    phone: str = Form(""),
    email: str = Form(""),
    company_name: str = Form(""),
    business_description: str = Form(""),
    districts: list[str] = Form(default=[]),
    natures: list[str] = Form(default=[]),
    min_security: str = Form(""),
    max_security: str = Form(""),
) -> Response:
    values = {
        "phone": phone, "email": email, "company_name": company_name,
        "business_description": business_description,
        "districts": districts, "natures": natures,
        "min_security": min_security, "max_security": max_security,
    }
    errors: dict[str, str] = {}

    normalized = auth.normalize_phone(phone)
    if normalized is None:
        errors["phone"] = "Enter a Bangladeshi mobile number, e.g. 01712345678."
    if not auth.valid_email(email):
        errors["email"] = "We send your tender digest here, so it must be valid."

    description = (business_description or "").strip()
    if len(description) < auth.MIN_DESCRIPTION_CHARS:
        # Not arbitrary: short descriptions measurably retrieve the wrong
        # tenders (§14). Refusing one now beats months of bad matches.
        errors["business_description"] = (
            f"Please write at least {auth.MIN_DESCRIPTION_CHARS} characters. "
            "A sentence or two about what you actually build or supply gives "
            "far better matches than a few keywords. Bangla is fine."
        )

    def money(raw: str) -> float | None:
        try:
            return float(raw.replace(",", "")) if raw.strip() else None
        except ValueError:
            return None

    low, high = money(min_security), money(max_security)
    if low is not None and high is not None and low > high:
        errors["max_security"] = "Maximum must be larger than minimum."

    if errors:
        return await _render(
            request, "signup.html", {"values": values, "errors": errors}, status=400
        )

    async with connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO users (phone, email, company_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (phone) DO UPDATE
                SET email = EXCLUDED.email,
                    company_name = EXCLUDED.company_name
            RETURNING *
            """,
            (normalized, email.strip(), company_name.strip() or None),
        )
        user = await cur.fetchone()
        assert user is not None

        await conn.execute(
            """
            INSERT INTO user_profiles
                (user_id, business_description, district_names,
                 procurement_natures, min_security, max_security, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (user_id) DO UPDATE
                SET business_description = EXCLUDED.business_description,
                    district_names = EXCLUDED.district_names,
                    procurement_natures = EXCLUDED.procurement_natures,
                    min_security = EXCLUDED.min_security,
                    max_security = EXCLUDED.max_security,
                    updated_at = now()
            """,
            (
                user["id"], description, districts,
                [n for n in natures if n in NATURES], low, high,
            ),
        )
        await conn.commit()

    try:
        _send_login_email(user, new_account=True)
    except Exception:  # noqa: BLE001 - the account exists; mail can be retried
        log.exception("could not send confirmation to user %s", user["id"])

    return await _render(request, "check_email.html", {"email": email})


# ------------------------------------------------------------------ signin


@router.get("/signin", response_class=HTMLResponse)
async def signin_form(request: Request) -> Response:
    return await _render(request, "signin.html", {"errors": {}, "values": {}})


@router.post("/signin", response_class=HTMLResponse)
async def signin_submit(request: Request, email: str = Form("")) -> Response:
    async with connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM users WHERE lower(email) = lower(%s)", (email.strip(),)
        )
        user = await cur.fetchone()

    if user is not None:
        try:
            _send_login_email(user, new_account=False)
        except Exception:  # noqa: BLE001
            log.exception("could not send sign-in link")

    # Same response either way: revealing which addresses have accounts would
    # leak the customer list to anyone who asks.
    return await _render(request, "check_email.html", {"email": email})


@router.get("/auth/{token}")
async def auth_link(request: Request, token: str) -> Response:
    user_id = auth.parse_login_token(token)
    if user_id is None:
        return await _render(
            request, "signin.html",
            {"errors": {"token": "That sign-in link is not valid. Ask for a new one."},
             "values": {}},
            status=400,
        )

    async with connection() as conn:
        await conn.execute(
            "UPDATE users SET verified_at = COALESCE(verified_at, now()), "
            "last_seen_at = now(), unsubscribed_at = NULL WHERE id = %s",
            (user_id,),
        )
        await conn.commit()

    response = RedirectResponse("/profile", status_code=303)
    auth.set_session(response, user_id)
    return response


@router.get("/signout")
async def signout() -> Response:
    response = RedirectResponse("/", status_code=303)
    auth.clear_session(response)
    return response


# ----------------------------------------------------------------- profile


@router.get("/profile", response_class=HTMLResponse)
async def profile_view(request: Request) -> Response:
    async with connection() as conn:
        user = await auth.current_user(conn, request)
        if user is None:
            return RedirectResponse("/signin", status_code=303)
        cur = await conn.execute(
            "SELECT * FROM user_profiles WHERE user_id = %s", (user["id"],)
        )
        profile = await cur.fetchone() or {}
        facet = await queries.facets(conn, limit=64)
        cur = await conn.execute(
            """
            SELECT m.score, m.reasons, t.id, t.title, t.organization_path,
                   t.district_name, t.procurement_nature, t.closing_at
              FROM matches m JOIN tenders t ON t.id = m.tender_id
             WHERE m.user_id = %s AND t.status = 'live' AND t.closing_at > now()
             ORDER BY m.score DESC LIMIT 20
            """,
            (user["id"],),
        )
        matches = list(await cur.fetchall())

    return await _render(
        request, "profile.html",
        {"user": user, "profile": profile, "facets": facet, "matches": matches,
         "errors": {}, "saved": request.query_params.get("saved") == "1",
         "vapid_public_key": settings.vapid_public_key},
    )


@router.post("/profile", response_class=HTMLResponse)
async def profile_save(
    request: Request,
    business_description: str = Form(""),
    districts: list[str] = Form(default=[]),
    natures: list[str] = Form(default=[]),
    min_security: str = Form(""),
    max_security: str = Form(""),
) -> Response:
    async with connection() as conn:
        user = await auth.current_user(conn, request)
        if user is None:
            return RedirectResponse("/signin", status_code=303)

        description = (business_description or "").strip()
        if len(description) < auth.MIN_DESCRIPTION_CHARS:
            return await _render(
                request, "profile.html",
                {
                    "user": user,
                    "profile": {"business_description": description,
                                "district_names": districts,
                                "procurement_natures": natures},
                    "facets": await queries.facets(conn, limit=64),
                    "matches": [],
                    "errors": {"business_description":
                               f"Please write at least {auth.MIN_DESCRIPTION_CHARS} "
                               "characters — short descriptions match badly."},
                },
                status=400,
            )

        def money(raw: str) -> float | None:
            try:
                return float(raw.replace(",", "")) if raw.strip() else None
            except ValueError:
                return None

        await conn.execute(
            """
            UPDATE user_profiles
               SET business_description = %s, district_names = %s,
                   procurement_natures = %s, min_security = %s,
                   max_security = %s, updated_at = now()
             WHERE user_id = %s
            """,
            (description, districts, [n for n in natures if n in NATURES],
             money(min_security), money(max_security), user["id"]),
        )
        await conn.commit()

    return RedirectResponse("/profile?saved=1", status_code=303)
