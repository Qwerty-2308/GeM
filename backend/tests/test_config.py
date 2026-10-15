"""Where the tool keeps its data, and which settings the environment may change."""

from __future__ import annotations

import sys
from dataclasses import replace

from gem_assistant.config import Settings
from gem_assistant.container import Container


def test_data_lives_in_the_mac_app_data_folder_not_the_project(monkeypatch, tmp_path):
    """The project may sit in an iCloud-synced Desktop; the seller profile must not."""
    monkeypatch.delenv("GEM_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    settings = Settings.from_env()
    assert settings.data_dir == tmp_path / "Library" / "Application Support" / "GeM Assistant"
    assert settings.db_path.parent == settings.data_dir


def test_other_systems_use_the_xdg_data_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("GEM_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(sys, "platform", "linux")
    assert Settings.from_env().data_dir == tmp_path / "xdg" / "gem-assistant"


def test_gem_data_dir_still_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("GEM_DATA_DIR", str(tmp_path / "custom"))
    assert Settings.from_env().data_dir == tmp_path / "custom"


def test_a_new_data_folder_is_private_to_the_user(settings, clock, transport, tmp_path):
    fresh = tmp_path / "new" / "data"
    Container.build(replace(settings, data_dir=fresh, db_path=fresh / "gem.sqlite3"),
                    clock=clock, transport=transport)
    assert fresh.stat().st_mode & 0o777 == 0o700


def test_secrets_are_read_from_the_data_folders_env_file(monkeypatch, tmp_path):
    """The .env sits in the data folder (owner-only, never synced or committed). Real environment
    variables win over it."""
    import os

    from gem_assistant.config import Settings

    (tmp_path / ".env").write_text(
        "# secrets\n"
        "GROQ_API_KEY=gsk_from_file\n"
        "GOOGLE_CLIENT_ID = 'id-from-file'\n"
        'GOOGLE_CLIENT_SECRET="secret=with=equals"\n'
        "\n"
        "GEM_ALLOWED_EMAILS=Father@Gmail.com, son@gmail.com\n"
        "GEM_PASSWORD=from-the-file-123\n")
    monkeypatch.setenv("GEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GEM_PASSWORD", "from-the-real-environment")
    for name in ("GROQ_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GEM_ALLOWED_EMAILS"):
        monkeypatch.setenv(name, "x")  # so the file's values are removed again after this test
        monkeypatch.delenv(name)

    settings = Settings.from_env()
    assert os.environ["GROQ_API_KEY"] == "gsk_from_file"  # the chat reads it from the environment
    assert (settings.google_client_id, settings.google_client_secret) == ("id-from-file", "secret=with=equals")
    assert settings.allowed_emails == ("father@gmail.com", "son@gmail.com")
    assert settings.password == "from-the-real-environment"


def test_no_env_file_is_fine(monkeypatch, tmp_path):
    from gem_assistant.config import Settings

    monkeypatch.setenv("GEM_DATA_DIR", str(tmp_path))
    assert Settings.from_env().data_dir == tmp_path
