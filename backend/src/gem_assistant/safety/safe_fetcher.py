"""The ONLY way this project is allowed to contact GeM.

Two entry points share one guarded path:
- get():     plain downloads (bid PDFs) through the httpx transport.
- execute(): requests the headless browser wants to make. The browser hands
             over a `perform` callback and the request only happens if every
             guard passes.

Each guard has one job and is injected, so the order of checks lives here
and nowhere else.
"""

from __future__ import annotations

from typing import Callable

from ..config import PLACEHOLDER_CONTACT
from ..domain.models import RequestRecord
from ..domain.ports import Clock, HttpResponse, HttpTransport, RequestLog
from .circuit_breaker import CircuitBreaker
from .daily_budget import DailyBudget
from .errors import BlockedByGeM, MissingContactEmail, ResponseTooLarge, UnexpectedResponse
from .rate_limiter import RateLimiter
from .response_classifier import Expect, ResponseClassifier, ResponseKind
from .url_policy import UrlPolicy


class SafeFetcher:
    def __init__(
        self,
        transport: HttpTransport,
        policy: UrlPolicy,
        limiter: RateLimiter,
        budget: DailyBudget,
        breaker: CircuitBreaker,
        classifier: ResponseClassifier,
        log: RequestLog,
        clock: Clock,
        user_agent: str,
        max_bytes: int,
    ):
        self._transport = transport
        self._policy = policy
        self._limiter = limiter
        self._budget = budget
        self._breaker = breaker
        self._classifier = classifier
        self._log = log
        self._clock = clock
        self.user_agent = user_agent
        self._max_bytes = max_bytes
        self.requests_sent = 0  # monotonic, this process only

    def get(self, url: str, expect: Expect = Expect.HTML) -> HttpResponse:
        headers = {"User-Agent": self.user_agent}
        return self.execute(url, "GET", lambda: self._transport.get(url, headers, self._max_bytes), expect)

    def execute(
        self, url: str, method: str, perform: Callable[[], HttpResponse], expect: Expect
    ) -> HttpResponse:
        with self._limiter.hold():  # one request at a time, across every running copy
            self._ensure_contact()
            self._breaker.ensure_closed()
            self._policy.check(url, method)
            self._budget.ensure_available()
            self._limiter.wait()

            label = url if method == "GET" else f"{method} {url}"
            self.requests_sent += 1
            try:
                response = perform()
            except ResponseTooLarge:  # the transport stopped reading at the size cap
                self._record(label, None, "too_large")
                raise
            except Exception as error:  # network failure: log it, never retry automatically
                self._record(label, None, f"network_error: {type(error).__name__}")
                raise

            if len(response.body) > self._max_bytes:
                self._record(label, response.status, "too_large")
                raise UnexpectedResponse(f"Response larger than {self._max_bytes} bytes")

            result = self._classifier.classify(response, expect=expect)
            self._record(label, response.status, result.kind.value)

            if result.kind is ResponseKind.BLOCK:
                self._breaker.trip(f"{result.reason} — {label}")
                raise BlockedByGeM(result.reason)
            if result.kind is ResponseKind.NOT_FOUND:
                raise UnexpectedResponse(result.reason)
            return response

    def ensure_ready(self) -> None:
        """Cheap pre-check before starting a run: contact set, breaker closed, budget left."""
        self._ensure_contact()
        self._breaker.ensure_closed()
        self._budget.ensure_available()

    def _ensure_contact(self) -> None:
        if PLACEHOLDER_CONTACT in self.user_agent:
            raise MissingContactEmail(
                "Add your contact email on the Safety page first: every request names a contact "
                "so GeM can reach you.")

    def _record(self, url: str, status: int | None, outcome: str) -> None:
        self._log.record(RequestRecord(at=self._clock.now(), url=url, status=status, outcome=outcome))
