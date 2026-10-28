# RealityTrace — Pre-Guardian E2E Release Report

Date: 2026-10-28. Starting tree: `main` at `be24955` (clean).

## 1. Test results and commands

| Check | Command | Result |
|---|---|---|
| Backend suite | `backend/.venv/bin/python -m pytest -q` | **423 passed, 1 deselected** (`live_api`), 7.2s |
| Vision + reliability files | `pytest test_human_query_relevance test_bird_detection test_dog_query_relevance test_vision_accuracy_matrix test_phase6_reliability` | **142 passed** |
| Frontend unit | `npm test` | **15/15** |
| Typecheck | `npx tsc --noEmit` | clean, exit 0 |
| Lint | `npm run lint` | **not configured** (Next prompts for ESLint setup; no config exists) |
| Production build | `npm run build` (dev server stopped first) | green, exit 0 |
| Browser E2E | `npx playwright test` (system Chrome, seeded mock backend) | **6/6**, 19.2s |
| Eval reproducibility | in-memory `run_offline` vs `comparison_report.json` | identical modulo timestamp + code-revision stamps |
| Secret scan | grep for keys/tokens across code+docs | clean (one negative-assertion test); no `.env` file |

Live Meta API calls during this gate: **zero**. All validation used mocks, stored data, and deterministic fixtures.

## 2. Upload-to-investigation-to-playback verification (browser E2E, real UI)

All 6 Playwright journeys pass against local servers with a seeded deterministic backend:

1. Upload valid mp4 → appears in library → background indexing reaches INDEXED.
2. Standard investigation → event timeline with correct recording/description/timestamp.
3. Multi-Agent investigation → 8-agent trace, narrative, agent statuses.
4. Finding Preview → modal video opens and seeks to the evidence timestamp (`currentTime` asserted).
5. Unanswerable query → honest no-match message, no invented conclusions.
6. (New in this gate) Reload persistence: library entries survive reload; usage badge renders spend; `/api/investigations/traces` returns the persisted multi-agent trace.

## 3. Bird/person/dog regression results

- Backend matrix suites (bird/dog/person/vehicle/unknown/empty × query matrix, singular/plural/punctuation): all pass; contradiction rows flagged with confidence untouched.
- Live browser matrix vs `:3000`/`:8000`: `any birds detected?` → 2 × Bird; `any human detected` → 0, no Person; `any dog detected?` → 0, no Bird. Passed.
- Prior false-person suite remains green; no test was weakened (2 expectations in the prior suite were updated `scene_change`→`bird_detected` when the bird category shipped — strictly more precise).

## 4. Standard and Multi-Agent workflow results

- Both modes share the tested retrieval path (`QueryInvestigationAgent` wraps `investigate()`); subject gates, synonym coverage, and contradiction flags apply identically.
- 8-agent trace verified in UI (journey 3) and API (journey 6); truthful `completed/skipped/failed/mocked/timed_out` states covered by backend suites (no false-success reporting).
- Token/cost fields: provider-reported values recorded via `model_usage`; estimates labeled as estimates; no fabricated costs (mock E2E reports $0.0000).

## 5. Security and reliability findings

- Phase 6 suite (94 tests) green: transient-only retries, timeouts/deadlines, upload hardening, traversal defense, token gate, rate limits, concurrency caps, versioned cache, leakage tests.
- Known accepted limits (unchanged, documented in prior phases): reads open / optional API token only (no full auth — do not expose unauthenticated); rate limiter + semaphores + slots are per-process; cache TTL default 3600 with manual prune.
- No new defects found in this gate. Live logs show occasional text-agent `ValueError`s (non-JSON model output) handled by the designed fallback path (trace completes, `success=False` recorded) — P3, no action.

## 6. Evaluation dataset size and limitations

`realitytrace-eval` v2.1.0: 3 recordings (3 synthetic, **0 real**), 3 events, 11 queries (3 positive / 6 temporal / 1 negative / 1 ambiguous), cross-camera labels 0 valid / 2 invalid / 1 ambiguous. Machine-readable report reproduces exactly. Metrics prove harness health, **not** real-world accuracy — no real labeled CCTV has been evaluated.

## 7. Unresolved issues by severity

- P0/P1: **none**.
- P2: real-world detection accuracy unmeasured (synthetic-only eval + mock E2E). Recommended before production claims, not a Guardian blocker.
- P3: ESLint unconfigured (`next lint` prompts for setup); raw model responses not retained (parsed analyses + observations only); text-agent non-JSON fallbacks observed.

## 8. Manual checks still required

1. Bounded live-model evaluation on a small real labeled sample (measures prompts/cost/latency for real).
2. Deployment review of the no-auth default before any non-localhost exposure.
3. Confirm Guardian's alerting requirements against the trace/usage APIs it will consume.

## 9. Decision

`READY FOR GUARDIAN`
