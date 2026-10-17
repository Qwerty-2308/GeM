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
    yield from _serve(Container.build(settings, clock=clock, transport=transport))


@pytest.fixture
def chat_app(settings, clock, transport):
    """The chat, with a model that proposes a product and then asks him to confirm it."""
    from .test_chat import ScriptedModel, _calls, _says

    from .test_discovery import FakeSource

    model = ScriptedModel(_calls(("propose_profile_update", {"add_products": ["fire extinguisher"]})),
                          _says("I've filled in your product. Please confirm it."))
    source = FakeSource()  # Confirm starts a search; it must never reach GeM
    for served in _serve(Container.build(settings, clock=clock, transport=transport, chat_model=model,
                                         listing_source_factory=source.bind)):
        yield (*served, source)


@pytest.fixture
def web_app(settings, clock, transport):
    """Served as a website: a password first."""
    from gem_assistant.api.auth import Login

    yield from _serve(Container.build(settings, clock=clock, transport=transport),
                      Login.build(b"s" * 32, password="correct horse battery"))


@pytest.fixture
def google_app(settings, clock, transport):
    """Served as a website with Google sign-in only."""
    from gem_assistant.api.auth import GoogleLogin, Login

    google = GoogleLogin("123.apps.googleusercontent.com", "client-secret", frozenset({"father@gmail.com"}))
    yield from _serve(Container.build(settings, clock=clock, transport=transport), Login.build(b"s" * 32, google=google))


def _serve(container, login=None):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(container, login=login), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", container
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def page():
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
    page.goto(base + "/#/bids")
    page.wait_for_selector("text=1 closed bid hidden")
    assert page.locator("table tbody tr").count() == 0
    page.check("#show-closed")
    assert page.locator("table tbody tr").count() == 1


def test_the_chat_is_the_only_screen_and_a_profile_change_waits_for_his_click(chat_app, page):
    base, container, source = chat_app
    page.goto(base + "/")
    assert page.locator("nav a").count() == 0
    page.fill("#chat-input", "I sell fire extinguishers")
    page.click("text=Send")
    page.wait_for_selector("text=Please confirm it.")
    assert container.profiles.get() is None and source.calls == []  # proposed, not saved, not searched
    page.click("text=Confirm")
    page.wait_for_selector("text=Saved")
    assert container.profiles.get().categories == ["fire extinguisher"]
    # He asked for bids of a product, so confirming it starts that search: no second request needed.
    page.wait_for_selector("text=Found 2 bids", timeout=15000)
    assert source.calls == [(["extinguisher"], 2)]  # the telling word of "fire extinguisher"
    page.reload()
    page.wait_for_selector("text=I sell fire extinguishers")  # the conversation is still there


def test_a_bid_he_downloaded_himself_is_checked_in_the_chat(chat_app, page):
    """Invite-only bids need his own GeM login: he downloads the PDF as usual and drops it in here."""
    from .conftest import SAMPLE_PDF
    from .test_chat import _says

    base, container, source = chat_app
    container.chat_model.replies[:] = [_says("Checked the bid you added.")]  # it never has to open it
    page.goto(base + "/")
    page.set_input_files("#bid-file", str(SAMPLE_PDF))
    page.wait_for_selector("text=Checked the bid you added.")
    assert [b.bid.bid_number for b in container.bid_query.list()] == ["GEM/2025/B/6819518"]
    assert page.locator(".card", has_text="GEM/2025/B/6819518").count() == 1  # the bid's card, with its reasons
    opened = container.chat_model.sent[0][-1]
    assert opened["role"] == "tool" and "GEM/2025/B/6819518" in opened["content"]  # read before it answers
    assert source.calls == []  # GeM is never contacted


def test_todays_automatic_search_greets_him_in_the_chat(chat_app, page, clock):
    from datetime import datetime, timezone

    from gem_assistant.domain.models import SellerProfile

    base, container, source = chat_app
    container.profiles.save(SellerProfile(categories=["fire extinguisher"]))
    clock.current = datetime.now(timezone.utc)  # the page compares with today's real date
    assert container.daily_search.run_if_due().startswith("[ok]")
    page.goto(base + "/")
    page.wait_for_selector(".notice:has-text(\"Today's automatic search\")")
    assert "Found 2 bids" in page.locator(".notice", has_text="automatic search").inner_text()
    sent = container.chat_model
    sent.replies[:] = [{"role": "assistant", "content": "ok"}]
    page.fill("#chat-input", "do you search by yourself?")
    page.click("text=Send")
    page.wait_for_selector(".msg.bot:has-text('ok')")
    assert "every day at 09:00 India time" in sent.sent[-1][0]["content"]  # the model is told


def test_a_chat_error_is_shown_in_plain_words(chat_app, page):
    from gem_assistant.infrastructure.groq_chat import ChatModelError

    base, container, _ = chat_app

    def busy(messages, tools):
        raise ChatModelError("Groq is busy right now (rate limit). Wait a minute and ask again.")

    container.chat_model.complete = busy
    page.goto(base + "/")
    page.fill("#chat-input", "hi")
    page.click("text=Send")
    page.wait_for_selector(".notice.bad:has-text('Groq is busy right now')")


def test_the_models_markdown_shows_as_formatting_never_as_html(chat_app, page):
    from .test_chat import _says

    base, container, _ = chat_app
    container.chat_model.replies[:] = [_says(
        "## Your bids\n**Two** to check:\n\n| Bid | Verdict |\n|---|:---:|\n| GEM/1 | NEEDS_REVIEW |\n"
        "| <img src=x onerror=alert(1)> | BLOCKED |\n\n- read page 7")]
    page.goto(base + "/")
    page.fill("#chat-input", "my bids?")
    page.click("text=Send")
    bubble = page.locator(".msg.bot", has_text="Two")
    bubble.wait_for()
    assert bubble.locator("table tbody tr").count() == 2 and bubble.locator("strong").inner_text() == "Two"
    assert "|" not in bubble.inner_text() and "##" not in bubble.inner_text() and "• read page 7" in bubble.inner_text()
    assert bubble.locator("img").count() == 0 and "<img" in bubble.locator("td").nth(2).inner_text()


def test_after_a_safety_stop_resuming_is_one_click_from_the_chat(chat_app, page, transport):
    from gem_assistant.safety.errors import BlockedByGeM

    from .test_safe_fetcher import GOOD_URL

    base, container, _ = chat_app
    transport.queue(status=403)
    with pytest.raises(BlockedByGeM):
        container.fetcher.get(GOOD_URL)
    page.goto(base + "/")
    page.click("text=Fetching stopped")
    page.wait_for_selector("text=Resume fetching")


def test_a_fresh_install_says_what_to_set_up_first(live_app, page, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    base, _ = live_app
    page.goto(base + "/")
    page.wait_for_selector("text=set-groq-key")
    page.wait_for_selector("#contact-email")  # GeM needs to be able to reach him before any search


def test_the_website_asks_for_the_password_first(web_app, page):
    base, _ = web_app
    page.goto(base + "/")
    page.fill("#password", "a wrong guess")
    page.click("text=Log in")
    page.wait_for_selector("text=Wrong password")
    page.fill("#password", "correct horse battery")
    page.click("text=Log in")
    page.wait_for_selector("#chat-input")
    page.click("text=Log out")
    page.wait_for_selector("#password")


def test_the_website_offers_google_sign_in_and_explains_a_refusal(google_app, page):
    base, _ = google_app
    page.goto(base + "/")
    button = page.locator("text=Sign in with Google")
    assert button.get_attribute("href") == "/api/auth/google"
    assert page.locator("#password").count() == 0  # no password login configured
    page.goto(base + "/?login_error=not_allowed")
    page.wait_for_selector("text=isn't allowed")
