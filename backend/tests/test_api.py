"""End-to-end through the HTTP API, with the real PDF and a temp database."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gem_assistant.api.app import create_app
from gem_assistant.container import Container

from .conftest import SAMPLE_PDF


@pytest.fixture
def client(container: Container) -> TestClient:
    return TestClient(create_app(container, allowed_hosts=frozenset({"testserver"})))


def _import(client: TestClient) -> int:
    response = client.post("/api/bids/import",
                           files={"file": ("bid.pdf", SAMPLE_PDF.read_bytes(), "application/pdf")})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_import_and_view_bid(client):
    bid_id = _import(client)
    detail = client.get(f"/api/bids/{bid_id}").json()
    assert detail["bid_number"] == "GEM/2025/B/6819518"
    assert detail["verdict"] == "NEEDS_REVIEW"  # fake clock is before the closing date
    reasons = " ".join(r["reason"] for r in detail["results"])
    assert "No seller profile" in reasons
    assert "separate terms document" in reasons

    listing = client.get("/api/bids").json()
    assert [b["id"] for b in listing] == [bid_id]


def test_same_pdf_twice_is_not_duplicated(client):
    first = _import(client)
    second = client.post("/api/bids/import",
                         files={"file": ("again.pdf", SAMPLE_PDF.read_bytes(), "application/pdf")}).json()
    assert second == {"id": first, "already_imported": True}


def test_a_passed_deadline_without_a_corrigendum_check_needs_review(client, clock):
    """An imported PDF was never checked for a corrigendum, so a passed deadline may have
    been extended: the bid needs a look, it is not BLOCKED."""
    bid_id = _import(client)
    clock.advance(days=30)
    detail = client.get(f"/api/bids/{bid_id}").json()
    assert detail["verdict"] == "NEEDS_REVIEW"
    assert any("may have been extended" in r["reason"] for r in detail["results"])


def test_profile_round_trip_changes_verdict_reasons(client):
    bid_id = _import(client)
    profile = {
        "business_name": "Test Traders", "is_mse": True, "is_startup": False, "is_oem": False,
        "annual_turnover_rupees": 5000000, "years_experience": 4,
        "categories": ["Pumps", "pumps", " "], "certifications": [], "states": ["Tamil Nadu"],
    }
    saved = client.put("/api/profile", json=profile).json()
    assert saved["categories"] == ["Pumps"]  # cleaned + de-duplicated
    assert client.get("/api/profile").json()["business_name"] == "Test Traders"

    reasons = " ".join(r["reason"] for r in client.get(f"/api/bids/{bid_id}").json()["results"])
    assert "No seller profile" not in reasons
    assert "None of the items in this bid is one of your products" in reasons


def test_non_pdf_upload_is_rejected(client):
    response = client.post("/api/bids/import", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert response.status_code == 400


def test_safety_status_and_reset(client, container, transport):
    transport.queue(status=403)
    with pytest.raises(Exception):
        container.fetcher.get("https://bidplus.gem.gov.in/all-bids")

    status = client.get("/api/safety").json()
    assert status["breaker"]["open"] is True
    assert status["budget"]["used_today"] == 1

    assert client.post("/api/safety/reset", json={"confirmed": False}).status_code == 400
    ok = client.post("/api/safety/reset", json={"confirmed": True, "note": "checked"}).json()
    assert ok["breaker"]["open"] is False


def test_other_websites_cannot_post_to_the_app(client):
    response = client.post("/api/safety/reset", json={"confirmed": True},
                           headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_non_local_host_header_is_refused(container):
    client = TestClient(create_app(container))  # default: only 127.0.0.1 / localhost
    assert client.get("/api/bids", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/bids", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_frontend_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "GeM Assistant" in response.text


def test_pages_cannot_be_framed_or_sniffed(client):
    """Any website could otherwise load this local app in a hidden frame and trick a click
    on "Resume fetching"."""
    for path in ("/", "/api/safety"):
        headers = client.get(path).headers
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert headers["x-frame-options"] == "DENY"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"


def test_no_third_party_script_page_is_served(client):
    assert client.get("/api/docs").status_code == 404  # Swagger UI loads scripts from a CDN


@pytest.mark.parametrize("args, opens", [(["serve"], True), (["serve", "--no-open"], False)])
def test_serve_opens_the_app_in_the_browser(monkeypatch, tmp_path, args, opens):
    import uvicorn

    from gem_assistant import cli

    monkeypatch.setenv("GEM_DATA_DIR", str(tmp_path))
    served, opened = [], []
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: served.append((host, port)))
    monkeypatch.setattr(cli, "_open_browser_later", opened.append)
    monkeypatch.setattr(cli, "_already_running", lambda port: False)  # whatever this machine has on 8000
    assert cli.main(args) == 0
    assert served == [("127.0.0.1", 8000)]
    assert opened == (["http://127.0.0.1:8000/"] if opens else [])


@pytest.mark.parametrize("no_open", [[], ["--no-open"]])
def test_serve_opens_the_running_app_instead_of_starting_a_second(monkeypatch, tmp_path, no_open):
    import socket

    import uvicorn

    from gem_assistant import cli

    monkeypatch.setenv("GEM_DATA_DIR", str(tmp_path))
    served, opened = [], []
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: served.append(port))
    monkeypatch.setattr(cli, "_open_browser_later", opened.append)
    with socket.socket() as running:
        running.bind(("127.0.0.1", 0))
        running.listen()
        port = running.getsockname()[1]
        assert cli.main(["serve", "--port", str(port), *no_open]) == 0
    assert (served, opened) == ([], [] if no_open else [f"http://127.0.0.1:{port}/"])


@pytest.fixture
def mac(monkeypatch, tmp_path):
    """A pretend Mac: home in tmp_path, never run before, macOS's own commands recorded instead of run."""
    import sys

    calls = tmp_path / "calls"
    calls.touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("launchctl", "open", "curl"):
        (bin_dir / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\n')
        (bin_dir / name).chmod(0o755)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GEM_DATA_DIR", str(tmp_path / "Application Support" / "GeM Assistant"))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return tmp_path, calls


def test_install_app_registers_the_server_to_run_in_the_background(mac):
    """The server is a Login Item of its own: macOS ends whatever an app started when the app quits."""
    import os
    import plistlib
    import sys
    from pathlib import Path

    from gem_assistant import cli

    home, calls = mac
    data = home / "Application Support" / "GeM Assistant"
    assert cli.main(["install-app"]) == 0

    agent = home / "Library" / "LaunchAgents" / "local.gem-assistant.server.plist"
    job = plistlib.loads(agent.read_bytes())
    command = Path(sys.executable).with_name("gem-assistant")  # this install's own command
    assert os.access(command, os.X_OK)
    assert job["ProgramArguments"] == [str(command), "serve", "--no-open"]
    assert job["EnvironmentVariables"] == {"GEM_DATA_DIR": str(data)}  # the same data the install used
    assert job["StandardOutPath"] == job["StandardErrorPath"] == str(data / "app.log")
    assert job["RunAtLoad"] and job["KeepAlive"] == {"SuccessfulExit": False}  # at login; again after a crash
    assert data.stat().st_mode & 0o777 == 0o700  # launchd writes the log there; seller data is owner-only
    domain = f"gui/{os.getuid()}"
    assert calls.read_text().splitlines() == [  # a reinstall replaces the running copy
        f"launchctl bootout {domain}/local.gem-assistant.server",
        f"launchctl bootstrap {domain} {agent}",
    ]


def test_the_app_icon_opens_the_page(mac):
    import plistlib
    import subprocess

    from gem_assistant import cli

    home, calls = mac
    assert cli.main(["install-app"]) == 0
    calls.write_text("")

    contents = home / "Applications" / "GeM Assistant.app" / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundlePackageType"] == "APPL"
    subprocess.run([str(contents / "MacOS" / info["CFBundleExecutable"])], check=True)
    assert [line for line in calls.read_text().splitlines() if not line.startswith("curl")] == [
        "open http://127.0.0.1:8000/"]


@pytest.fixture
def fresh_install(settings, clock, transport):
    """As installed: nobody has set GEM_CONTACT_EMAIL."""
    from dataclasses import replace

    from gem_assistant.config import PLACEHOLDER_CONTACT
    settings = replace(settings, safety=replace(settings.safety, contact_email=PLACEHOLDER_CONTACT))
    return settings, Container.build(settings, clock=clock, transport=transport)


def test_contact_email_can_be_set_from_the_app(fresh_install, clock, transport):
    settings, container = fresh_install
    client = TestClient(create_app(container, allowed_hosts=frozenset({"testserver"})))
    assert client.get("/api/safety").json()["contact_email"] is None

    response = client.put("/api/safety/contact", json={"email": " seller@example.com "})
    assert response.status_code == 200, response.text
    assert response.json()["contact_email"] == "seller@example.com"
    assert "contact: seller@example.com" in container.fetcher.user_agent
    container.fetcher.ensure_ready()  # no longer refuses

    again = Container.build(settings, clock=clock, transport=transport)  # survives a restart
    assert "contact: seller@example.com" in again.fetcher.user_agent


@pytest.mark.parametrize("email", ["", "not-an-email", "a@b", "x@example.com\r\nX-Evil: 1", "a b@example.com"])
def test_a_bad_contact_email_is_refused(client, container, email):
    before = container.fetcher.user_agent
    assert client.put("/api/safety/contact", json={"email": email}).status_code == 400
    assert container.fetcher.user_agent == before


def test_search_preferences_round_trip(client):
    profile = {"categories": ["fire extinguisher"], "ignore_words": ["refilling", "AMC"], "bid_order": "closing"}
    assert client.put("/api/profile", json=profile).status_code == 200
    saved = client.get("/api/profile").json()
    assert (saved["ignore_words"], saved["bid_order"]) == (["refilling", "AMC"], "closing")
    assert client.put("/api/profile", json={**profile, "bid_order": "random"}).status_code == 422
