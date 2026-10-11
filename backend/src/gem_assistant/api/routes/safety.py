from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..schemas import ResetIn, SafetyOut

router = APIRouter(prefix="/api/safety", tags=["safety"])


@router.get("", response_model=SafetyOut)
def get_safety(request: Request) -> SafetyOut:
    return SafetyOut.from_status(request.app.state.container.safety.status())


@router.post("/reset", response_model=SafetyOut)
def reset_breaker(body: ResetIn, request: Request) -> SafetyOut:
    service = request.app.state.container.safety
    try:
        service.reset(confirmed=body.confirmed, note=body.note.strip()[:500])
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return SafetyOut.from_status(service.status())
