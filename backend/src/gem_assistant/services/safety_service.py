"""Read-only view of the safety layer, plus the human reset."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import SafetySettings
from ..domain.models import BreakerState, RequestRecord
from ..domain.ports import RequestLog
from ..safety.circuit_breaker import CircuitBreaker
from ..safety.daily_budget import DailyBudget

RULES = (
    "Never logs in to GeM. No passwords or accounts exist in this tool.",
    "Only https URLs on gem.gov.in. robots.txt-disallowed paths (including /resources/ and bg_emd) are never fetched.",
    "Bid search opens the public /all-bids page in a headless browser. Only the page and its own search request "
    "reach GeM; scripts are served locally and images, styles and everything else are blocked.",
    "The browser's temporary cookies are thrown away after every search. The only POSTs are the page's "
    "own search and its own corrigendum view, and that view accepts a bid id and nothing deeper.",
    "Only documents GeM itself links to are downloaded, each only once. Document IDs are never guessed.",
    "Bids that were analysed are checked for a corrigendum, one request each — never every bid found. "
    "The corrigendum is shown to you as written; it is never read for a new deadline.",
    "Searches are at least {gap} minutes apart, with capped keywords, pages and downloads.",
    "One request at a time, at least {interval:g}s apart, at most {limit} per day.",
    "Identifies itself honestly in the User-Agent. No proxies, IP rotation or browser disguise.",
    "Nothing is sent to GeM until GEM_CONTACT_EMAIL is set.",
    "Any 401/403/429, server error, redirect, CAPTCHA or block page stops all fetching until you reset it.",
    "Failed requests are never retried automatically.",
)


@dataclass(frozen=True)
class SafetyStatus:
    breaker: BreakerState
    used_today: int
    daily_limit: int
    min_interval_seconds: float
    user_agent: str
    recent_requests: list[RequestRecord]
    rules: list[str]


class SafetyService:
    def __init__(self, breaker: CircuitBreaker, budget: DailyBudget, log: RequestLog, settings: SafetySettings,
                 min_gap_minutes: int = 15):
        self._min_gap_minutes = min_gap_minutes
        self._breaker = breaker
        self._budget = budget
        self._log = log
        self._settings = settings

    def status(self) -> SafetyStatus:
        return SafetyStatus(
            breaker=self._breaker.state(),
            used_today=self._budget.used_today(),
            daily_limit=self._budget.daily_limit,
            min_interval_seconds=self._settings.min_interval_seconds,
            user_agent=self._settings.user_agent,
            recent_requests=self._log.recent(20),
            rules=[r.format(interval=self._settings.min_interval_seconds, limit=self._budget.daily_limit,
                            gap=self._min_gap_minutes)
                   for r in RULES],
        )

    def reset(self, confirmed: bool, note: str) -> BreakerState:
        return self._breaker.reset(confirmed=confirmed, note=note)
