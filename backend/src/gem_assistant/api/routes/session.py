from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import COOKIE, SESSION_SECONDS

router = APIRouter(prefix="/api", tags=["session"])


class LoginIn(BaseModel):
    password: str


@router.get("/session")
def session(request: Request) -> dict:
    sessions = request.app.state.sessions
    if sessions is None:  # on this computer only: no login
        return {"login_required": False, "logged_in": True}
    return {"login_required": True, "logged_in": sessions.valid(request.cookies.get(COOKIE))}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    sessions = request.app.state.sessions
    if sessions is None:
        return {"ok": True}
    if not sessions.password_ok(body.password):
        time.sleep(1.0)  # slows guessing; a 12+ character password does the rest
        raise HTTPException(status_code=401, detail="Wrong password.")
    response.set_cookie(COOKIE, sessions.issue(), max_age=SESSION_SECONDS, httponly=True, secure=True,
                        samesite="strict", path="/")
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=True, samesite="strict")
    return {"ok": True}
