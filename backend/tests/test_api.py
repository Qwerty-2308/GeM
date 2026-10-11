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
    assert "doesn't match any category" in reasons


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
