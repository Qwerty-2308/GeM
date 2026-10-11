from __future__ import annotations

from ..domain.models import SellerProfile
from ..domain.ports import Clock, ProfileReader, ProfileWriter


class ProfileService:
    def __init__(self, reader: ProfileReader, writer: ProfileWriter, clock: Clock):
        self._reader = reader
        self._writer = writer
        self._clock = clock

    def get(self) -> SellerProfile | None:
        return self._reader.get()

    def save(self, profile: SellerProfile) -> SellerProfile:
        cleaned = profile.model_copy(update={
            "business_name": profile.business_name.strip(),
            "categories": _unique(profile.categories),
            "certifications": _unique(profile.certifications),
            "states": _unique(profile.states),
            "updated_at": self._clock.now(),
        })
        return self._writer.save(cleaned)


def _unique(items: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for item in items:
        stripped = item.strip()
        if stripped and stripped.lower() not in seen:
            seen[stripped.lower()] = stripped
    return list(seen.values())
