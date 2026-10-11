"""Enforces a minimum gap between requests. One request at a time."""

from __future__ import annotations

import threading
from datetime import datetime

from ..domain.ports import Clock


class RateLimiter:
    def __init__(self, clock: Clock, min_interval_seconds: float, last_request_at: datetime | None = None):
        self._clock = clock
        self._min_interval = min_interval_seconds
        self._last = last_request_at
        # Held for the whole request by SafeFetcher, so requests never overlap.
        self.lock = threading.Lock()

    def wait(self) -> None:
        if self._last is not None:
            elapsed = (self._clock.now() - self._last).total_seconds()
            remaining = self._min_interval - elapsed
            if remaining > 0:
                self._clock.sleep(remaining)
        self._last = self._clock.now()
