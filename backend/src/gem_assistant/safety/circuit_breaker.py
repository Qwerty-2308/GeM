"""Stops all fetching after a warning sign. Stays stopped, across restarts, until a human resets it."""

from __future__ import annotations

from ..domain.models import BreakerState
from ..domain.ports import BreakerStateStore, Clock
from .errors import FetchingStopped


class CircuitBreaker:
    def __init__(self, store: BreakerStateStore, clock: Clock):
        self._store = store
        self._clock = clock

    def state(self) -> BreakerState:
        return self._store.load()

    def ensure_closed(self) -> None:
        state = self._store.load()
        if state.open:
            raise FetchingStopped(f"Fetching is stopped: {state.reason}. Reset it from the Safety page.")

    def trip(self, reason: str) -> None:
        state = self._store.load()
        if state.open:
            return  # keep the first reason
        self._store.save(state.model_copy(update={
            "open": True, "reason": reason, "opened_at": self._clock.now(),
        }))

    def reset(self, confirmed: bool, note: str) -> BreakerState:
        if not confirmed:
            raise ValueError("Reset must be explicitly confirmed by a person")
        state = self._store.load()
        new_state = state.model_copy(update={
            "open": False, "reason": None, "opened_at": None,
            "last_reset_at": self._clock.now(), "last_reset_note": note or None,
        })
        self._store.save(new_state)
        return new_state
