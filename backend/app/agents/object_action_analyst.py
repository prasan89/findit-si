"""Agent 2: ObjectActionAnalystAgent — LLM analysis of objects, actions, scene changes."""
import json
import logging
import time

from ..agents.base import AgentResult, AgentStatus, InvestigationContext
from ..agents.llm_helper import llm_json_call
from ..agents.schemas import ObjectActionAnalysis, ObjectActionReport
from ..config import get_settings

logger = logging.getLogger(__name__)

AGENT_NAME = "ObjectActionAnalystAgent"


async def run(ctx: InvestigationContext) -> AgentResult[ObjectActionAnalysis]:
    t0 = time.monotonic()
    settings = get_settings()
    events = ctx.events

    if not events:
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.SKIPPED,
            output=ObjectActionAnalysis(
                reports=[],
                events_analyzed=0,
                model_used=settings.investigation_model,
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

        result, tokens_in, tokens_out, cost = await llm_json_call(
            prompt=prompt,
            db_path=ctx.db_path,
            operation="object_action_analysis",
            model=settings.investigation_model,
        )

        reports: list[ObjectActionReport] = []
        for r in result.get("reports", []):
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
            model_used=settings.investigation_model,
        )

        ctx.budget_spent += cost

        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.COMPLETED,
            output=analysis,
            duration_ms=(time.monotonic() - t0) * 1000,
            tokens_used=tokens_in + tokens_out,
            cost_usd=cost,
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
