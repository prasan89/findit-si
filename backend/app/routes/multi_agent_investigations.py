"""Multi-agent investigation router."""
import json
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agents.orchestrator import InvestigationOrchestrator
from ..config import get_settings
from ..database import create_tables, get_db

logger = logging.getLogger(__name__)

router = APIRouter()


class MultiAgentInvestigationRequest(BaseModel):
    query: str
    recording_ids: Optional[list[str]] = None
    budget_usd: Optional[float] = None


@router.post("/multi-agent")
async def create_multi_agent_investigation(request: MultiAgentInvestigationRequest):
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

    budget = request.budget_usd if request.budget_usd is not None else settings.investigation_budget_usd

    orchestrator = InvestigationOrchestrator(
        db_path=settings.db_path,
        budget_usd=budget,
    )

    result = await orchestrator.run(
        query=request.query,
        recording_ids=request.recording_ids,
    )
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
