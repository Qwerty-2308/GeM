from __future__ import annotations

import hmac
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..auth import COOKIE, SESSION_SECONDS, Login, LoginRefused

router = APIRouter(prefix="/api", tags=["session"])

_OAUTH_COOKIE = "gem_oauth"
_OAUTH_PATH = "/api/auth/google"


class LoginIn(BaseModel):
    password: str


def _login(request: Request) -> Login | None:
    return request.app.state.login


def _start_session(response: Response, login: Login) -> None:
    response.set_cookie(COOKIE, login.sessions.issue(), max_age=SESSION_SECONDS, httponly=True, secure=True,
                        samesite="strict", path="/")


@router.get("/session")
def session(request: Request) -> dict:
    login = _login(request)
    if login is None:  # on this computer only: no login
        return {"login_required": False, "logged_in": True, "methods": []}
    return {"login_required": True, "logged_in": login.sessions.valid(request.cookies.get(COOKIE)),
            "methods": login.methods}


@router.post("/login")
def password_login(body: LoginIn, request: Request, response: Response) -> dict:
    login = _login(request)
    if login is None:
        return {"ok": True}
    if login.password is None:
        raise HTTPException(status_code=404, detail="Sign in with Google.")
    if not login.password_ok(body.password):
        time.sleep(1.0)  # slows guessing; a 12+ character password does the rest
        raise HTTPException(status_code=401, detail="Wrong password.")
    _start_session(response, login)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=True, samesite="strict")
    return {"ok": True}


def _callback_url(request: Request) -> str:
    """Where Google sends him back. It must be listed exactly in the Google client's
    "Authorized redirect URIs". Websites are https; plain http only on this computer."""
    host = request.headers.get("host", "")
    scheme = "http" if host.split(":")[0] in ("localhost", "127.0.0.1") else "https"
    return f"{scheme}://{host}{_OAUTH_PATH}/callback"


@router.get("/auth/google")
def google_start(request: Request) -> RedirectResponse:
    login = _login(request)
    if login is None or login.google is None:
        raise HTTPException(status_code=404, detail="Google sign-in isn't set up.")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    response = RedirectResponse(login.google.authorize_url(_callback_url(request), state, nonce), status_code=302)
    # Lax, not Strict: it has to come back with the redirect from Google, a cross-site navigation.
    response.set_cookie(_OAUTH_COOKIE, f"{state}.{nonce}", max_age=600, httponly=True, secure=True,
                        samesite="lax", path=_OAUTH_PATH)
    return response


@router.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    login = _login(request)
    if login is None or login.google is None:
        raise HTTPException(status_code=404, detail="Google sign-in isn't set up.")
    expected_state, _, nonce = request.cookies.get(_OAUTH_COOKIE, "").partition(".")
    try:
        # The state must be the one this browser was sent off with: stops someone logging him in
        # to a session of theirs (login CSRF) and replays.
        if error or not code or not expected_state or not hmac.compare_digest(state, expected_state):
            raise LoginRefused("failed")
        login.google.email_for(code, _callback_url(request), nonce)
    except LoginRefused as refused:
        response = RedirectResponse(f"/?login_error={refused.code}", status_code=302)
    else:
        response = RedirectResponse("/", status_code=302)
        _start_session(response, login)
    response.delete_cookie(_OAUTH_COOKIE, path=_OAUTH_PATH, httponly=True, secure=True, samesite="lax")
    return response
