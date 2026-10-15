"""Enforces a minimum gap between requests, and one request at a time — across every
running copy of the app (the web app and a command-line search share one database)."""

from __future__ import annotations

import fcntl
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from ..domain.ports import Clock


class RateLimiter:
    def __init__(self, clock: Clock, min_interval_seconds: float, last_request_at: datetime | None = None,
                 last_request_source: Callable[[], datetime | None] | None = None,
                 lock_path: Path | None = None):
        self._clock = clock
        self._min_interval = min_interval_seconds
        self._last = last_request_at
        # The request log is shared by all processes; this process's memory is not.
        self._last_source = last_request_source
        self._lock_path = lock_path
        self._thread_lock = threading.Lock()

    @contextmanager
    def hold(self) -> Iterator[None]:
        """Held by SafeFetcher for the whole request: a thread lock for this process, an OS
        file lock for every other process."""
        with self._thread_lock:
            if self._lock_path is None:
                yield
                return
            with open(self._lock_path, "a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)  # released when the file closes
                yield

    def wait(self) -> None:
        last = self._last
        shared = self._last_source() if self._last_source else None
        if shared is not None and (last is None or shared > last):
            last = shared
        if last is not None:
            remaining = self._min_interval - (self._clock.now() - last).total_seconds()
            if remaining > 0:
                self._clock.sleep(remaining)
        self._last = self._clock.now()
