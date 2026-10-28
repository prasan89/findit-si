"""Agent 2: ObjectActionAnalystAgent — LLM analysis of objects, actions, scene changes."""
import json
import logging
import time

from ..agents.base import AgentResult, AgentStatus, InvestigationContext
from ..agents.llm_helper import llm_json_call_ex
from ..agents.schemas import ObjectActionAnalysis, ObjectActionReport
from ..config import get_settings
from ..provider_config import resolve_text_spec

logger = logging.getLogger(__name__)

AGENT_NAME = "ObjectActionAnalystAgent"


async def run(ctx: InvestigationContext) -> AgentResult[ObjectActionAnalysis]:
    t0 = time.monotonic()
    settings = get_settings()
    spec = resolve_text_spec(settings)
    events = ctx.events

    if not events:
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.SKIPPED,
            output=ObjectActionAnalysis(
                reports=[],
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
                "event_type": ev.get("event_type", ""),
                "scene_description": ev.get("scene_description") or "",
            }
            for ev in events[:10]
        ], indent=2)

        prompt = f"""You are a forensic video analyst. For each detected event, identify:
1. All distinct objects present (people, vehicles, items)
2. Actions being performed (movement, interaction, entry, exit)
3. Whether a scene change occurred (new object appeared or disappeared)

Events:
{events_text}

Return ONLY valid JSON:
{{
  "reports": [
    {{
      "event_id": "<event_id>",
      "objects_identified": ["<object1>", "<object2>"],
      "actions_identified": ["<action1>"],
      "scene_change_detected": true
    }}
  ]
}}"""

        if not ctx.claim_llm_call():
            return AgentResult(
                agent_name=AGENT_NAME,
                status=AgentStatus.SKIPPED,
                output=ObjectActionAnalysis(
                    reports=[],
                    events_analyzed=0,
                    model_used=spec.model,
                ),
                duration_ms=(time.monotonic() - t0) * 1000,
                error="Investigation model-call cap reached",
            )
        call = await llm_json_call_ex(
            prompt=prompt,
            db_path=ctx.db_path,
            operation="object_action_analysis",
            model=spec.model,
            provider=spec.provider,
        )
        tokens_in, tokens_out, cost = call.tokens_in, call.tokens_out, call.cost_usd

        reports: list[ObjectActionReport] = []
        for r in call.data.get("reports", []):
            try:
                reports.append(ObjectActionReport(
                    event_id=r.get("event_id", ""),
                    objects_identified=r.get("objects_identified", []),
                    actions_identified=r.get("actions_identified", []),
                    scene_change_detected=bool(r.get("scene_change_detected", False)),
                ))
            except Exception:
                pass

        if not reports:
            for ev in events[:10]:
                reports.append(ObjectActionReport(
                    event_id=ev.get("event_id", ""),
                    objects_identified=ev.get("object_descriptions", []),
                    actions_identified=[ev.get("event_type", "").replace("_", " ")],
                    scene_change_detected=ev.get("event_type") == "scene_change",
                ))

        analysis = ObjectActionAnalysis(
            reports=reports,
            events_analyzed=len(events),
            model_used=spec.model,
        )

        ctx.budget_spent += cost

        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.MOCKED if call.mocked else AgentStatus.COMPLETED,
            output=analysis,
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
        logger.exception("ObjectActionAnalystAgent failed")
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.FAILED,
            output=None,
            duration_ms=(time.monotonic() - t0) * 1000,
            error=str(exc),
        )
