"""The pages a seller fills in, driven in a real browser against the real app. Never contacts GeM."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import replace

import pytest
import uvicorn

from gem_assistant.api.app import create_app
from gem_assistant.config import PLACEHOLDER_CONTACT
from gem_assistant.container import Container

from .test_listing import _chromium, browser_test

pytestmark = browser_test


@pytest.fixture
def live_app(settings, clock, transport):
    """As freshly installed: no contact email anywhere."""
    settings = replace(settings, safety=replace(settings.safety, contact_email=PLACEHOLDER_CONTACT))
    container = Container.build(settings, clock=clock, transport=transport)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(container), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", container
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def page(live_app):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chromium())
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        yield page
        browser.close()
    assert errors == []


@pytest.mark.parametrize("typed, rupees", [
    ("10,00,000", 1_000_000),
    ("10 lakh", 1_000_000),
    ("1.5 crore", 15_000_000),
    ("₹ 25,50,000", 2_550_000),
])
def test_turnover_can_be_typed_the_way_people_say_it(live_app, page, typed, rupees):
    base, container = live_app
    page.goto(base + "/#/profile")
    page.fill("#turnover", typed)
    page.click("text=Save profile")
    page.wait_for_selector("text=Saved")
    assert container.profiles.get().annual_turnover_rupees == rupees


def test_a_turnover_that_cannot_be_read_is_not_saved_as_blank(live_app, page):
    base, container = live_app
    page.goto(base + "/#/profile")
    page.fill("#turnover", "ten lakh")
    page.click("text=Save profile")
    page.wait_for_selector("text=couldn't read")
    assert container.profiles.get() is None


def test_the_first_search_asks_for_a_contact_email_on_the_page(live_app, page):
    base, container = live_app
    page.goto(base + "/#/discover")
    page.fill("#contact-email", "seller@example.com")
    page.click("text=Save email")
    page.wait_for_selector("text=Saved")
    assert "contact: seller@example.com" in container.fetcher.user_agent


def test_what_to_look_for_saves_products_ignored_words_and_order(live_app, page):
    base, container = live_app
    page.goto(base + "/#/discover")
    page.fill("#products", "fire extinguisher, office chair")
    page.fill("#ignore", "refilling, AMC")
    page.select_option("#bid-order", "closing")
    page.click("text=Save what to look for")
    page.wait_for_selector("text=fire extinguisher, office chair")
    saved = container.profiles.get()
    assert (saved.categories, saved.ignore_words, saved.bid_order) == (
        ["fire extinguisher", "office chair"], ["refilling", "AMC"], "closing")


def test_what_he_is_typing_survives_the_end_of_a_search(live_app, page):
    """The page redraws itself when a search ends; unsaved text must still be there."""
    base, container = live_app
    container.discovery._lock.acquire()  # a search is running
    page.goto(base + "/#/discover")
    page.wait_for_selector("text=Searching…")
    page.fill("#products", "fire extinguisher")
    page.select_option("#bid-order", "closing")
    container.discovery._lock.release()  # ...and ends
    page.wait_for_selector("text=Search GeM now")
    assert (page.input_value("#products"), page.input_value("#bid-order")) == ("fire extinguisher", "closing")


def test_closed_bids_are_hidden_until_asked_for(live_app, page):
    """The sample bid closed in 2025: by default the list shows only bids he can still quote on."""
    from .conftest import SAMPLE_PDF

    base, container = live_app
    container.bid_import.import_pdf(SAMPLE_PDF.read_bytes(), source_name="bid.pdf")
    page.goto(base + "/#/")
    page.wait_for_selector("text=1 closed bid hidden")
    assert page.locator("table tbody tr").count() == 0
    page.check("#show-closed")
    assert page.locator("table tbody tr").count() == 1
