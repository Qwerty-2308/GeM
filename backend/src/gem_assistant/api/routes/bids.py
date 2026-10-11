from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, UploadFile

from ...infrastructure.pypdf_extractor import InvalidPdf
from ...safety.errors import SafetyError, UnexpectedResponse, UrlNotAllowed
from ...services.bid_fetch_service import BadBidLink
from ...services.bid_import_service import EvidenceMismatch
from ..schemas import BidDetailOut, BidSummaryOut, FetchIn, FetchOut, ImportOut

router = APIRouter(prefix="/api/bids", tags=["bids"])


@router.get("", response_model=list[BidSummaryOut])
def list_bids(request: Request) -> list[BidSummaryOut]:
    return [BidSummaryOut.from_assessed(b) for b in request.app.state.container.bid_query.list()]


@router.get("/{bid_id}", response_model=BidDetailOut)
def get_bid(bid_id: int, request: Request) -> BidDetailOut:
    item = request.app.state.container.bid_query.get(bid_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Bid not found")
    return BidDetailOut.from_assessed(item)


@router.post("/import", response_model=ImportOut)
async def import_bid(file: UploadFile, request: Request) -> ImportOut:
    container = request.app.state.container
    limit = container.settings.safety.max_download_bytes
    data = await file.read(limit + 1)
    try:
        result = container.bid_import.import_pdf(data, source_name=file.filename or "upload.pdf")
    except (InvalidPdf, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ImportOut(id=result.id, already_imported=result.already_imported)


@router.post("/fetch", response_model=FetchOut)
def fetch_bid(body: FetchIn, request: Request) -> FetchOut:
    """Download one bid document from GeM by its link, through the safety layer."""
    service = request.app.state.container.bid_fetch
    try:
        result = service.fetch_from_link(body.link)
    except (BadBidLink, UrlNotAllowed) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except UnexpectedResponse as error:
        raise HTTPException(status_code=404, detail=f"GeM has no document at that link ({error}).") from error
    except SafetyError as error:  # breaker open, blocked, budget used up
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (InvalidPdf, EvidenceMismatch) as error:
        raise HTTPException(status_code=422, detail=f"Downloaded, but could not analyse it: {error}") from error
    except Exception as error:  # network trouble; logged by the safety layer, not retried
        raise HTTPException(status_code=502, detail=f"Could not reach GeM: {type(error).__name__}") from error
    return FetchOut(id=result.import_result.id, already_imported=result.import_result.already_imported,
                    downloaded=result.downloaded)
