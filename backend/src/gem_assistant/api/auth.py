"""Logins for website mode. One password (GEM_PASSWORD), signed session cookies.

A session token is "<expiry>.<signature>". The signature covers the expiry and a hash of the
password, so changing the password logs every browser out. Standard library only.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Callable

COOKIE = "gem_session"
SESSION_SECONDS = 30 * 86400


class Sessions:
    def __init__(self, password: str, secret: bytes, now: Callable[[], float] = time.time):
        self._password = password.encode()
        self._secret = secret
        self._now = now

    def password_ok(self, typed: str) -> bool:
        return hmac.compare_digest(typed.encode(), self._password)

    def issue(self) -> str:
        expiry = str(int(self._now()) + SESSION_SECONDS)
        return f"{expiry}.{self._sign(expiry)}"

    def valid(self, token: str | None) -> bool:
        expiry, _, signature = (token or "").partition(".")
        if not expiry.isdigit() or not hmac.compare_digest(signature, self._sign(expiry)):
            return False
        return int(expiry) > self._now()

    def _sign(self, expiry: str) -> str:
        message = f"{expiry}:{hashlib.sha256(self._password).hexdigest()}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()


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
