"""Push subscribe/unsubscribe endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tenderradar.alerts import push
from tenderradar.db.pool import connection
from tenderradar.web import auth

router = APIRouter()


@router.post("/push/subscribe")
async def subscribe(request: Request) -> JSONResponse:
    async with connection() as conn:
        user = await auth.current_user(conn, request)
        if user is None:
            return JSONResponse({"error": "not signed in"}, status_code=401)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid body"}, status_code=400)

        ok = await push.save_subscription(
            conn, user["id"], body, request.headers.get("user-agent")
        )
        await conn.commit()
    if not ok:
        return JSONResponse({"error": "incomplete subscription"}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/push/unsubscribe")
async def unsubscribe(request: Request) -> JSONResponse:
    async with connection() as conn:
        user = await auth.current_user(conn, request)
        if user is None:
            return JSONResponse({"error": "not signed in"}, status_code=401)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid body"}, status_code=400)

        endpoint = (body or {}).get("endpoint")
        if endpoint:
            await push.delete_subscription(conn, endpoint)
            await conn.commit()
    return JSONResponse({"ok": True})
