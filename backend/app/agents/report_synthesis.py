"""Agent 8: ReportSynthesisAgent — assembles final report from verified evidence."""
import logging
import time

from ..agents.base import AgentResult, AgentStatus, InvestigationContext
from ..agents.schemas import AuditReport, ClaimEvidence, EvidenceBundle, FinalReport
from ..investigate import _build_narrative

logger = logging.getLogger(__name__)

AGENT_NAME = "ReportSynthesisAgent"


async def run(
    ctx: InvestigationContext,
    audit_report: AuditReport | None,
    evidence_bundle: EvidenceBundle | None,
    agents_run: list[str],
) -> AgentResult[FinalReport]:
    t0 = time.monotonic()
    try:
        investigation_result = ctx.agent_outputs.get("investigation_result")
        ts_reliable = False
        if investigation_result:
            ts_reliable = investigation_result.timestamp_ordering_reliable

        verified_event_ids: set[str] = set()
        if audit_report and audit_report.verified_events:
            verified_event_ids = set(audit_report.verified_events)

        verified_events = [
            ev for ev in ctx.events
            if ev.get("event_id") in verified_event_ids or not verified_event_ids
        ]

        narrative = _build_narrative(
            ctx.query,
            [ctx.agent_outputs["investigation_result"].events[i]
             for i in range(len(ctx.agent_outputs["investigation_result"].events))
             if ctx.agent_outputs["investigation_result"].events[i].event_id in verified_event_ids
             or not verified_event_ids]
            if investigation_result else [],
            ts_reliable,
        )

        key_findings: list[str] = []
        if evidence_bundle and evidence_bundle.ranked_events:
            for rev in evidence_bundle.ranked_events[:5]:
                key_findings.append(
                    f"{rev.action_description} "
                    f"(score={rev.score:.2f}, t={rev.timestamp_seconds:.1f}s)"
                )

        uncertainty_notes: list[str] = []
        if investigation_result:
            if investigation_result.uncertainty_summary:
                uncertainty_notes.append(investigation_result.uncertainty_summary)
        if audit_report and audit_report.claims_rejected > 0:
            uncertainty_notes.append(
                f"{audit_report.claims_rejected} claim(s) could not be verified "
                f"against source evidence"
            )

        evidence_citations: list[str] = []
        claim_evidence: list[ClaimEvidence] = []
        for ev in verified_events[:10]:
            ev_id = ev.get("event_id", "?")
            rec_id = ev.get("recording_id", "?")
            ts = ev.get("timestamp_seconds", 0.0)
            evidence_citations.append(f"{ev_id}@{rec_id}:{ts:.1f}s")
            # Phase 5: minimal claim-to-evidence provenance. Each included
            # claim (here: the event's action description plus its observed
            # facts) points at its source event and evidence frames.
            refs = []
            for frame in ev.get("evidence_frames", []) or []:
                if isinstance(frame, dict):
                    fts = frame.get("timestamp_seconds", ts)
                    try:
                        fts_f = float(fts)
                    except (TypeError, ValueError):
                        fts_f = ts
                    refs.append(f"{ev_id}@{rec_id}:{fts_f:.1f}s")
            claim_evidence.append(ClaimEvidence(
                claim=str(ev.get("action_description", "")),
                event_ids=[ev_id],
                evidence_refs=refs or [f"{ev_id}@{rec_id}:{ts:.1f}s"],
                verified=(ev_id in verified_event_ids) if verified_event_ids else None,
            ))

        from ..config import get_settings
        report = FinalReport(
            narrative=narrative,
            key_findings=key_findings,
            uncertainty_notes=uncertainty_notes,
            evidence_citations=evidence_citations,
            model_used=get_settings().investigation_model,
            agents_run=agents_run,
            claim_evidence=claim_evidence,
        )

        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.COMPLETED,
            output=report,
            duration_ms=(time.monotonic() - t0) * 1000,
            event_ids=[ev.get("event_id", "") for ev in verified_events[:10]],
            evidence_ids=list(evidence_citations),
        )
    except Exception as exc:
        logger.exception("ReportSynthesisAgent failed")
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.FAILED,
            output=None,
            duration_ms=(time.monotonic() - t0) * 1000,
            error=str(exc),
        )
