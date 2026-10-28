"""Investigation router: POST an investigation query, get back a structured event timeline."""
import logging
from fastapi import APIRouter, HTTPException

from ..config import get_settings
from ..database import create_tables
from ..models import InvestigationRequest, InvestigationResult
from ..investigate import investigate

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/", response_model=InvestigationResult)
async def create_investigation(request: InvestigationRequest):
    """
    Investigate an incident across multiple CCTV recordings.

    Returns a chronological list of evidence-backed events relevant to the query.
    All timestamps are traceable to source recordings.
    """
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    settings = get_settings()
    await create_tables(settings.db_path)

    result = await investigate(
        query=request.query,
        db_path=settings.db_path,
        recording_ids=request.recording_ids,
        time_start_seconds=request.time_start_seconds,
        time_end_seconds=request.time_end_seconds,
    )
    return result
