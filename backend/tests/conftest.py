from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gem_assistant.config import PROJECT_ROOT, SafetySettings, Settings
from gem_assistant.container import Container
from gem_assistant.domain.ports import HttpResponse

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_PDF = FIXTURES / "GEM-2025-B-6819518_doc8506455.pdf"
ALL_BIDS_SHELL = FIXTURES / "all-bids-shell_2026-09-13.html"


class FakeClock:
    """Time only moves when we say so; sleep() advances it instead of waiting."""

    def __init__(self, start: datetime):
        self.current = start
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.current += timedelta(seconds=seconds)

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


class FakeTransport:
    """Returns queued responses and records every call. Never touches the network."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.responses: list[HttpResponse | Exception] = []

    def queue(self, status: int = 200, body: bytes = b"<html>ok</html>", headers: dict | None = None):
        self.responses.append(HttpResponse(url="", status=status, headers=headers or {}, body=body))

    def get(self, url: str, headers: dict[str, str], max_bytes: int) -> HttpResponse:
        self.calls.append((url, headers))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _never_the_real_data_folder(monkeypatch, tmp_path_factory):
    """Settings.from_env() reads <data folder>/.env. No test may pick up the owner's real secrets
    (and then call Groq or Google with them), whatever this machine has set."""
    monkeypatch.setenv("GEM_DATA_DIR", str(tmp_path_factory.mktemp("data")))
    for name in ("GROQ_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GEM_ALLOWED_EMAILS",
                 "GEM_PASSWORD", "GEM_CONTACT_EMAIL", "GEM_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2025, 11, 1, 6, 0, tzinfo=timezone.utc))


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "test.sqlite3",
        frontend_dir=PROJECT_ROOT / "frontend",
        safety=replace(SafetySettings(), contact_email="test@example.com"),
    )


@pytest.fixture
def container(settings: Settings, clock: FakeClock, transport: FakeTransport) -> Container:
    return Container.build(settings, clock=clock, transport=transport)
