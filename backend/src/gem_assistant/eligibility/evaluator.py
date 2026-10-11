"""Runs every rule and takes the most severe outcome."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from ..domain.models import Assessment, BidAnalysis, SellerProfile, Verdict
from ..domain.ports import EligibilityRule
from .rules import DEFAULT_RULES


class EligibilityEvaluator:
    def __init__(self, rules: Sequence[EligibilityRule] = DEFAULT_RULES):
        self._rules = tuple(rules)

    def assess(self, bid: BidAnalysis, profile: SellerProfile | None, now: datetime) -> Assessment:
        results = [result for rule in self._rules for result in rule.evaluate(bid, profile, now)]
        results.sort(key=lambda r: -r.outcome.severity)  # most serious first; stable within a level
        verdict = max((r.outcome for r in results), key=lambda v: v.severity, default=Verdict.ELIGIBLE)
        return Assessment(verdict=verdict, results=results)
