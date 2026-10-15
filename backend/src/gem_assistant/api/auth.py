"""Logins for website mode: Google sign-in for listed emails, a password, or both.
Signed session cookies. Standard library and httpx only.

A session token is "<expiry>.<signature>". The signature covers the expiry and a fingerprint of
the login settings (password, allowed emails, Google client), so changing any of them logs
every browser out.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

import httpx

COOKIE = "gem_session"
SESSION_SECONDS = 30 * 86400

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}


class Sessions:
    def __init__(self, secret: bytes, binding: str, now: Callable[[], float] = time.time):
        self._secret = secret
        self._binding = binding
        self._now = now

    def issue(self) -> str:
        expiry = str(int(self._now()) + SESSION_SECONDS)
        return f"{expiry}.{self._sign(expiry)}"

    def valid(self, token: str | None) -> bool:
        expiry, _, signature = (token or "").partition(".")
        if not expiry.isdigit() or not hmac.compare_digest(signature, self._sign(expiry)):
            return False
        return int(expiry) > self._now()

    def _sign(self, expiry: str) -> str:
        return hmac.new(self._secret, f"{expiry}:{self._binding}".encode(), hashlib.sha256).hexdigest()


class LoginRefused(Exception):
    def __init__(self, code: str):  # "not_allowed" or "failed": the page shows its own sentence
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GoogleLogin:
    """Sign in with Google (OpenID Connect, authorization code flow). Only `allowed_emails` get
    in: a Google account by itself proves nothing about who may use this site."""

    client_id: str
    client_secret: str
    allowed_emails: frozenset[str]  # lower case
    transport: httpx.BaseTransport | None = None
    now: Callable[[], float] = time.time

    def authorize_url(self, redirect_uri: str, state: str, nonce: str) -> str:
        return GOOGLE_AUTHORIZE_URL + "?" + urlencode({
            "client_id": self.client_id, "response_type": "code", "scope": "openid email",
            "redirect_uri": redirect_uri, "state": state, "nonce": nonce, "prompt": "select_account"})

    def email_for(self, code: str, redirect_uri: str, nonce: str) -> str:
        try:
            with httpx.Client(transport=self.transport, timeout=15) as client:
                response = client.post(GOOGLE_TOKEN_URL, data={
                    "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
                    "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
            claims = _jwt_claims(response.json()["id_token"]) if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            raise LoginRefused("failed")
        # The token came straight from Google's token endpoint over HTTPS, asked for with our client
        # secret, so its signature needn't be checked (Google's OpenID Connect guide). Its claims are.
        exp = claims.get("exp")
        if (claims.get("iss") not in _GOOGLE_ISSUERS or claims.get("aud") != self.client_id
                or not nonce or not hmac.compare_digest(str(claims.get("nonce", "")), nonce)
                or not isinstance(exp, (int, float)) or exp <= self.now()):
            raise LoginRefused("failed")
        email = str(claims.get("email", "")).strip().lower()
        if claims.get("email_verified") is not True or email not in self.allowed_emails:
            raise LoginRefused("not_allowed")
        return email


def _jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    if not isinstance(claims, dict):
        raise ValueError("not a claims object")
    return claims


@dataclass(frozen=True)
class Login:
    sessions: Sessions
    password: str | None = None
    google: GoogleLogin | None = None

    @property
    def methods(self) -> list[str]:
        return [name for name, on in (("google", self.google), ("password", self.password)) if on]

    def password_ok(self, typed: str) -> bool:
        return self.password is not None and hmac.compare_digest(typed.encode(), self.password.encode())

    @classmethod
    def build(cls, secret: bytes, password: str | None = None, google: GoogleLogin | None = None) -> "Login":
        binding = hashlib.sha256(json.dumps([
            password, google.client_id if google else None,
            sorted(google.allowed_emails) if google else None]).encode()).hexdigest()
        return cls(Sessions(secret, binding), password, google)


def load_or_create_secret(path: Path) -> bytes:
    """GEM_SECRET_KEY, or a random key kept in the data folder (owner-only), so logins
    survive a restart."""
    if os.environ.get("GEM_SECRET_KEY"):
        return os.environ["GEM_SECRET_KEY"].encode()
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return path.read_text().strip().encode()
