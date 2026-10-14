import hashlib
import hmac
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.ai import visible_history
from app.config import settings
from app.session import session_manager

router = APIRouter()

_PAGE = Path(__file__).with_name("browse.html")
_COOKIE = "maipt_browse"
_MAX_AGE = 7 * 24 * 60 * 60


class LoginBody(BaseModel):
    username: str
    password: str


def _created_at(session: dict):
    created = session.get("created_at")
    if hasattr(created, "isoformat"):
        return created.isoformat()
    return created


def _same(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return hmac.compare_digest(
        hashlib.sha256(left.encode()).digest(),
        hashlib.sha256(right.encode()).digest(),
    )


def _sign(username: str) -> str:
    expires = int(time.time()) + _MAX_AGE
    payload = f"{username}.{expires}".encode()
    signature = hmac.new(settings.API_KEY.encode(), payload, hashlib.sha256).hexdigest()
    return payload.decode() + "." + signature


def _cookie_ok(request: Request) -> bool:
    token = request.cookies.get(_COOKIE) or ""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    username, expires, signature = parts
    if username != settings.BROWSE_USERNAME or not settings.BROWSE_USERNAME:
        return False
    try:
        if int(expires) < time.time():
            return False
    except ValueError:
        return False
    payload = f"{username}.{expires}".encode()
    expected = hmac.new(settings.API_KEY.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _require(request: Request) -> None:
    if not _cookie_ok(request):
        raise HTTPException(status_code=401, detail="Sign in required")


def _summary(session: dict) -> dict:
    history = visible_history(session)
    last = history[-1]["content"] if history else ""
    if len(last) > 140:
        last = last[:140].rstrip()
    return {
        "session_id": session.get("session_id"),
        "user_id": session.get("user_id"),
        "week_number": session.get("week_number"),
        "status": session.get("status"),
        "created_at": _created_at(session),
        "message_count": len(history),
        "last_message": last,
    }


@router.get("/browse", response_class=HTMLResponse)
async def browse_page():
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))


@router.post("/browse/login")
async def browse_login(body: LoginBody, response: Response):
    username = (body.username or "").strip()
    password = body.password or ""
    expected_user = settings.BROWSE_USERNAME
    expected_password = settings.BROWSE_PASSWORD
    user_ok = _same(username, expected_user)
    pass_ok = _same(password, expected_password)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Those details do not match")
    response.set_cookie(
        _COOKIE,
        _sign(username),
        max_age=_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/browse",
    )
    return {"ok": True}


@router.post("/browse/logout")
async def browse_logout(response: Response):
    response.delete_cookie(_COOKIE, path="/browse")
    return {"ok": True}


@router.get("/browse/api/me")
async def browse_me(request: Request):
    _require(request)
    return {"ok": True, "username": settings.BROWSE_USERNAME}


@router.get("/browse/api/sessions")
async def browse_sessions(request: Request, user_id: Optional[int] = None):
    _require(request)
    sessions = session_manager.list_sessions(user_id)
    return {"sessions": [_summary(session) for session in sessions]}


@router.get("/browse/api/session/{session_id}")
async def browse_conversation(session_id: str, request: Request):
    _require(request)
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {
        "session_id": session_id,
        "user_id": session.get("user_id"),
        "week_number": session.get("week_number"),
        "status": session.get("status"),
        "created_at": _created_at(session),
        "conversation": visible_history(session),
        "diagram_suggestions": session.get("diagram_suggestions") or [],
    }
