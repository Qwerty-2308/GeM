"""All tunable settings in one place, read from environment variables."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Default contact address. SafeFetcher refuses to send anything while it is in use, so
# GeM can always reach whoever runs the tool.
PLACEHOLDER_CONTACT = "set-GEM_CONTACT_EMAIL@example.com"


def _load_env_file(path: Path) -> None:
    """Secrets (GROQ_API_KEY, GOOGLE_CLIENT_SECRET, ...) from the data folder's .env: owner-only,
    outside the project, so never synced with the Desktop or committed. KEY=VALUE lines; # comments;
    optional quotes. A variable already set in the real environment wins."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name or name.startswith("#"):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(name, value)


def default_data_dir() -> Path:
    """Per-user app data, outside the project folder. The project may live in a synced
    folder (iCloud Desktop & Documents, Dropbox), and the seller profile must never leave
    this machine — nor should SQLite run under a sync client."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "GeM Assistant"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "gem-assistant"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class SafetySettings:
    # Never go below these. They are deliberately far slower than GeM needs.
    min_interval_seconds: float = 4.0
    daily_request_limit: int = 250
    request_timeout_seconds: float = 30.0
    max_download_bytes: int = 20 * 1024 * 1024
    contact_email: str = PLACEHOLDER_CONTACT
    allowed_host_suffix: str = "gem.gov.in"
    # From robots.txt observed 13-Sep-2026, plus our own hard bans.
    blocked_path_prefixes: tuple[str, ...] = (
        "/resources/",
        "/cgi-bin/",
        "/bg_emd/",
    )
    # The listing page's own search request. POST-only, and only the headless
    # browser rendering /all-bids ever triggers it.
    post_allowed_paths: tuple[str, ...] = ("/all-bids-data",)
    # The listing page's own corrigendum view, which carries the bid's document id in
    # the path (docs/02-RECON-FINDINGS.md §4). Only "<prefix>/<id>" is allowed.
    post_allowed_prefixes: tuple[str, ...] = ("/bidding/bid/viewCorrigendum",)

    @property
    def user_agent(self) -> str:
        return user_agent_for(self.contact_email)


def user_agent_for(contact_email: str) -> str:
    return f"GeMAssistant/0.1 (private seller tool; contact: {contact_email})"


@dataclass(frozen=True)
class DiscoverySettings:
    # Hard ceilings; env vars can only lower them.
    min_minutes_between_runs: int = 15   # GeM: new bids take up to 15 min to show
    max_keywords: int = 5
    max_pages_per_keyword: int = 2       # 10 results per page
    max_downloads_per_run: int = 5
    # One request each, so this is capped alongside downloads rather than applied to
    # every bid the search merely listed.
    max_corrigendum_checks_per_run: int = 5
    browser_timeout_seconds: float = 60.0
    browser_executable_path: str | None = None  # default: Playwright's own Chromium


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    frontend_dir: Path
    max_pdf_pages: int = 300
    safety: SafetySettings = field(default_factory=SafetySettings)
    discovery: DiscoverySettings = field(default_factory=DiscoverySettings)
    groq_model: str = "openai/gpt-oss-120b"  # production on Groq, tool use, 131k context
    password: str | None = field(default=None, repr=False)  # website mode only (GEM_PASSWORD)
    # Website mode, Google sign-in: only these emails get in (GEM_ALLOWED_EMAILS, comma-separated).
    google_client_id: str | None = None
    google_client_secret: str | None = field(default=None, repr=False)
    allowed_emails: tuple[str, ...] = ()

    @property
    def groq_key_file(self) -> Path:
        """Set with `gem-assistant set-groq-key`; GROQ_API_KEY wins when set. Owner-only."""
        return self.data_dir / "groq-api-key.txt"

    @property
    def recon_dir(self) -> Path:
        return self.data_dir / "recon"

    @property
    def contact_file(self) -> Path:
        """The contact email typed into the app. It wins over GEM_CONTACT_EMAIL, so a
        seller never has to edit shell files."""
        return self.data_dir / "contact-email.txt"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("GEM_DATA_DIR") or default_data_dir())
        _load_env_file(data_dir / ".env")
        safety = SafetySettings(
            min_interval_seconds=max(4.0, _env_float("GEM_MIN_INTERVAL_SECONDS", 4.0)),
            daily_request_limit=min(250, _env_int("GEM_DAILY_REQUEST_LIMIT", 250)),
            # An empty value counts as unset, so the request guard still refuses.
            contact_email=os.environ.get("GEM_CONTACT_EMAIL", "").strip() or PLACEHOLDER_CONTACT,
        )
        defaults = DiscoverySettings()
        discovery = DiscoverySettings(
            min_minutes_between_runs=max(defaults.min_minutes_between_runs,
                                         _env_int("GEM_MIN_MINUTES_BETWEEN_RUNS", 15)),
            max_keywords=min(defaults.max_keywords, _env_int("GEM_MAX_KEYWORDS", 5)),
            max_pages_per_keyword=min(defaults.max_pages_per_keyword, _env_int("GEM_MAX_PAGES_PER_KEYWORD", 2)),
            max_downloads_per_run=min(defaults.max_downloads_per_run, _env_int("GEM_MAX_DOWNLOADS_PER_RUN", 5)),
            max_corrigendum_checks_per_run=min(defaults.max_corrigendum_checks_per_run,
                                              _env_int("GEM_MAX_CORRIGENDUM_CHECKS_PER_RUN", 5)),
            browser_executable_path=os.environ.get("GEM_CHROMIUM_PATH") or None,
        )
        return cls(
            data_dir=data_dir,
            db_path=data_dir / "gem.sqlite3",
            frontend_dir=Path(os.environ.get("GEM_FRONTEND_DIR", PROJECT_ROOT / "frontend")),
            safety=safety,
            discovery=discovery,
            groq_model=os.environ.get("GROQ_MODEL") or cls.groq_model,
            password=os.environ.get("GEM_PASSWORD") or None,
            google_client_id=os.environ.get("GOOGLE_CLIENT_ID", "").strip() or None,
            google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", "").strip() or None,
            allowed_emails=tuple(e.strip().lower() for e in os.environ.get("GEM_ALLOWED_EMAILS", "").split(",")
                                 if e.strip()),
        )
