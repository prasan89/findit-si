"""Agent 7: EvidenceAuditorAgent — LLM verifies claims against source evidence, rejects unsupported ones."""
import json
import logging
import time

from ..agents.base import AgentResult, AgentStatus, InvestigationContext
from ..agents.llm_helper import llm_json_call_ex
from ..agents.schemas import AuditReport, RejectedClaim
from ..config import get_settings

logger = logging.getLogger(__name__)

AGENT_NAME = "EvidenceAuditorAgent"


async def run(ctx: InvestigationContext) -> AgentResult[AuditReport]:
    t0 = time.monotonic()
    settings = get_settings()
    events = ctx.events

    if not events:
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.SKIPPED,
            output=AuditReport(
                claims_verified=0,
                claims_rejected=0,
                rejected_items=[],
                verified_events=[],
                model_used=settings.investigation_model,
            ),
            duration_ms=0.0,
        )

    try:
        claims_to_audit = []
        for ev in events[:10]:
            ev_id = ev.get("event_id", "")
            for claim in ev.get("inferred_claims", []):
                claims_to_audit.append({
                    "event_id": ev_id,
                    "claim": claim,
                    "supporting_facts": ev.get("observed_facts", []),
                })

        if not claims_to_audit:
            verified_events = [ev.get("event_id", "") for ev in events[:10]]
            return AgentResult(
                agent_name=AGENT_NAME,
                status=AgentStatus.COMPLETED,
                output=AuditReport(
                    claims_verified=len(verified_events),
                    claims_rejected=0,
                    rejected_items=[],
                    verified_events=verified_events,
                    model_used=settings.investigation_model,
                ),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        claims_text = json.dumps(claims_to_audit, indent=2)

        prompt = f"""You are an evidence auditor for a CCTV investigation. Review each inferred claim and verify whether it is supported by the listed observed facts.

Rules:
- A claim is VERIFIED if the observed facts directly support it
- A claim is REJECTED if the observed facts do not mention the claimed detail, or if the claim goes beyond what was observed
- "observed" means explicitly stated in the facts list; do NOT assume things not mentioned

Claims to audit:
{claims_text}

Return ONLY valid JSON:
{{
  "audited": [
    {{
      "event_id": "<event_id>",
      "claim": "<the claim>",
      "verdict": "verified|rejected",
      "reason": "<one sentence explanation if rejected>"
    }}
  ]
}}"""

        if not ctx.claim_llm_call():
            return AgentResult(
                agent_name=AGENT_NAME,
                status=AgentStatus.SKIPPED,
                output=AuditReport(
                    claims_verified=0,
                    claims_rejected=0,
                    rejected_items=[],
                    verified_events=[],
                    model_used=settings.investigation_model,
                ),
                duration_ms=(time.monotonic() - t0) * 1000,
                error="Investigation model-call cap reached",
            )
        call = await llm_json_call_ex(
            prompt=prompt,
            db_path=ctx.db_path,
            operation="evidence_audit",
            model=settings.investigation_model,
        )
        result, tokens_in, tokens_out, cost = (
            call.data, call.tokens_in, call.tokens_out, call.cost_usd)

        rejected_items: list[RejectedClaim] = []
        verified_event_ids: set[str] = set()
        rejected_event_ids: set[str] = set()
        claims_verified = 0
        claims_rejected = 0

        for item in result.get("audited", []):
            ev_id = item.get("event_id", "")
            if item.get("verdict") == "rejected":
                claims_rejected += 1
                rejected_event_ids.add(ev_id)
                rejected_items.append(RejectedClaim(
                    claim=item.get("claim", ""),
                    reason=item.get("reason", "Not supported by observed facts"),
                    event_id=ev_id,
                ))
            else:
                claims_verified += 1
                verified_event_ids.add(ev_id)

        if not result.get("audited"):
            claims_verified = len(claims_to_audit)
            verified_event_ids = {c["event_id"] for c in claims_to_audit}

        final_verified = verified_event_ids - rejected_event_ids
        all_event_ids = {ev.get("event_id", "") for ev in events[:10]}
        events_no_claims = all_event_ids - {c["event_id"] for c in claims_to_audit}
        final_verified = final_verified | events_no_claims

        report = AuditReport(
            claims_verified=claims_verified,
            claims_rejected=claims_rejected,
            rejected_items=rejected_items,
            verified_events=list(final_verified),
            model_used=settings.investigation_model,
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
            model_used=settings.investigation_model,
            event_ids=[ev.get("event_id", "") for ev in events[:10]],
            retries=max(0, call.attempts - 1),
            timeouts=call.timeouts,
        )
    except Exception as exc:
        logger.exception("EvidenceAuditorAgent failed")
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.FAILED,
            output=None,
            duration_ms=(time.monotonic() - t0) * 1000,
            error=str(exc),
        )
