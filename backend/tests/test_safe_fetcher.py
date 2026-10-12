"""The rules that keep GeM from blocking us. These must never regress."""

from __future__ import annotations

import pytest

from gem_assistant.config import Settings
from gem_assistant.container import Container
from gem_assistant.safety.errors import (
    BlockedByGeM,
    DailyBudgetExhausted,
    FetchingStopped,
    UnexpectedResponse,
    UrlNotAllowed,
)
from gem_assistant.safety.response_classifier import Expect, ResponseClassifier, ResponseKind
from gem_assistant.domain.ports import HttpResponse

from .conftest import ALL_BIDS_SHELL

GOOD_URL = "https://bidplus.gem.gov.in/all-bids"


# ---------- URL policy ----------

@pytest.mark.parametrize("url", [
    "http://bidplus.gem.gov.in/all-bids",                       # not https
    "https://evil.com/all-bids",                               # wrong host
    "https://gem.gov.in.evil.com/",                            # suffix trick
    "https://notgem.gov.in/",                                  # suffix without dot
    "https://169.254.169.254/latest/meta-data",                # IP literal
    "https://user:pw@bidplus.gem.gov.in/all-bids",             # credentials
    "https://bidplus.gem.gov.in:8443/all-bids",                # odd port
    "https://bidplus.gem.gov.in/bg_emd/epbgservice/PBGIntimation",  # robots.txt
    "https://bidplus.gem.gov.in/resources/x.js",               # robots.txt
    "https://bidplus.gem.gov.in/all-bids-data",                # POST-only, browser only
    "https://bidplus.gem.gov.in/a/../bg_emd/x",                # traversal
])
def test_disallowed_urls_never_reach_the_network(container: Container, transport, url):
    with pytest.raises(UrlNotAllowed):
        container.fetcher.get(url)
    assert transport.calls == []


def test_allowed_url_is_fetched_with_honest_user_agent(container: Container, transport):
    transport.queue()
    container.fetcher.get(GOOD_URL)
    (url, headers), = transport.calls
    assert url == GOOD_URL
    assert "GeMAssistant" in headers["User-Agent"]
    assert "test@example.com" in headers["User-Agent"]


# ---------- rate limit ----------

def test_requests_are_spaced_by_minimum_interval(container: Container, transport, clock, settings: Settings):
    for _ in range(3):
        transport.queue()
    for _ in range(3):
        container.fetcher.get(GOOD_URL)
    assert clock.slept == [settings.safety.min_interval_seconds] * 2


def test_rate_limit_survives_restart(settings: Settings, clock, transport):
    first = Container.build(settings, clock=clock, transport=transport)
    transport.queue()
    first.fetcher.get(GOOD_URL)

    second = Container.build(settings, clock=clock, transport=transport)  # app restarted
    transport.queue()
    second.fetcher.get(GOOD_URL)
    assert clock.slept == [settings.safety.min_interval_seconds]


# ---------- daily budget ----------

def test_daily_budget_stops_requests(settings: Settings, clock, transport):
    from dataclasses import replace
    small = replace(settings, safety=replace(settings.safety, daily_request_limit=2))
    container = Container.build(small, clock=clock, transport=transport)
    transport.queue(); transport.queue()
    container.fetcher.get(GOOD_URL)
    container.fetcher.get(GOOD_URL)
    with pytest.raises(DailyBudgetExhausted):
        container.fetcher.get(GOOD_URL)
    assert len(transport.calls) == 2

    clock.advance(days=1)
    transport.queue()
    container.fetcher.get(GOOD_URL)  # new day, new budget


# ---------- circuit breaker ----------

@pytest.mark.parametrize("status,headers,body", [
    (403, {}, b""),
    (429, {}, b""),
    (401, {}, b""),
    (503, {}, b""),
    (302, {"location": "https://sso.gem.gov.in/ARXSSO/oauth/doLogin"}, b""),
    (200, {}, b"<html>Please complete the CAPTCHA</html>"),
    (200, {}, b"<html>The requested URL was rejected. Your support ID is 123</html>"),
])
def test_warning_signs_trip_breaker_and_stop_everything(container: Container, transport, status, headers, body):
    transport.queue(status=status, headers=headers, body=body)
    with pytest.raises(BlockedByGeM):
        container.fetcher.get(GOOD_URL)

    with pytest.raises(FetchingStopped):
        container.fetcher.get(GOOD_URL)
    assert len(transport.calls) == 1  # the second call never left the machine


def test_breaker_stays_open_after_restart(settings: Settings, clock, transport):
    first = Container.build(settings, clock=clock, transport=transport)
    transport.queue(status=403)
    with pytest.raises(BlockedByGeM):
        first.fetcher.get(GOOD_URL)

    second = Container.build(settings, clock=clock, transport=transport)
    with pytest.raises(FetchingStopped):
        second.fetcher.get(GOOD_URL)


def test_breaker_needs_explicit_human_confirmation(container: Container, transport):
    transport.queue(status=429)
    with pytest.raises(BlockedByGeM):
        container.fetcher.get(GOOD_URL)

    with pytest.raises(ValueError):
        container.safety.reset(confirmed=False, note="")
    container.safety.reset(confirmed=True, note="checked in browser")

    transport.queue()
    container.fetcher.get(GOOD_URL)


def test_not_found_is_not_retried_and_does_not_trip(container: Container, transport):
    transport.queue(status=404)
    with pytest.raises(UnexpectedResponse):
        container.fetcher.get(GOOD_URL)
    assert len(transport.calls) == 1
    assert container.safety.status().breaker.open is False


def test_network_error_is_logged_and_not_retried(container: Container, transport):
    transport.responses.append(TimeoutError("slow"))
    with pytest.raises(TimeoutError):
        container.fetcher.get(GOOD_URL)
    assert len(transport.calls) == 1
    assert container.safety.status().recent_requests[0].outcome.startswith("network_error")


def test_pdf_expected_but_html_returned_trips_breaker(container: Container, transport):
    transport.queue(body=b"<html>login</html>")
    with pytest.raises(BlockedByGeM):
        container.fetcher.get("https://bidplus.gem.gov.in/showbidDocument/8506455", expect=Expect.PDF)


def test_every_request_is_logged(container: Container, transport):
    transport.queue()
    container.fetcher.get(GOOD_URL)
    status = container.safety.status()
    assert status.used_today == 1
    assert status.recent_requests[0].url == GOOD_URL


# ---------- classifier against real GeM output ----------

def test_real_all_bids_page_is_not_mistaken_for_a_block():
    body = ALL_BIDS_SHELL.read_bytes()
    result = ResponseClassifier().classify(HttpResponse(url="", status=200, headers={}, body=body))
    assert result.kind is ResponseKind.OK


# ---------- browser requests through execute() ----------

def test_post_is_only_allowed_to_the_listing_search(container: Container, transport):
    from gem_assistant.domain.ports import HttpResponse as R
    ok = R(url="", status=200, headers={}, body=b'{"code": 200}')
    container.fetcher.execute("https://bidplus.gem.gov.in/all-bids-data", "POST", lambda: ok, Expect.JSON)
    for url in ["https://bidplus.gem.gov.in/bidding/updateLoadData/",
                "https://gem.gov.in/searchresult/getServices",
                "https://bidplus.gem.gov.in/public-bid-other-details/1"]:
        with pytest.raises(UrlNotAllowed):
            container.fetcher.execute(url, "POST", lambda: ok, Expect.JSON)
    with pytest.raises(UrlNotAllowed):
        container.fetcher.execute("https://bidplus.gem.gov.in/all-bids", "PUT", lambda: ok, Expect.HTML)


def test_execute_runs_perform_only_after_guards(container: Container, clock, settings: Settings):
    calls = []
    from gem_assistant.domain.ports import HttpResponse as R

    def perform():
        calls.append(clock.now())
        return R(url="", status=200, headers={}, body=b"<html>ok</html>")

    container.fetcher.execute(GOOD_URL, "GET", perform, Expect.HTML)
    container.fetcher.execute(GOOD_URL, "GET", perform, Expect.HTML)
    assert (calls[1] - calls[0]).total_seconds() >= settings.safety.min_interval_seconds
    assert container.safety.status().used_today == 2


def test_html_where_json_expected_trips_breaker(container: Container):
    from gem_assistant.domain.ports import HttpResponse as R
    page = R(url="", status=200, headers={}, body=b"<html>window['bobcmn']</html>")
    with pytest.raises(BlockedByGeM):
        container.fetcher.execute("https://bidplus.gem.gov.in/all-bids-data", "POST", lambda: page, Expect.JSON)
    assert container.safety.status().breaker.open


def test_no_request_leaves_until_a_contact_email_is_set(settings, clock, transport):
    from dataclasses import replace

    from gem_assistant.config import PLACEHOLDER_CONTACT
    from gem_assistant.safety.errors import MissingContactEmail

    unset = replace(settings, safety=replace(settings.safety, contact_email=PLACEHOLDER_CONTACT))
    c = Container.build(unset, clock=clock, transport=transport)
    with pytest.raises(MissingContactEmail, match="Safety page"):
        c.fetcher.get("https://bidplus.gem.gov.in/all-bids")
    with pytest.raises(MissingContactEmail):
        c.fetcher.ensure_ready()
    assert transport.calls == []
    assert c.safety.status().used_today == 0


def test_plain_text_is_judged_by_shape_not_by_words():
    robots = HttpResponse(url="", status=200, headers={}, body=b"User-agent: *\nDisallow: /captcha/\n")
    page = HttpResponse(url="", status=200, headers={}, body=b"<!DOCTYPE html><html>Request Rejected</html>")
    assert ResponseClassifier().classify(robots, Expect.TEXT).kind is ResponseKind.OK
    assert ResponseClassifier().classify(page, Expect.TEXT).kind is ResponseKind.BLOCK


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_contact_email_counts_as_unset(monkeypatch, value):
    from gem_assistant.config import PLACEHOLDER_CONTACT

    monkeypatch.setenv("GEM_CONTACT_EMAIL", value)
    assert Settings.from_env().safety.contact_email == PLACEHOLDER_CONTACT


def test_the_gap_holds_across_two_running_copies_of_the_app(settings, clock):
    """The web app and a command-line search are separate processes sharing one database:
    the second must wait for the first's request, not just for its own."""
    from .conftest import FakeTransport

    first_transport, second_transport = FakeTransport(), FakeTransport()
    first = Container.build(settings, clock=clock, transport=first_transport)
    second = Container.build(settings, clock=clock, transport=second_transport)  # started before any request
    first_transport.queue(); second_transport.queue()

    first.fetcher.get("https://bidplus.gem.gov.in/all-bids")
    second.fetcher.get("https://bidplus.gem.gov.in/all-bids")

    assert clock.slept == [settings.safety.min_interval_seconds]


def test_only_one_copy_of_the_app_talks_to_gem_at_a_time(tmp_path):
    """Two processes each hold their own thread lock; the file lock is what keeps them apart."""
    import threading
    from datetime import datetime, timezone

    from gem_assistant.safety.rate_limiter import RateLimiter
    from .conftest import FakeClock

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    a = RateLimiter(clock, 4.0, lock_path=tmp_path / "fetch.lock")
    b = RateLimiter(clock, 4.0, lock_path=tmp_path / "fetch.lock")
    entered = threading.Event()

    def second():
        with b.hold():
            entered.set()

    with a.hold():
        thread = threading.Thread(target=second)
        thread.start()
        assert not entered.wait(0.3), "the second copy got in while the first held the lock"
    assert entered.wait(2)
    thread.join()
