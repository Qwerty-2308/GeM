"""Caps requests per calendar day (India time), counted from the persistent log."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..domain.ports import Clock, RequestLog
from .errors import DailyBudgetExhausted

IST = timezone(timedelta(hours=5, minutes=30))


class DailyBudget:
    def __init__(self, log: RequestLog, clock: Clock, daily_limit: int):
        self._log = log
        self._clock = clock
        self.daily_limit = daily_limit

    def _start_of_today(self) -> datetime:
        now_ist = self._clock.now().astimezone(IST)
        return now_ist.replace(hour=0, minute=0, second=0, microsecond=0)

    def used_today(self) -> int:
        return self._log.count_since(self._start_of_today())

    def ensure_available(self) -> None:
        if self.used_today() >= self.daily_limit:
            raise DailyBudgetExhausted(
                f"Daily limit of {self.daily_limit} requests reached. Try again tomorrow."
            )
