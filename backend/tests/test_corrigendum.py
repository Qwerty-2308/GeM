"""The corrigendum check: response reading, the model invariant, what may be POSTed,
and what the person is told.

A corrigendum can move a deadline or change a specification, so the costly mistake here
is reporting "no corrigendum" when we could not actually tell.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from gem_assistant.domain.models import (
    BidAnalysis, Corrigendum, CorrigendumStatus, Evidence, ExtractedField, ExtractionMethod, FieldStatus,
)
from gem_assistant.domain.ports import HttpResponse
from gem_assistant.eligibility.rules import CorrigendumRule, DeadlineRule
from gem_assistant.infrastructure.browser_listing import PlaywrightListingSource
from gem_assistant.parsing.corrigendum_parser import (
    MAX_TEXT_CHARS, html_to_text, parse_corrigendum_response,
)
from gem_assistant.safety.errors import UrlNotAllowed
from gem_assistant.safety.response_classifier import Expect, ResponseClassifier, ResponseKind
from gem_assistant.safety.url_policy import UrlPolicy

AT = datetime(2026, 10, 5, 6, 30, tzinfo=timezone.utc)


# ---------- reading GeM's answer ----------

@pytest.mark.parametrize("body", [b"0", b"", b"  \n 0 \n ", b"   "])
def test_zero_or_empty_means_no_corrigendum(body):
    """The page's own test is `if (data == 0)`, and in JavaScript "" == 0 too."""
    result = parse_corrigendum_response(body, AT)
    assert result.status is CorrigendumStatus.NONE
    assert result.text is None and result.checked_at == AT


def test_html_fragment_becomes_readable_text():
    body = b"<div><b>Corrigendum</b> dated 01-10-2026</div><p>Bid end date extended.</p>"
    result = parse_corrigendum_response(body, AT)
    assert result.status is CorrigendumStatus.PRESENT
    assert "Corrigendum dated 01-10-2026" in result.text
    assert "Bid end date extended." in result.text
    assert "<" not in result.text


def test_entities_are_unescaped_and_scripts_dropped():
    body = b"<div>Quantity &amp; delivery changed</div><script>alert('x')</script>"
    text = html_to_text(body.decode())
    assert "Quantity & delivery changed" in text
    assert "alert" not in text


def test_markup_with_no_readable_text_is_failed_not_none():
    """A response we cannot read must never be reported as "no corrigendum"."""
    result = parse_corrigendum_response(b"<div><script>var a=1;</script></div>", AT)
    assert result.status is CorrigendumStatus.FAILED
    assert result.text is None and "Open the bid on GeM" in result.note


def test_long_corrigendum_is_shortened_and_says_so():
    body = ("<p>" + "x" * (MAX_TEXT_CHARS + 500) + "</p>").encode()
    result = parse_corrigendum_response(body, AT)
    assert result.status is CorrigendumStatus.PRESENT
    assert len(result.text) == MAX_TEXT_CHARS
    assert "read the full corrigendum on GeM" in result.note


def test_undecodable_bytes_do_not_crash():
    result = parse_corrigendum_response(b"<p>caf\xff\xfe</p>", AT)
    assert result.status is CorrigendumStatus.PRESENT


# ---------- telling a corrigendum apart from a block page ----------

def _classify(body: bytes):
    return ResponseClassifier().classify(
        HttpResponse(url="u", status=200, headers={}, body=body), expect=Expect.HTML_FRAGMENT)


@pytest.mark.parametrize("body", [
    b"<div>Corrigendum: Access denied to vendors not empanelled with AVNL.</div>",
    b"<div>Corrigendum: supply of CAPTCHA verification modules, qty revised.</div>",
    b"<div>Bidders are advised to enable javascript on the portal before bidding.</div>",
    b"0",
])
def test_buyers_wording_does_not_look_like_a_block_page(body):
    """Corrigendum text is written by government offices and can legitimately contain
    phrases that appear on WAF pages. Tripping the breaker on those would stop the whole
    tool over an ordinary sentence."""
    assert _classify(body).kind is ResponseKind.OK


@pytest.mark.parametrize("body", [
    b"<!DOCTYPE html><html><head><title>Request Rejected</title></head><body>"
    b"The requested URL was rejected. Your support ID is 123.</body></html>",
    b"<html><body>Please enable JavaScript to continue</body></html>",
])
def test_a_block_page_served_instead_of_a_corrigendum_is_still_caught(body):
    """A real challenge or block page is a whole document, never a fragment."""
    assert _classify(body).kind is ResponseKind.BLOCK


# ---------- the model invariant ----------

def test_present_requires_text():
    with pytest.raises(ValidationError, match="PRESENT requires"):
        Corrigendum(status=CorrigendumStatus.PRESENT, checked_at=AT)


@pytest.mark.parametrize("status", [CorrigendumStatus.NONE, CorrigendumStatus.NOT_CHECKED,
                                    CorrigendumStatus.FAILED])
def test_only_present_may_carry_text(status):
    with pytest.raises(ValidationError, match="must not carry"):
        Corrigendum(status=status, text="something")


def test_a_bid_defaults_to_not_checked():
    assert _bid().corrigendum.status is CorrigendumStatus.NOT_CHECKED


# ---------- what may be sent ----------

def _policy() -> UrlPolicy:
    return UrlPolicy("gem.gov.in", ("/resources/", "/bg_emd/"), ("/all-bids-data",),
                     ("/bidding/bid/viewCorrigendum",))


def test_corrigendum_post_is_allowed_with_an_id():
    _policy().check("https://bidplus.gem.gov.in/bidding/bid/viewCorrigendum/8506455", "POST")


def test_the_prefix_alone_is_not_enough():
    with pytest.raises(UrlNotAllowed, match="POST to"):
        _policy().check("https://bidplus.gem.gov.in/bidding/bid/viewCorrigendum", "POST")


def test_allowing_one_endpoint_does_not_open_the_tree_below_it():
    with pytest.raises(UrlNotAllowed, match="POST to"):
        _policy().check("https://bidplus.gem.gov.in/bidding/bid/viewCorrigendum/1/submit", "POST")


def test_a_similar_looking_path_is_not_matched():
    with pytest.raises(UrlNotAllowed, match="POST to"):
        _policy().check("https://bidplus.gem.gov.in/bidding/bid/viewCorrigendumX/1", "POST")


def test_sibling_endpoints_stay_refused():
    for url in ("https://bidplus.gem.gov.in/public-bid-other-details/8506455",
                "https://bidplus.gem.gov.in/bidding/bid/submitBid/1"):
        with pytest.raises(UrlNotAllowed, match="POST to"):
            _policy().check(url, "POST")


@pytest.mark.parametrize("path", [
    "/bidding/bid/viewCorrigendum/%2E%2E%2Fsubmit",      # decodes to ../submit
    "/bidding/bid/viewCorrigendum/%2F..%2Fbg_emd%2Fx",   # reaches the never-touch path
    "/bidding/bid/viewCorrigendum/..%2F..%2Fresources",  # robots-disallowed
    "/bidding/bid/viewCorrigendum/123%2F456",
])
def test_percent_encoding_cannot_smuggle_a_second_path_segment(path):
    """A server that decodes %2F would see a different endpoint than the one we allowed,
    so an encoded path is refused outright rather than guessed at."""
    with pytest.raises(UrlNotAllowed, match="encoded"):
        _policy().check("https://bidplus.gem.gov.in" + path, "POST")


def test_encoding_cannot_hide_a_blocked_path_on_a_get():
    with pytest.raises(UrlNotAllowed, match="encoded"):
        _policy().check("https://bidplus.gem.gov.in/%72esources/secret.pdf", "GET")


def test_corrigendum_is_not_reachable_by_get():
    with pytest.raises(UrlNotAllowed, match="listing page"):
        _policy().check("https://bidplus.gem.gov.in/bidding/bid/viewCorrigendum/8506455", "GET")


# ---------- what the person is told ----------

def _bid(corrigendum: Corrigendum | None = None) -> BidAnalysis:
    return BidAnalysis(
        bid_number="GEM/2026/B/1", source_name="x.pdf", sha256="a" * 64, page_count=1,
        unread_lines=0, imported_at=AT,
        fields=[ExtractedField(name="bid_end", label="Bid end", status=FieldStatus.NOT_FOUND)],
        flags=[], **({"corrigendum": corrigendum} if corrigendum else {}),
    )


def test_asking_about_no_bids_opens_no_browser_and_spends_no_request(container):
    """A run that analysed nothing must not still load the listing page. That page load
    is a request out of a small daily budget, spent for no answer."""
    source = PlaywrightListingSource(
        fetcher=container.fetcher,
        network=None,
        playwright_factory=lambda: pytest.fail("a browser was opened for an empty list"),
    )
    assert source.corrigenda([]) == {}
    assert container.fetcher.requests_sent == 0


def test_the_safety_page_admits_every_kind_of_post_the_policy_allows(container):
    """The Safety page is where someone checks what this tool may do. A capability the
    code allows but the page does not mention is a promise quietly broken, so this is
    keyed to the policy settings rather than to fixed wording."""
    rules = " ".join(container.safety.status().rules).lower()
    safety = container.settings.safety

    if any("viewcorrigendum" in p.lower() for p in safety.post_allowed_prefixes):
        assert "corrigendum" in rules, "corrigendum requests are allowed but not disclosed"
    if any("all-bids-data" in p for p in safety.post_allowed_paths):
        assert "search" in rules, "the listing search is allowed but not disclosed"


@pytest.mark.parametrize("status, expected", [
    (CorrigendumStatus.NONE, ""),
    (CorrigendumStatus.PRESENT, "has a corrigendum"),
    (CorrigendumStatus.NOT_CHECKED, "Not checked for a corrigendum"),
    (CorrigendumStatus.FAILED, "did not complete"),
])
def test_every_state_except_none_asks_for_a_look(status, expected):
    text = "an amendment" if status is CorrigendumStatus.PRESENT else None
    bid = _bid(Corrigendum(status=status, checked_at=AT, text=text))
    results = CorrigendumRule().evaluate(bid, None, AT)
    if not expected:
        assert results == []
    else:
        (result,) = results
        assert result.outcome.value == "NEEDS_REVIEW" and expected in result.reason


def _closed_bid(corrigendum: Corrigendum) -> BidAnalysis:
    """A bid whose document says it closed a week before AT."""
    end = ExtractedField(
        name="bid_end", label="Bid end date/time", status=FieldStatus.FOUND, critical=True,
        value="2026-09-28T17:00:00+05:30",
        evidence=Evidence(page=1, quote="/Bid End Date/Time 28-09-2026 17:00:00", method=ExtractionMethod.LABEL_PARSE),
    )
    return _bid(corrigendum).model_copy(update={"fields": [end]})


@pytest.mark.parametrize("status, outcome", [
    (CorrigendumStatus.NONE, "BLOCKED"),
    (CorrigendumStatus.PRESENT, "NEEDS_REVIEW"),
    (CorrigendumStatus.NOT_CHECKED, "NEEDS_REVIEW"),
    (CorrigendumStatus.FAILED, "NEEDS_REVIEW"),
])
def test_a_passed_deadline_only_blocks_when_gem_confirms_no_corrigendum(status, outcome):
    """Seen live on 11-Oct-2026: three bids whose PDFs said they closed on 5-7 Oct were
    still open, extended by corrigenda. The document's date is the original deadline."""
    text = "Bid end date extended" if status is CorrigendumStatus.PRESENT else None
    bid = _closed_bid(Corrigendum(status=status, checked_at=AT, text=text))
    (result,) = DeadlineRule().evaluate(bid, None, AT)
    assert result.outcome.value == outcome
    if outcome == "NEEDS_REVIEW":
        assert "may have been extended" in result.reason


def test_a_no_corrigendum_answer_from_before_the_deadline_cannot_block():
    """GeM said "no corrigendum" on 20 Sep and the document's deadline was 28 Sep. An
    extension published after that check would not be in the answer."""
    early = Corrigendum(status=CorrigendumStatus.NONE, checked_at=datetime(2026, 9, 20, tzinfo=timezone.utc))
    (result,) = DeadlineRule().evaluate(_closed_bid(early), None, AT)
    assert result.outcome.value == "NEEDS_REVIEW" and "may have been extended" in result.reason
