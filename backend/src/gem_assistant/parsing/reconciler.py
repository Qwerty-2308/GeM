"""Cross-checks structured fields against buyer clauses.

GeM's own terms say buyers sometimes demand things in the ATC that contradict
the structured bid form (e.g. EMD = No, but a clause asks for EMD). When that
happens the field becomes CONFLICTING. We never silently pick one side.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from ..domain.models import ClauseFlag, ExtractedField, FieldStatus


class ConflictCheck(Protocol):
    def apply(self, fields: dict[str, ExtractedField], flags: Sequence[ClauseFlag]) -> None: ...


# "Bidders can also submit the EMD with a Demand Draft..." (GeM's "Forms of EMD and PBG") and
# exemption wording explain how EMD would be paid; they don't ask for it. An amount always does.
_EXPLAINS = re.compile(r"\b(can|may)\s+(also\s+)?submit\b|\bexempt", re.IGNORECASE)
_AMOUNT = re.compile(r"(\brs\b\.?|\binr\b|₹)\s*\d", re.IGNORECASE)


def _demands(quote: str) -> bool:
    return bool(_AMOUNT.search(quote)) or not _EXPLAINS.search(quote)


@dataclass(frozen=True)
class FieldSaysNoButClauseMentions:
    """Field value 'No' + any clause of `flag_category` => CONFLICTING."""

    field_name: str
    flag_category: str
    description: str

    def apply(self, fields: dict[str, ExtractedField], flags: Sequence[ClauseFlag]) -> None:
        field = fields.get(self.field_name)
        if field is None or field.status is not FieldStatus.FOUND or field.value != "No":
            return
        clash = next((f for f in flags if f.category == self.flag_category and _demands(f.evidence.quote)), None)
        if clash is None:
            return
        fields[self.field_name] = field.model_copy(update={
            "status": FieldStatus.CONFLICTING,
            "note": f"{self.description} Clause on page {clash.evidence.page}: \"{clash.evidence.quote}\"",
        })


DEFAULT_CONFLICT_CHECKS: tuple[ConflictCheck, ...] = (
    FieldSaysNoButClauseMentions(
        "emd_required", "emd", "Bid form says EMD is not required, but a buyer clause mentions EMD."),
    FieldSaysNoButClauseMentions(
        "epbg_required", "performance_security",
        "Bid form says ePBG is not required, but a buyer clause mentions performance security."),
)


class RuleBasedReconciler:
    def __init__(self, checks: Sequence[ConflictCheck] = DEFAULT_CONFLICT_CHECKS):
        self._checks = tuple(checks)

    def reconcile(self, fields: list[ExtractedField], flags: list[ClauseFlag]) -> list[ExtractedField]:
        by_name = {f.name: f for f in fields}
        for check in self._checks:
            check.apply(by_name, flags)
        return [by_name[f.name] for f in fields]
