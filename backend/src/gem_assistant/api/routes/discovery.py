from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ...services.discovery_service import DiscoveryNotAllowed
from ..schemas import DiscoveryOut

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


@router.get("", response_model=DiscoveryOut)
def get_discovery(request: Request) -> DiscoveryOut:
    return DiscoveryOut.from_status(request.app.state.container.discovery.status())


@router.post("/run", status_code=202)
def run_discovery(request: Request) -> JSONResponse:
    try:
        request.app.state.container.discovery.start_in_background(trigger="manual")
    except DiscoveryNotAllowed as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return JSONResponse({"started": True}, status_code=202)
