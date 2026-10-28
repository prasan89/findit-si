"""Agent 5: QueryInvestigationAgent — wraps existing investigate() retrieval engine."""
import logging
import time

from ..agents.base import AgentResult, AgentStatus, InvestigationContext
from ..agents.schemas import EvidenceBundle, RankedEvent
from ..investigate import investigate

logger = logging.getLogger(__name__)

AGENT_NAME = "QueryInvestigationAgent"


async def run(ctx: InvestigationContext) -> AgentResult[EvidenceBundle]:
    t0 = time.monotonic()
    try:
        result = await investigate(
            query=ctx.query,
            db_path=ctx.db_path,
            recording_ids=ctx.recording_ids or None,
        )

        ranked_events = [
            RankedEvent(
                event_id=ev.event_id,
                recording_id=ev.recording_id,
                score=ev.confidence,
                timestamp_seconds=ev.timestamp_seconds,
                action_description=ev.action_description,
            )
            for ev in result.events
        ]

        bundle = EvidenceBundle(
            query=ctx.query,
            ranked_events=ranked_events,
            narrative_draft=result.narrative,
            evidence_gaps=result.evidence_gaps,
            total_searched=result.searched_recordings,
        )

        ctx.agent_outputs["investigation_result"] = result
        for ev in result.events:
            ev_dict = ev.model_dump()
            ev_dict.setdefault("rec_dt", ev_dict.get("timestamp_absolute"))
            ctx.events.append(ev_dict)

        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.COMPLETED,
            output=bundle,
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as exc:
        logger.exception("QueryInvestigationAgent failed")
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.FAILED,
            output=None,
            duration_ms=(time.monotonic() - t0) * 1000,
            error=str(exc),
        )
