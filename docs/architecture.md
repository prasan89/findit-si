# RealityTrace Phase 3 — Multi-Agent Investigation Architecture

## Overview

RealityTrace Phase 3 implements a genuine 8-agent investigation pipeline for CCTV incident analysis. Each agent performs a distinct, testable task and produces a validated Pydantic output that is consumed by downstream agents.

```
POST /api/investigations/multi-agent
              │
              ▼
    ┌─────────────────────────────────────────────┐
    │         InvestigationOrchestrator           │
    │                                             │
    │  Budget: $investigation_budget_usd (default │
    │          $2.00 per investigation)           │
    │  Timeout: investigation_timeout_seconds     │
    └─────────────┬───────────────────────────────┘
                  │
    ┌─────────────▼───────────────────────────────┐
    │  Stage 1: QueryInvestigationAgent           │ (non-LLM)
    │  Wraps investigate() — retrieves, ranks,    │
    │  and scores events by query relevance.       │
    │  Output: EvidenceBundle                     │
    │  DB reads: events, recordings, event_links  │
    └─────────────┬───────────────────────────────┘
                  │  ctx.events populated
    ┌─────────────▼───────────────────────────────┐
    │  Stage 2: VideoIntelligenceAgent            │ (LLM: muse-spark-1.3)
    │  Text-only prompt: per-event visual summary │
    │  from observed_facts + action_description.  │
    │  Output: VisualSummaryReport                │
    └─────────────┬───────────────────────────────┘
                  │
    ┌─────────────▼───────────────────────────────┐
    │  Stage 3: ObjectActionAnalystAgent          │ (LLM: muse-spark-1.3)
    │  Identifies distinct objects, actions,      │
    │  and scene changes per event.               │
    │  Output: ObjectActionAnalysis               │
    └─────────────┬───────────────────────────────┘
                  │
    ┌─────────────▼───────────────────────────────┐
    │  Stage 4: TemporalReasoningAgent            │ (non-LLM)
    │  Normalizes timestamps, detects gaps        │
    │  and clock-skew inconsistencies.            │
    │  Reuses _sort_key() from investigate.py     │
    │  Output: TemporalReport                     │
    └─────────────┬───────────────────────────────┘
                  │
    ┌─────────────▼───────────────────────────────┐
    │  Stage 5: CrossCameraCorrelationAgent       │ (non-LLM)
    │  Wraps correlate_across_cameras() —         │
    │  temporal + appearance scoring, writes      │
    │  event_links rows. SKIPPED if < 2 cameras.  │
    │  Output: CorrelationReport                  │
    │  DB writes: event_links                     │
    └─────────────┬───────────────────────────────┘
                  │
    ┌─────────────▼───────────────────────────────┐
    │  Stage 6: ContradictionHunterAgent          │ (LLM: muse-spark-1.3)
    │  Reviews events for conflicting claims,     │
    │  impossible transitions, evidence gaps.     │
    │  Returns contradictions_found=false if      │
    │  none found (distinct from "no data").      │
    │  Output: ContradictionReport                │
    └─────────────┬───────────────────────────────┘
                  │
    ┌─────────────▼───────────────────────────────┐
    │  Stage 7: EvidenceAuditorAgent              │ (LLM: muse-spark-1.3)
    │  Verifies each inferred_claim against its   │
    │  event's observed_facts. Rejects claims     │
    │  not supported by observations.             │
    │  Output: AuditReport                        │
    └─────────────┬───────────────────────────────┘
                  │
    ┌─────────────▼───────────────────────────────┐
    │  Stage 8: ReportSynthesisAgent              │ (non-LLM)
    │  Assembles final narrative from verified    │
    │  evidence only. Reuses _build_narrative()   │
    │  from investigate.py.                       │
    │  Output: FinalReport with evidence_citations│
    └─────────────┬───────────────────────────────┘
                  │
    ┌─────────────▼───────────────────────────────┐
    │  investigation_traces (SQLite)              │
    │  Persists: trace_id, agent_results (JSON),  │
    │  final_report, total_cost_usd, status       │
    └─────────────────────────────────────────────┘
```

## Agent Classification

| Agent | Type | Model | DB Access |
|---|---|---|---|
| QueryInvestigationAgent | Non-LLM | — | R: events, recordings, event_links |
| VideoIntelligenceAgent | LLM | muse-spark-1.3 | W: model_usage |
| ObjectActionAnalystAgent | LLM | muse-spark-1.3 | W: model_usage |
| TemporalReasoningAgent | Non-LLM | — | — |
| CrossCameraCorrelationAgent | Non-LLM | — | W: event_links |
| ContradictionHunterAgent | LLM | muse-spark-1.3 | W: model_usage |
| EvidenceAuditorAgent | LLM | muse-spark-1.3 | W: model_usage |
| ReportSynthesisAgent | Non-LLM | — | — |

## Budget Enforcement

- LLM agents (2, 3, 6, 7) are SKIPPED if `ctx.budget_spent >= ctx.budget_usd`
- Non-LLM agents always run regardless of budget
- `BudgetExceededError` from `provider.py` marks agent as SKIPPED
- Default budget: `$2.00` per investigation (configurable via `INVESTIGATION_BUDGET_USD`)
- Token accounting uses existing `model_usage` table and `record_usage()`

## Evidence Safety Guarantees

- Every factual claim is linked to a source `event_id` from the database
- `ReportSynthesisAgent` only uses events that passed `EvidenceAuditorAgent`
- `CrossCameraLinks` remain `candidate` unless score >= 0.85 (confirmed)
- Narratives are assembled from retrieved evidence text only — never fabricated
- No facial recognition, no real-individual identification
- No API keys, model prompts, or sensitive data exposed in frontend

## File Layout

```
backend/app/agents/
├── __init__.py
├── base.py          — AgentResult, AgentStatus, InvestigationContext
├── schemas.py       — Pydantic output schemas (8 agents)
├── llm_helper.py    — Shared text-only LLM call helper
├── video_intelligence.py
├── object_action_analyst.py
├── temporal_reasoning.py
├── cross_camera_correlation.py
├── query_investigation.py
├── contradiction_hunter.py
├── evidence_auditor.py
├── report_synthesis.py
├── orchestrator.py  — InvestigationOrchestrator
└── registry.py      — AGENT_REGISTRY

backend/app/routes/multi_agent_investigations.py
frontend/src/components/AgentStatusPanel.tsx
frontend/src/components/InvestigationPanel.tsx  (updated)
frontend/src/types/index.ts                     (updated)
```

## Model Providers (Meta + Baseten)

Both LLM paths speak OpenAI-compatible `POST /chat/completions` over httpx
(no OpenAI SDK dependency). `backend/app/provider_config.py` resolves
`(provider, base_url, api_key, model)` per path:

- Frame indexing: `VISION_PROVIDER` (`meta` default) → `VISION_MODEL`, or
  `baseten` → `BASETEN_MODEL` (`meta-models/muse-spark-open-1026`).
- Investigation agents: `INVESTIGATION_PROVIDER` (`meta` default) →
  `INVESTIGATION_MODEL`, or `baseten` → `BASETEN_MODEL`.

Keys: `MODEL_API_KEY`/`META_API_KEY` (+`META_API_BASE_URL`) for Meta;
`BASETEN_API_KEY` (+`BASETEN_BASE_URL`) for Baseten. Backend only — never in
bundles, logs, traces, or error responses. A selected provider without its key
serves deterministic mocks (agents report `MOCKED`, vision reports `mock-v1`);
startup logs a warning via `validate_provider_config()`.

Provenance: usage rows record provider+model; traces record resolved
provider/model per agent (`provider_used`) and per run (`model_config`).
Frame-cache keys include the model id and investigation result-cache keys
include both providers, so results never cross providers. Baseten token
counts are exact when reported; cost is unavailable (contract pricing) and
recorded as 0.0 — never presented as free. Retry, timeout, budget, and
structured-output validation are shared across providers.
