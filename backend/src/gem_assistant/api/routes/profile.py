from __future__ import annotations

from fastapi import APIRouter, Request

from ...domain.models import SellerProfile

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=SellerProfile | None)
def get_profile(request: Request) -> SellerProfile | None:
    return request.app.state.container.profiles.get()


@router.put("", response_model=SellerProfile)
def save_profile(profile: SellerProfile, request: Request) -> SellerProfile:
    return request.app.state.container.profiles.save(profile)
