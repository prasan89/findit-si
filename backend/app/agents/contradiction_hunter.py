"""Agent 6: ContradictionHunterAgent — LLM finds inconsistencies or confirms no contradictions."""
import json
import logging
import time

from ..agents.base import AgentResult, AgentStatus, InvestigationContext
from ..agents.llm_helper import llm_json_call
from ..agents.schemas import ContradictionItem, ContradictionReport
from ..config import get_settings

logger = logging.getLogger(__name__)

AGENT_NAME = "ContradictionHunterAgent"


async def run(ctx: InvestigationContext) -> AgentResult[ContradictionReport]:
    t0 = time.monotonic()
    settings = get_settings()
    events = ctx.events

    if not events:
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.SKIPPED,
            output=ContradictionReport(
                contradictions_found=False,
                items=[],
                alternative_explanations=["No events to analyze for contradictions"],
                evidence_gaps=["No events available"],
                model_used=settings.investigation_model,
            ),
            duration_ms=0.0,
        )

    try:
        temporal_result = ctx.agent_outputs.get("temporal_report")
        inconsistencies = []
        if temporal_result and hasattr(temporal_result, "inconsistencies"):
            inconsistencies = temporal_result.inconsistencies

        events_text = json.dumps([
            {
                "event_id": ev.get("event_id", ""),
                "action_description": ev.get("action_description", ""),
                "observed_facts": ev.get("observed_facts", []),
                "inferred_claims": ev.get("inferred_claims", []),
                "timestamp_seconds": ev.get("timestamp_seconds", 0),
                "camera_name": ev.get("camera_name") or ev.get("cam_name", ""),
                "confidence": ev.get("confidence", 0.5),
            }
            for ev in events[:10]
        ], indent=2)

        inconsistencies_text = json.dumps(inconsistencies) if inconsistencies else "[]"

        prompt = f"""You are a forensic evidence analyst. Review these CCTV investigation events for contradictions and inconsistencies.

Look for:
1. Events with conflicting action descriptions or inferred claims
2. Timeline inconsistencies (events out of sequence, impossible transitions)
3. Evidence gaps (claims made without supporting observations)
4. Alternative explanations for the same observations

Pre-detected timestamp inconsistencies: {inconsistencies_text}

Events:
{events_text}

Return ONLY valid JSON. If no contradictions found, return contradictions_found: false with empty items:
{{
  "contradictions_found": false,
  "items": [
    {{
      "description": "<what contradicts what>",
      "event_ids": ["<event_id1>", "<event_id2>"],
      "severity": "high|medium|low"
    }}
  ],
  "alternative_explanations": ["<explanation1>"],
  "evidence_gaps": ["<gap1>"]
}}"""

        result, tokens_in, tokens_out, cost = await llm_json_call(
            prompt=prompt,
            db_path=ctx.db_path,
            operation="contradiction_hunting",
            model=settings.investigation_model,
        )

        items: list[ContradictionItem] = []
        for item in result.get("items", []):
            try:
                items.append(ContradictionItem(
                    description=item.get("description", ""),
                    event_ids=item.get("event_ids", []),
                    severity=item.get("severity", "low"),
                ))
            except Exception:
                pass

        if inconsistencies and not items:
            for incons in inconsistencies:
                items.append(ContradictionItem(
                    description=incons,
                    event_ids=[],
                    severity="medium",
                ))

        report = ContradictionReport(
            contradictions_found=bool(result.get("contradictions_found", len(items) > 0)),
            items=items,
            alternative_explanations=result.get("alternative_explanations", []),
            evidence_gaps=result.get("evidence_gaps", []),
            model_used=settings.investigation_model,
        )

        ctx.budget_spent += cost

        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.COMPLETED,
            output=report,
            duration_ms=(time.monotonic() - t0) * 1000,
            tokens_used=tokens_in + tokens_out,
            cost_usd=cost,
        )
    except Exception as exc:
        logger.exception("ContradictionHunterAgent failed")
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.FAILED,
            output=None,
            duration_ms=(time.monotonic() - t0) * 1000,
            error=str(exc),
        )
