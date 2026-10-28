"""Multi-agent investigation router."""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agents.orchestrator import InvestigationOrchestrator
from ..config import get_settings
from ..database import create_tables

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
