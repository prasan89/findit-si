"""
InvestigationOrchestrator — runs all 8 specialist agents sequentially,
enforces budget, persists execution trace.
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from .base import AgentResult, AgentStatus, InvestigationContext
from .schemas import FinalReport
from ..database import get_db
from ..provider import BudgetExceededError, get_total_spent, get_usage_summary

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _code_revision() -> Optional[str]:
    """Best-effort git revision for traceability; None when unavailable."""
    try:
        import subprocess
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return None


class InvestigationOrchestrator:
    def __init__(self, db_path: str, budget_usd: float = 2.0):
        self.db_path = db_path
        self.budget_usd = budget_usd

    async def run(
        self,
        query: str,
        recording_ids: Optional[list[str]] = None,
    ) -> dict:
        """
        Execute the 8-agent pipeline and return a serializable result dict.
        Always returns; individual agent failures are recorded but do not abort the pipeline.
        """
        from . import (
            video_intelligence,
            object_action_analyst,
            temporal_reasoning,
            cross_camera_correlation,
            query_investigation,
            contradiction_hunter,
            evidence_auditor,
            report_synthesis,
        )

        trace_id = str(uuid.uuid4())
        investigation_id = str(uuid.uuid4())
        started_at = _now()
        t0_total = time.monotonic()

        ctx = InvestigationContext(
            query=query,
            db_path=self.db_path,
            recording_ids=list(recording_ids or []),
            budget_usd=self.budget_usd,
        )

        await self._persist_trace(trace_id, investigation_id, query,
                                   started_at, recording_ids or [], "RUNNING")

        results: list[AgentResult] = []

        # Stage 1: QueryInvestigationAgent — populate ctx.events first
        r5 = await self._run_agent("QueryInvestigationAgent", query_investigation.run, ctx)
        results.append(r5)

        # Stage 2-3: VideoIntelligenceAgent + ObjectActionAnalystAgent (LLM, parallel-ish but sequential for budget)
        for agent_name, agent_module in [
            ("VideoIntelligenceAgent", video_intelligence),
            ("ObjectActionAnalystAgent", object_action_analyst),
        ]:
            if self._budget_ok(ctx):
                r = await self._run_agent(agent_name, agent_module.run, ctx)
            else:
                r = AgentResult(
                    agent_name=agent_name,
                    status=AgentStatus.SKIPPED,
                    output=None,
                    duration_ms=0.0,
                    error="Budget limit reached",
                )
            results.append(r)

        # Stage 4: TemporalReasoningAgent (non-LLM)
        r3 = await self._run_agent("TemporalReasoningAgent", temporal_reasoning.run, ctx)
        results.append(r3)
        if r3.output is not None:
            ctx.agent_outputs["temporal_report"] = r3.output

        # Stage 5: CrossCameraCorrelationAgent (non-LLM)
        r4 = await self._run_agent("CrossCameraCorrelationAgent", cross_camera_correlation.run, ctx)
        results.append(r4)

        # Stage 6: ContradictionHunterAgent (LLM)
        if self._budget_ok(ctx):
            r6 = await self._run_agent("ContradictionHunterAgent", contradiction_hunter.run, ctx)
        else:
            r6 = AgentResult(
                agent_name="ContradictionHunterAgent",
                status=AgentStatus.SKIPPED,
                output=None,
                duration_ms=0.0,
                error="Budget limit reached",
            )
        results.append(r6)

        # Stage 7: EvidenceAuditorAgent (LLM)
        if self._budget_ok(ctx):
            r7 = await self._run_agent("EvidenceAuditorAgent", evidence_auditor.run, ctx)
        else:
            r7 = AgentResult(
                agent_name="EvidenceAuditorAgent",
                status=AgentStatus.SKIPPED,
                output=None,
                duration_ms=0.0,
                error="Budget limit reached",
            )
        results.append(r7)

        # Stage 8: ReportSynthesisAgent (non-LLM, always runs)
        agents_run = [
            r.agent_name for r in results if r.status == AgentStatus.COMPLETED
        ]
        r8 = await report_synthesis.run(
            ctx=ctx,
            audit_report=r7.output,
            evidence_bundle=r5.output,
            agents_run=agents_run,
        )
        results.append(r8)

        total_duration_ms = (time.monotonic() - t0_total) * 1000
        total_cost = sum(r.cost_usd for r in results)

        final_report_dict = None
        if r8.output:
            final_report_dict = r8.output.model_dump()

        trace_status = "COMPLETED"
        if any(r.status == AgentStatus.FAILED for r in results):
            trace_status = "PARTIAL"

        # Phase 5: measured provider usage + run configuration for the trace.
        # Usage rows are provider-reported (measured); the model_usage table
        # holds the per-call detail — the trace stores the run-level summary.
        try:
            by_model = await get_usage_summary(self.db_path)
            total_spent = await get_total_spent(self.db_path)
        except Exception:
            by_model, total_spent = [], 0.0
        measured_tokens_in = sum(int(row.get("tokens_in") or 0) for row in by_model)
        measured_tokens_out = sum(int(row.get("tokens_out") or 0) for row in by_model)
        usage_summary = {
            "measured": True,
            "total_spent_usd": round(total_spent, 6),
            "tokens_in_total": measured_tokens_in,
            "tokens_out_total": measured_tokens_out,
            "by_model": by_model,
            "note": ("Provider-reported usage from the model_usage table. "
                     "Covers all recorded calls, not only this trace."),
        }
        try:
            from ..config import get_settings as _get_settings
            _settings = _get_settings()
            model_config = {
                "investigation_model": _settings.investigation_model,
                "vision_model": _settings.vision_model,
                "vision_provider": _settings.vision_provider,
                "budget_usd": self.budget_usd,
            }
        except Exception:
            model_config = {"budget_usd": self.budget_usd}
        code_revision = _code_revision()
        retrieved_event_ids = sorted({eid for r in results for eid in r.event_ids})
        retrieved_evidence_ids = sorted({eid for r in results for eid in r.evidence_ids})
        # Phase 5: full retrieved events for scoring/audit (response only —
        # the persisted trace references them by ID to avoid duplication).
        retrieved_events: list[dict] = []
        _inv_result = ctx.agent_outputs.get("investigation_result")
        if _inv_result is not None:
            for _ev in _inv_result.events:
                retrieved_events.append(
                    _ev.model_dump() if hasattr(_ev, "model_dump") else dict(_ev))

        await self._persist_trace(
            trace_id, investigation_id, query, started_at,
            recording_ids or [],
            trace_status,
            finished_at=_now(),
            total_duration_ms=total_duration_ms,
            total_cost_usd=total_cost,
            agent_results=results,
            final_report=final_report_dict,
            code_revision=code_revision,
            model_config=model_config,
            usage_summary=usage_summary,
        )

        return {
            "trace_id": trace_id,
            "investigation_id": investigation_id,
            "query": query,
            "agents": [r.to_dict() for r in results],
            "final_report": final_report_dict,
            "total_cost_usd": round(total_cost, 6),
            "total_duration_ms": round(total_duration_ms, 1),
            "status": trace_status,
            "code_revision": code_revision,
            "model_config": model_config,
            "usage_summary": usage_summary,
            "retrieved_event_ids": retrieved_event_ids,
            "retrieved_evidence_ids": retrieved_evidence_ids,
            "retrieved_events": retrieved_events,
        }

    def _budget_ok(self, ctx: InvestigationContext) -> bool:
        return ctx.budget_spent < ctx.budget_usd

    async def _run_agent(self, name: str, fn, ctx: InvestigationContext) -> AgentResult:
        try:
            return await fn(ctx)
        except BudgetExceededError as exc:
            return AgentResult(
                agent_name=name,
                status=AgentStatus.SKIPPED,
                output=None,
                duration_ms=0.0,
                error=f"Budget exceeded: {exc}",
            )
        except Exception as exc:
            logger.exception("Agent %s raised an unhandled exception", name)
            return AgentResult(
                agent_name=name,
                status=AgentStatus.FAILED,
                output=None,
                duration_ms=0.0,
                error=str(exc),
            )

    async def _persist_trace(
        self,
        trace_id: str,
        investigation_id: str,
        query: str,
        started_at: str,
        recording_ids: list[str],
        status: str,
        finished_at: Optional[str] = None,
        total_duration_ms: Optional[float] = None,
        total_cost_usd: Optional[float] = None,
        agent_results: Optional[list[AgentResult]] = None,
        final_report: Optional[dict] = None,
        code_revision: Optional[str] = None,
        model_config: Optional[dict] = None,
        usage_summary: Optional[dict] = None,
    ) -> None:
        agent_results_json = json.dumps(
            [r.to_dict() for r in agent_results] if agent_results else []
        )
        final_report_json = json.dumps(final_report) if final_report else None
        recording_ids_json = json.dumps(recording_ids)
        model_config_json = json.dumps(model_config) if model_config else None
        usage_summary_json = json.dumps(usage_summary) if usage_summary else None

        async with get_db(self.db_path) as db:
            # Phase 5 columns (code_revision, model_config, usage_summary) are
            # added by migration; fall back to the legacy column set when the
            # migration has not run (e.g. an older database file).
            try:
                await db.execute(
                    """INSERT OR REPLACE INTO investigation_traces
                       (trace_id, investigation_id, query, started_at, finished_at,
                        total_duration_ms, total_cost_usd, agent_results, final_report,
                        recording_ids, status, code_revision, model_config, usage_summary)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        trace_id, investigation_id, query, started_at, finished_at,
                        total_duration_ms, total_cost_usd or 0.0,
                        agent_results_json, final_report_json,
                        recording_ids_json, status, code_revision,
                        model_config_json, usage_summary_json,
                    ),
                )
            except Exception:
                await db.execute(
                    """INSERT OR REPLACE INTO investigation_traces
                       (trace_id, investigation_id, query, started_at, finished_at,
                        total_duration_ms, total_cost_usd, agent_results, final_report,
                        recording_ids, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        trace_id, investigation_id, query, started_at, finished_at,
                        total_duration_ms, total_cost_usd or 0.0,
                        agent_results_json, final_report_json,
                        recording_ids_json, status,
                    ),
                )
            await db.commit()
