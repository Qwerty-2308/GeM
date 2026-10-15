class SafetyError(Exception):
    """Base class: a request was refused or stopped for safety reasons."""


class FetchingStopped(SafetyError):
    """The circuit breaker is open. A human must reset it."""


class UrlNotAllowed(SafetyError):
    """The URL is outside what this tool is allowed to touch."""


class DailyBudgetExhausted(SafetyError):
    """The daily request cap has been reached."""


class BlockedByGeM(SafetyError):
    """GeM answered with something that looks like a block or challenge. Breaker tripped."""


class UnexpectedResponse(SafetyError):
    """A non-200 answer that is not a block (e.g. 404). Not retried."""


class MissingContactEmail(SafetyError):
    """No contact email is set, so GeM could not reach whoever runs this tool."""


class ResponseTooLarge(UnexpectedResponse):
    """The size cap was hit while downloading. One document's problem, not a block."""
