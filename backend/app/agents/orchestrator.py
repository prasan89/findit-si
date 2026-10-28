"""
InvestigationOrchestrator — runs all 8 specialist agents sequentially,
enforces budget, persists execution trace.
"""
import asyncio
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


AGENT_SEQUENCE = [
    "QueryInvestigationAgent",
    "VideoIntelligenceAgent",
    "ObjectActionAnalystAgent",
    "TemporalReasoningAgent",
    "CrossCameraCorrelationAgent",
    "ContradictionHunterAgent",
    "EvidenceAuditorAgent",
    "ReportSynthesisAgent",
]


class InvestigationOrchestrator:
    def __init__(
        self,
        db_path: str,
        budget_usd: float = 2.0,
        agent_timeout_s: Optional[float] = None,
        investigation_timeout_s: Optional[float] = None,
        max_llm_calls: Optional[int] = None,
        parallel_pairs: Optional[bool] = None,
    ):
        self.db_path = db_path
        self.budget_usd = budget_usd
        # Phase 6: timeouts/LLM cap (constructor overrides win; otherwise
        # Settings; otherwise the historical defaults).
        agent_default, inv_default, llm_default, pairs_default = 30.0, 120.0, 8, True
        try:
            from ..config import get_settings
            _settings = get_settings()
            agent_default = _settings.agent_timeout_seconds
            inv_default = _settings.investigation_timeout_seconds
            llm_default = _settings.investigation_max_llm_calls
            pairs_default = _settings.agent_parallel_pairs
        except Exception:
            pass
        self.agent_timeout_s = agent_default if agent_timeout_s is None else agent_timeout_s
        self.investigation_timeout_s = (
            inv_default if investigation_timeout_s is None else investigation_timeout_s)
        self.max_llm_calls = max_llm_calls if max_llm_calls is not None else llm_default
        self.parallel_pairs = pairs_default if parallel_pairs is None else parallel_pairs

    async def run(
        self,
        query: str,
        recording_ids: Optional[list[str]] = None,
    ) -> dict:
        """
        Execute the 8-agent pipeline and return a serializable result dict.
        Always returns; individual agent failures are recorded but do not abort the pipeline.
        Phase 6: each agent has its own timeout and the whole pipeline has an
        overall deadline. On deadline expiry the trace is persisted as PARTIAL
        with explicit timed_out/skipped markers — never fabricated results.
        """
        trace_id = str(uuid.uuid4())
        investigation_id = str(uuid.uuid4())
        started_at = _now()
        t0_total = time.monotonic()

        ctx = InvestigationContext(
            query=query,
            db_path=self.db_path,
            recording_ids=list(recording_ids or []),
            budget_usd=self.budget_usd,
            max_llm_calls=self.max_llm_calls,
        )

        await self._persist_trace(trace_id, investigation_id, query,
                                   started_at, recording_ids or [], "RUNNING")

        results: list[AgentResult] = []
        deadline_hit = False
        try:
            await asyncio.wait_for(
                self._run_stages(ctx, results),
                timeout=self.investigation_timeout_s,
            )
        except asyncio.TimeoutError:
            deadline_hit = True
            logger.warning(
                "Investigation %s hit the overall deadline (%.1fs); "
                "persisting partial trace",
                investigation_id[:8], self.investigation_timeout_s,
            )
            self._pad_unfinished_agents(results)
            # Best-effort final report from verified evidence when retrieval
            # finished; otherwise the report stays absent (never fabricated).
            await self._best_effort_synthesis(ctx, results)

        total_duration_ms = (time.monotonic() - t0_total) * 1000
        total_cost = sum(r.cost_usd for r in results)

        final_report_dict = None
        for r in reversed(results):
            if r.agent_name == "ReportSynthesisAgent" and r.output is not None:
                final_report_dict = r.output.model_dump()
                break

        trace_status = "COMPLETED"
        if deadline_hit or any(r.status in (AgentStatus.FAILED, AgentStatus.TIMED_OUT)
                               for r in results):
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
                "agent_timeout_s": self.agent_timeout_s,
                "investigation_timeout_s": self.investigation_timeout_s,
                "max_llm_calls": self.max_llm_calls,
            }
        except Exception:
            model_config = {
                "budget_usd": self.budget_usd,
                "agent_timeout_s": self.agent_timeout_s,
                "investigation_timeout_s": self.investigation_timeout_s,
                "max_llm_calls": self.max_llm_calls,
            }
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
            # Phase 6: model-call accounting for this investigation.
            "llm_calls_made": ctx.llm_calls,
            "llm_call_cap": ctx.max_llm_calls,
            "retries_total": sum(r.retries or 0 for r in results),
            "timeouts_total": sum(r.timeouts or 0 for r in results),
        }

    def _budget_ok(self, ctx: InvestigationContext) -> bool:
        return ctx.budget_spent < ctx.budget_usd

    async def _run_stages(
        self,
        ctx: InvestigationContext,
        results: list[AgentResult],
    ) -> None:
        """Run the 8 stages in order, appending to results (survives cancel)."""
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

        # Stage 1: QueryInvestigationAgent — populate ctx.events first
        r5 = await self._run_agent("QueryInvestigationAgent", query_investigation.run, ctx)
        results.append(r5)

        # Stages 2-3: VideoIntelligenceAgent + ObjectActionAnalystAgent.
        # Independent (both read ctx.events, write disjoint outputs) so they
        # run concurrently when enabled; results assemble in fixed order.
        await self._run_llm_pair(
            ctx, results,
            ("VideoIntelligenceAgent", video_intelligence),
            ("ObjectActionAnalystAgent", object_action_analyst),
        )

        # Stage 4: TemporalReasoningAgent (non-LLM)
        r3 = await self._run_agent("TemporalReasoningAgent", temporal_reasoning.run, ctx)
        results.append(r3)
        if r3.output is not None:
            ctx.agent_outputs["temporal_report"] = r3.output

        # Stage 5: CrossCameraCorrelationAgent (non-LLM)
        r4 = await self._run_agent("CrossCameraCorrelationAgent", cross_camera_correlation.run, ctx)
        results.append(r4)

        # Stages 6-7: ContradictionHunterAgent + EvidenceAuditorAgent.
        # Independent of each other (both read ctx.events/temporal report,
        # write disjoint outputs) so they run concurrently when enabled.
        await self._run_llm_pair(
            ctx, results,
            ("ContradictionHunterAgent", contradiction_hunter),
            ("EvidenceAuditorAgent", evidence_auditor),
        )
        r7 = next(r for r in results if r.agent_name == "EvidenceAuditorAgent")

        # Stage 8: ReportSynthesisAgent (non-LLM, always runs)
        agents_run = [
            r.agent_name for r in results if r.status == AgentStatus.COMPLETED
        ]
        r8 = await self._run_agent(
            "ReportSynthesisAgent",
            lambda c: report_synthesis.run(
                ctx=c,
                audit_report=r7.output,
                evidence_bundle=r5.output,
                agents_run=agents_run,
            ),
            ctx,
        )
        results.append(r8)

    async def _run_llm_pair(
        self,
        ctx: InvestigationContext,
        results: list[AgentResult],
        first: tuple[str, object],
        second: tuple[str, object],
    ) -> None:
        """Run an independent LLM-agent pair, concurrently when enabled.

        Budget is checked for both up front (both see pre-pair spend; the
        per-investigation LLM-call cap still bounds total calls either way).
        Results always append in the declared order so traces assemble
        deterministically. Shared-context mutations are await-free (atomic
        under asyncio), so no lock is needed.
        """
        rows: dict[str, AgentResult] = {}
        runnable: list[tuple[str, object]] = []
        for name, _module in (first, second):
            if self._budget_ok(ctx):
                runnable.append((name, _module))
            else:
                rows[name] = AgentResult(
                    agent_name=name,
                    status=AgentStatus.SKIPPED,
                    output=None,
                    duration_ms=0.0,
                    error="Budget limit reached",
                )
        if self.parallel_pairs and len(runnable) == 2:
            (n1, m1), (n2, m2) = runnable
            r1, r2 = await asyncio.gather(
                self._run_agent(n1, m1.run, ctx),  # type: ignore[attr-defined]
                self._run_agent(n2, m2.run, ctx),  # type: ignore[attr-defined]
            )
            rows[n1], rows[n2] = r1, r2
        else:
            for name, module in runnable:
                rows[name] = await self._run_agent(name, module.run, ctx)  # type: ignore[attr-defined]
        for name, _module in (first, second):
            results.append(rows[name])

    def _pad_unfinished_agents(self, results: list[AgentResult]) -> None:
        """Mark agents missing after a deadline with timed_out/skipped states.

        The pipeline is sequential, so the first missing agent was in flight
        (timed_out) and the rest never started (skipped). Durations reflect
        the overall deadline; nothing is presented as completed.
        """
        present = {r.agent_name for r in results}
        missing = [name for name in AGENT_SEQUENCE if name not in present]
        for i, name in enumerate(missing):
            status = AgentStatus.TIMED_OUT if i == 0 else AgentStatus.SKIPPED
            results.append(AgentResult(
                agent_name=name,
                status=status,
                output=None,
                duration_ms=round(self.investigation_timeout_s * 1000, 1) if i == 0 else 0.0,
                error="Overall investigation deadline exceeded",
            ))

    async def _best_effort_synthesis(
        self,
        ctx: InvestigationContext,
        results: list[AgentResult],
    ) -> None:
        """Replace the padded synthesis row with a real report when possible.

        Only runs when retrieval finished (ctx.events non-empty); the report
        then reflects verified evidence only. Otherwise the padded row stands
        and the final report stays absent.
        """
        if not ctx.events:
            return
        try:
            from . import report_synthesis
            agents_run = [
                r.agent_name for r in results if r.status == AgentStatus.COMPLETED
            ]
            evidence_bundle = None
            for r in results:
                if r.agent_name == "QueryInvestigationAgent" and r.output is not None:
                    evidence_bundle = r.output
                    break
            audit_report = None
            for r in results:
                if r.agent_name == "EvidenceAuditorAgent" and r.output is not None:
                    audit_report = r.output
                    break
            r8 = await report_synthesis.run(
                ctx=ctx,
                audit_report=audit_report,
                evidence_bundle=evidence_bundle,
                agents_run=agents_run,
            )
            results[:] = [r for r in results if r.agent_name != "ReportSynthesisAgent"]
            results.append(r8)
        except Exception as exc:
            logger.warning("Best-effort synthesis failed: %s", type(exc).__name__)

    async def _run_agent(self, name: str, fn, ctx: InvestigationContext) -> AgentResult:
        try:
            return await asyncio.wait_for(fn(ctx), timeout=self.agent_timeout_s)
        except asyncio.TimeoutError:
            logger.warning("Agent %s timed out after %.1fs", name, self.agent_timeout_s)
            return AgentResult(
                agent_name=name,
                status=AgentStatus.TIMED_OUT,
                output=None,
                duration_ms=round(self.agent_timeout_s * 1000, 1),
                error=f"Agent exceeded its {self.agent_timeout_s}s execution timeout",
                timeouts=1,
            )
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
