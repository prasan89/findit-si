"""Multi-agent investigation router."""
import copy
import json
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from ..agents.orchestrator import InvestigationOrchestrator
from ..config import get_settings
from ..database import create_tables, get_db
from ..guards import (release_investigation_slot, require_api_token,
                      try_acquire_investigation_slot)
from ..investigation_cache import (build_fingerprint, cached_or_compute, lookup,
                                   model_config_part,
                                   normalize_idempotency_key,
                                   recording_fingerprint, store)

logger = logging.getLogger(__name__)

router = APIRouter()


class MultiAgentInvestigationRequest(BaseModel):
    query: str
    recording_ids: Optional[list[str]] = None
    budget_usd: Optional[float] = None

    @field_validator("query")
    @classmethod
    def query_bounded(cls, v: str) -> str:
        try:
            max_chars = get_settings().max_query_chars
        except Exception:
            max_chars = 2000
        if len(v) > max_chars:
            raise ValueError(f"query exceeds {max_chars} characters")
        return v

    @field_validator("recording_ids")
    @classmethod
    def recording_ids_bounded(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        try:
            max_ids = get_settings().max_recording_ids
        except Exception:
            max_ids = 50
        if len(v) > max_ids:
            raise ValueError(f"recording_ids exceeds {max_ids} entries")
        return v

    @field_validator("budget_usd")
    @classmethod
    def budget_bounded(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if v < 0 or v > 1000:
            raise ValueError("budget_usd must be within 0..1000")
        return v


def _is_cacheable(result: dict) -> bool:
    """Phase 8: only cache results without transient agent failures.

    Budget-skipped agents are deterministic for the same budget and cache
    fine; failed/timed_out agents indicate transient trouble — caching them
    would mask recovery, so those results are recomputed every time.
    """
    for agent in result.get("agents", []):
        if agent.get("status") in ("failed", "timed_out"):
            return False
    return result.get("final_report") is not None


@router.post("/multi-agent", dependencies=[Depends(require_api_token)])
async def create_multi_agent_investigation(
    request: MultiAgentInvestigationRequest, http_request: Request
):
    """
    Run an 8-agent investigation pipeline for a query.

    Executes 8 specialist agents in sequence:
    1. QueryInvestigationAgent — retrieves ranked evidence
    2. VideoIntelligenceAgent — LLM visual summary per event
    3. ObjectActionAnalystAgent — LLM object/action identification
    4. TemporalReasoningAgent — timeline normalization and gap detection
    5. CrossCameraCorrelationAgent — cross-camera candidate links
    6. ContradictionHunterAgent — LLM inconsistency detection
    7. EvidenceAuditorAgent — LLM claim verification
    8. ReportSynthesisAgent — final narrative from verified evidence

    Returns the execution trace including per-agent status, duration, and cost.
    """
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    settings = get_settings()
    await create_tables(settings.db_path)
    request_id = getattr(http_request.state, "request_id", None)

    budget = request.budget_usd if request.budget_usd is not None else settings.investigation_budget_usd

    # Phase 8: result cache (hits skip all work, including the slot).
    idem_key = normalize_idempotency_key(http_request.headers.get("idempotency-key"))
    ttl_s = settings.investigation_cache_ttl_s
    rec_part = await recording_fingerprint(settings.db_path, request.recording_ids)
    fingerprint = build_fingerprint(
        "multi_agent", request.query, rec_part, None, None, budget,
        model_config_part(settings.investigation_model, settings.vision_model,
                          settings.investigation_max_llm_calls))
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
            return hit

    orchestrator = InvestigationOrchestrator(
        db_path=settings.db_path,
        budget_usd=budget,
    )

    limit = settings.investigation_max_concurrent
    if not try_acquire_investigation_slot(limit):
        raise HTTPException(
            status_code=503,
            detail="Too many concurrent investigations. Retry shortly.",
            headers={"Retry-After": "5"},
        )
    try:
        async def _compute():
            return await orchestrator.run(
                query=request.query,
                recording_ids=request.recording_ids,
            )

        result, _shared = await cached_or_compute(f"ma:{fingerprint}", _compute)
        result = copy.deepcopy(result)
    finally:
        release_investigation_slot(limit)
    result["request_id"] = request_id
    result["cache_hit"] = False
    if ttl_s > 0 and _is_cacheable(result):
        stored = dict(result)
        stored["request_id"] = None
        stored["cache_hit"] = False
        await store(settings.db_path, fingerprint, "multi_agent",
                    stored, settings.investigation_cache_max_entries,
                    idem_key)
    return result


def _load_json(value, default):
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _trace_row_to_dict(row) -> dict:
    """Serialize a stored trace row. Tolerates pre-Phase-5 databases."""
    record = dict(row)
    return {
        "trace_id": record.get("trace_id"),
        "investigation_id": record.get("investigation_id"),
        "query": record.get("query"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "total_duration_ms": record.get("total_duration_ms"),
        "total_cost_usd": record.get("total_cost_usd"),
        "agent_results": _load_json(record.get("agent_results"), []),
        "final_report": _load_json(record.get("final_report"), None),
        "recording_ids": _load_json(record.get("recording_ids"), []),
        "status": record.get("status"),
        "code_revision": record.get("code_revision"),
        "model_config": _load_json(record.get("model_config"), None),
        "usage_summary": _load_json(record.get("usage_summary"), None),
    }


@router.get("/traces")
async def list_traces(limit: int = 20):
    """List recent multi-agent investigation traces (newest first)."""
    settings = get_settings()
    await create_tables(settings.db_path)

    limit = max(1, min(limit, 100))
    async with get_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            rows = await (await db.execute(
                "SELECT * FROM investigation_traces ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )).fetchall()
        except Exception:
            rows = []
    return {"traces": [_trace_row_to_dict(r) for r in rows]}


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    """Fetch one persisted investigation trace for audit."""
    settings = get_settings()
    await create_tables(settings.db_path)

    async with get_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM investigation_traces WHERE trace_id = ?",
            (trace_id,),
        )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Trace not found.")
    return _trace_row_to_dict(row)
