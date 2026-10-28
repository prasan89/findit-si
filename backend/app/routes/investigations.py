"""Investigation router: POST an investigation query, get back a structured event timeline."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import get_settings
from ..database import create_tables
from ..guards import (release_investigation_slot, require_api_token,
                      try_acquire_investigation_slot)
from ..investigation_cache import (build_fingerprint, cached_or_compute, lookup,
                                   model_config_part,
                                   normalize_idempotency_key,
                                   recording_fingerprint, store)
from ..models import InvestigationRequest, InvestigationResult
from ..investigate import investigate

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/", response_model=InvestigationResult,
             dependencies=[Depends(require_api_token)])
async def create_investigation(request: InvestigationRequest, http_request: Request):
    """
    Investigate an incident across multiple CCTV recordings.

    Returns a chronological list of evidence-backed events relevant to the query.
    All timestamps are traceable to source recordings.
    """
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    settings = get_settings()
    await create_tables(settings.db_path)
    request_id = getattr(http_request.state, "request_id", None)

    # Phase 8: result cache (hits skip all work, including the slot).
    idem_key = normalize_idempotency_key(http_request.headers.get("idempotency-key"))
    ttl_s = settings.investigation_cache_ttl_s
    rec_part = await recording_fingerprint(settings.db_path, request.recording_ids)
    fingerprint = build_fingerprint(
        "standard", request.query, rec_part,
        request.time_start_seconds, request.time_end_seconds, None,
        model_config_part(settings.investigation_model, settings.vision_model))
    if ttl_s > 0:
        try:
            hit = await lookup(settings.db_path, fingerprint, ttl_s, idem_key)
        except KeyError:
            raise HTTPException(
                status_code=422,
                detail="Idempotency key already used with different parameters.")
        if hit is not None:
            hit["request_id"] = request_id
            hit["cache_hit"] = True
            return InvestigationResult(**hit)

    limit = settings.investigation_max_concurrent
    if not try_acquire_investigation_slot(limit):
        raise HTTPException(
            status_code=503,
            detail="Too many concurrent investigations. Retry shortly.",
            headers={"Retry-After": "5"},
        )
    try:
        async def _compute():
            return await investigate(
                query=request.query,
                db_path=settings.db_path,
                recording_ids=request.recording_ids,
                time_start_seconds=request.time_start_seconds,
                time_end_seconds=request.time_end_seconds,
            )

        result, _shared = await cached_or_compute(f"std:{fingerprint}", _compute)
        result = result.model_copy(deep=True)
    finally:
        release_investigation_slot(limit)
    result.request_id = request_id
    result.cache_hit = False
    if ttl_s > 0:
        stored = result.model_copy(update={"request_id": None, "cache_hit": False})
        await store(settings.db_path, fingerprint, "standard",
                    stored.model_dump(), settings.investigation_cache_max_entries,
                    idem_key)
    return result
