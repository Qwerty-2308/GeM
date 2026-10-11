"""Collects real bid documents for parser work, and checks once whether the buyer
attachments they link to open without login.

Why it exists: the parser was written against a single bid PDF, and new labels may only
come from real documents (docs/02-RECON-FINDINGS.md). A corpus run is a discovery run
(trigger "corpus") that downloads more documents than usual, chosen for variety, and
keeps them on disk with a manifest. Every request still goes through SafeFetcher.
"""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from ..domain.models import ListingEntry

# Hard ceilings for this one command. Constants, not settings, so nothing can raise them.
CORPUS_MAX_DOWNLOADS = 30
CORPUS_MAX_CORRIGENDA = 10


def pick_varied(entries: Sequence[ListingEntry], limit: int) -> list[ListingEntry]:
    """Up to `limit` entries: round-robin across keywords and, within a keyword, the
    entry whose ministry has been picked least so far. Ties keep the input order
    (soonest-closing first)."""
    pools: dict[str, list[ListingEntry]] = {}
    for entry in entries:
        pools.setdefault(entry.keyword, []).append(entry)
    per_ministry: Counter[str | None] = Counter()
    picked: list[ListingEntry] = []
    while len(picked) < limit and any(pools.values()):
        for pool in pools.values():
            if not pool or len(picked) == limit:
                continue
            best = min(pool, key=lambda e: per_ministry[e.ministry])
            pool.remove(best)
            per_ministry[best.ministry] += 1
            picked.append(best)
    return picked
