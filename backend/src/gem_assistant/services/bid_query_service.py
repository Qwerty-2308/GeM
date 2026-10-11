"""Reads stored bids and assesses them against the current profile and time.

Verdicts are computed on read, so editing the profile updates every bid.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import Assessment, BidAnalysis
from ..domain.ports import BidReader, Clock, ProfileReader
from ..eligibility.evaluator import EligibilityEvaluator


@dataclass(frozen=True)
class AssessedBid:
    bid: BidAnalysis
    assessment: Assessment


class BidQueryService:
    def __init__(self, bids: BidReader, profiles: ProfileReader, evaluator: EligibilityEvaluator, clock: Clock):
        self._bids = bids
        self._profiles = profiles
        self._evaluator = evaluator
        self._clock = clock

    def list(self) -> list[AssessedBid]:
        profile, now = self._profiles.get(), self._clock.now()
        return [AssessedBid(b, self._evaluator.assess(b, profile, now)) for b in self._bids.list()]

    def get(self, bid_id: int) -> AssessedBid | None:
        bid = self._bids.get(bid_id)
        if bid is None:
            return None
        return AssessedBid(bid, self._evaluator.assess(bid, self._profiles.get(), self._clock.now()))
