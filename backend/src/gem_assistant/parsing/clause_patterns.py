"""Warning patterns for buyer-added clauses (ATC).

High recall on purpose: a false alarm costs a minute of reading, a miss can
cost a bid. These flag clauses for a human to read; they decide nothing.
Add a ClausePattern to cover a new kind of clause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ClausePattern:
    category: str
    label: str
    pattern: re.Pattern[str]
    blocking_hint: bool = False  # likely to disqualify -> pushes verdict to review


def clause(category: str, label: str, regex: str, blocking_hint: bool = False) -> ClausePattern:
    return ClausePattern(category, label, re.compile(regex, re.IGNORECASE), blocking_hint)


DEFAULT_CLAUSE_PATTERNS: tuple[ClausePattern, ...] = (
    clause("restricted_sources", "Only certain bidders may be eligible",
           r"\b(invited only|only from|shall not be considered|will not be considered|not be entertained"
           r"|not eligible)\b", blocking_hint=True),
    clause("rejection", "Grounds for rejection",
           r"\b(liable to be rejected|will be rejected|shall be rejected|disqualif\w*)", blocking_hint=True),
    clause("experience", "Experience requirement", r"\b(past\s+)?experience\b", blocking_hint=True),
    clause("turnover", "Turnover requirement", r"\bturnover\b", blocking_hint=True),
    clause("oem_authorisation", "OEM / authorisation requirement",
           r"\b(OEM authori[sz]ation|authori[sz]ation certificate|authori[sz]ed (dealer|distributor)"
           r"|manufacturer'?s? authori[sz]ation)", blocking_hint=True),
    clause("certification", "Certificate or test report needed",
           r"\b(ISO\s?\d{3,5}|BIS|NABL|test reports?|type test)\b"),
    clause("emd", "Mentions EMD / bid security", r"\b(EMD|earnest money|bid security)\b"),
    clause("performance_security", "Performance security", r"\b(PSD|performance security|PBG)\b"),
    clause("penalty", "Penalty or extra charges", r"\b(penalty|liquidated damages|demurrage)\b"),
)

# GeM's own standard sentences, the same in every bid that carries them. They set no requirement
# of their own (the turnover and experience figures sit in the bid form, which the field rules
# read), so they raise no flag. Keep this to wording that only explains or lists documents.
GEM_STANDARD_WORDING = re.compile(
    r"liable to be rejected if they don'?t upload any of the certificates"
    r"|date of constitution\s*/\s*incorporation of the bidder is less than"
    r"|documentary evidence in the form of certified audited balance sheets"
    r"|any one of the following documents may be considered as valid proof",
    re.IGNORECASE)

# Means the buyer attached a separate ATC file that this tool has not read.
ATTACHMENT_MARKER = re.compile(r"Buyer uploaded ATC document", re.IGNORECASE)
