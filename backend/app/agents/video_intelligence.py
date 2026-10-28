"""Agent 1: VideoIntelligenceAgent — LLM-powered visual summary per event."""
import json
import logging
import time

from ..agents.base import AgentResult, AgentStatus, InvestigationContext
from ..agents.llm_helper import llm_json_call_ex
from ..agents.schemas import VisualSummary, VisualSummaryReport
from ..config import get_settings
from ..provider_config import resolve_text_spec

logger = logging.getLogger(__name__)

AGENT_NAME = "VideoIntelligenceAgent"


async def run(ctx: InvestigationContext) -> AgentResult[VisualSummaryReport]:
    t0 = time.monotonic()
    settings = get_settings()
    spec = resolve_text_spec(settings)
    events = ctx.events

    if not events:
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.SKIPPED,
            output=VisualSummaryReport(
                summaries=[],
                events_analyzed=0,
                model_used=spec.model,
            ),
            duration_ms=0.0,
        )

    try:
        events_text = json.dumps([
            {
                "event_id": ev.get("event_id", ""),
                "action_description": ev.get("action_description", ""),
                "observed_facts": ev.get("observed_facts", []),
                "object_descriptions": ev.get("object_descriptions", []),
                "timestamp_seconds": ev.get("timestamp_seconds", 0),
                "camera_name": ev.get("camera_name") or ev.get("cam_name", ""),
                "evidence_frames": [
                    f.get("frame_path", "") if isinstance(f, dict) else str(f)
                    for f in (ev.get("evidence_frames") or [])
                ],
            }
            for ev in events[:10]
        ], indent=2)

        prompt = f"""You are a CCTV investigation analyst. Review these detected events and provide a concise visual intelligence summary for each.

For each event, describe:
1. What can be visually confirmed from the observed facts
2. What the visual evidence suggests about the scene
3. Frame references in the format "recording_id@Xs" (use event_id as recording_id placeholder if unknown)

Events:
{events_text}

Return ONLY valid JSON — no prose, no markdown:
{{
  "summaries": [
    {{
      "event_id": "<event_id>",
      "visual_notes": ["<note1>", "<note2>"],
      "frame_references": ["<event_id>@<timestamp_seconds>s"]
    }}
  ]
}}"""

        if not ctx.claim_llm_call():
            return AgentResult(
                agent_name=AGENT_NAME,
                status=AgentStatus.SKIPPED,
                output=VisualSummaryReport(
                    summaries=[],
                    events_analyzed=0,
                    model_used=spec.model,
                ),
                duration_ms=(time.monotonic() - t0) * 1000,
                error="Investigation model-call cap reached",
            )
        call = await llm_json_call_ex(
            prompt=prompt,
            db_path=ctx.db_path,
            operation="video_intelligence",
            model=spec.model,
            provider=spec.provider,
        )
        tokens_in, tokens_out, cost = call.tokens_in, call.tokens_out, call.cost_usd

        summaries: list[VisualSummary] = []
        for s in call.data.get("summaries", []):
            try:
                summaries.append(VisualSummary(
                    event_id=s.get("event_id", ""),
                    visual_notes=s.get("visual_notes", []),
                    frame_references=s.get("frame_references", []),
                ))
            except Exception:
                pass

        if not summaries:
            for ev in events[:10]:
                ev_id = ev.get("event_id", "")
                summaries.append(VisualSummary(
                    event_id=ev_id,
                    visual_notes=[ev.get("action_description", "No description available")],
                    frame_references=[f"{ev_id}@{ev.get('timestamp_seconds', 0):.1f}s"],
                ))

        report = VisualSummaryReport(
            summaries=summaries,
            events_analyzed=len(events),
            model_used=spec.model,
        )

        ctx.budget_spent += cost

        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.MOCKED if call.mocked else AgentStatus.COMPLETED,
            output=report,
            duration_ms=(time.monotonic() - t0) * 1000,
            tokens_used=tokens_in + tokens_out,
            cost_usd=cost,
            tokens_in=tokens_in if not call.mocked else None,
            tokens_out=tokens_out if not call.mocked else None,
            usage_measured=call.success and not call.mocked,
            model_used=spec.model,
            provider_used=call.provider,
            event_ids=[ev.get("event_id", "") for ev in events[:10]],
            retries=max(0, call.attempts - 1),
            timeouts=call.timeouts,
        )
    except Exception as exc:
        logger.exception("VideoIntelligenceAgent failed")
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.FAILED,
            output=None,
            duration_ms=(time.monotonic() - t0) * 1000,
            error=str(exc),
        )
