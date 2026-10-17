"""The profile's search once a day by itself, at the time set in GEM_DAILY_SEARCH_AT (India time).

It is the same run as a search he starts, so every guard applies: one at a time, the 15-minute
gap, the safety stop, the daily request cap. A refused or failed day is not retried: the next try
is tomorrow, so nothing can loop against GeM.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, time, timedelta, timezone

from ..domain.ports import Clock, DiscoveryRunStore
from .discovery_service import DiscoveryNotAllowed, DiscoveryService

INDIA = timezone(timedelta(hours=5, minutes=30))  # no daylight saving
TRIGGER = "schedule"


class DailySearch:
    def __init__(self, discovery: DiscoveryService, runs: DiscoveryRunStore, clock: Clock, at: time | None):
        self._discovery = discovery
        self._runs = runs
        self._clock = clock
        self._at = at
        self._tried_on: date | None = None  # refused runs leave no record; this stops a retry loop

    @property
    def at(self) -> time | None:
        return self._at

    def next_run(self) -> datetime | None:
        """Today at the set time; at once when a server started later hasn't run today's yet;
        else tomorrow. None when it is turned off."""
        if self._at is None:
            return None
        now = self._clock.now().astimezone(INDIA)
        last = self._runs.last(trigger=TRIGGER)
        done = {self._tried_on, last.started_at.astimezone(INDIA).date() if last else None}
        today = datetime.combine(now.date(), self._at, tzinfo=INDIA)
        if now.date() in done:
            return today + timedelta(days=1)
        return max(today, now)

    def run_if_due(self) -> str:
        """Runs today's search when it is due. Says what happened, for the server log."""
        due = self.next_run()
        now = self._clock.now()
        if due is None or now < due:
            return "not due"
        self._tried_on = now.astimezone(INDIA).date()
        try:
            run = self._discovery.run(trigger=TRIGGER)
        except DiscoveryNotAllowed as error:
            return f"skipped: {error}"
        return f"[{run.outcome}] {run.message}"

    def start(self) -> threading.Thread | None:
        if self._at is None:
            return None
        wake = threading.Event()  # never set: only its timed wait is used

        def loop() -> None:
            while True:
                wait = (self.next_run() - self._clock.now()).total_seconds()
                if wait > 0:
                    wake.wait(min(wait, 600))  # look again every 10 min: a laptop may sleep through
                    continue
                print(f"Daily GeM search: {self.run_if_due()}", flush=True)

        thread = threading.Thread(target=loop, daemon=True, name="gem-daily-search")
        thread.start()
        return thread
