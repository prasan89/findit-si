# RealityTrace Evaluation Report (Phase 5)

- Evaluation mode: **offline**
- Dataset: realitytrace-eval v2.0.0 (3 synthetic / 0 real recordings, 3 events, 8 queries)
- Code revision: 48da5b7
- Timestamp: 2026-10-28T13:35:42Z
- Model/config: `{"investigation_model": "muse-spark-1.3", "vision_model": "muse-spark-1.3", "mode": "offline-mocked (no API keys; zero live calls)"}`

> All footage is synthetic color blocks. Results do NOT represent real-world CCTV performance.

## Query-level results

| Query | Kind | Mode | P | R | F1 | Returned | Expected |
|---|---|---|---|---|---|---|---|
| Q1 | positive | standard | 1.000 | 1.000 | 1.000 | 1 | GT-001 |
| Q2 | positive | standard | 1.000 | 1.000 | 1.000 | 1 | GT-002 |
| Q3 | positive | standard | 1.000 | 1.000 | 1.000 | 1 | GT-003 |
| Q4 | temporal | standard | 0.000 | 0.000 | 0.000 | 1 | GT-001 |
| Q5 | negative | standard | n/a | n/a | n/a | 0 | - |
| Q6 | ambiguous | standard | n/a | n/a | n/a | 1 | - |
| Q7 | temporal | standard | n/a | 0.000 | n/a | 0 | GT-001,GT-003 |
| Q8 | temporal | standard | n/a | n/a | n/a | 0 | - |
| Q1 | positive | multi_agent | 1.000 | 1.000 | 1.000 | 1 | GT-001 |
| Q2 | positive | multi_agent | 1.000 | 1.000 | 1.000 | 1 | GT-002 |
| Q3 | positive | multi_agent | 1.000 | 1.000 | 1.000 | 1 | GT-003 |
| Q4 | temporal | multi_agent | 0.000 | 0.000 | 0.000 | 1 | GT-001 |
| Q5 | negative | multi_agent | n/a | n/a | n/a | 0 | - |
| Q6 | ambiguous | multi_agent | n/a | n/a | n/a | 1 | - |
| Q7 | temporal | multi_agent | n/a | 0.000 | n/a | 0 | GT-001,GT-003 |
| Q8 | temporal | multi_agent | n/a | n/a | n/a | 0 | - |

## Event detection (union over queries)

| Mode | P | R | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|
| standard | 1.000 | 1.000 | 1.000 | 3 | 0 | 0 |
| multi_agent | 1.000 | 1.000 | 1.000 | 3 | 0 | 0 |

## Temporal accuracy (matched events)

| Mode | Rel mean | Rel median | Abs mean (n) | Missing abs |
|---|---|---|---|---|
| standard | 0.000s | 0.000s | 0.000s (n=3) | 0 missing / 0 malformed |
| multi_agent | 0.000s | 0.000s | 0.000s (n=3) | 0 missing / 0 malformed |

## Cross-camera association

| Mode | P | R | F1 | False assoc (rate) | Ambiguous hits | Unmapped |
|---|---|---|---|---|---|---|
| standard | 0.000 | n/a | n/a | 2 (1.000 of 2) | 0 | 0 |
| multi_agent | 0.000 | n/a | n/a | 2 (1.000 of 2) | 0 | 0 |

## Evidence quality

| Mode | Coverage | Ref validity | Support | Unsupported | Contradiction |
|---|---|---|---|---|---|
| standard | 1.000 (5/5) | 1.000 (5/5) | n/a | n/a | n/a |
| multi_agent | 1.000 (10/10) | 1.000 (10/10) | n/a | n/a | n/a |

_Support/contradiction are null without human annotations; reference existence is never treated as support._

## Observability (multi-agent)

- Agent runs: 64 ({"completed": 34, "skipped": 15, "failed": 0, "mocked": 15})
- Total agent time: 48.3ms
- Measured tokens: in=0 out=0
- Measured cost: $0.0
- Cache: hits=None misses=None (unavailable=True)
- Retries: None, timeouts: None
- Skips: 15 (see JSON for reasons)

## Top failures

- standard Q4 (temporal): FN=['GT-001'] FP_preds=[0] P=0.000 R=0.000
- standard Q7 (temporal): FN=['GT-001', 'GT-003'] FP_preds=- P=n/a R=0.000
- standard cross-camera FALSE association: GT-001 <-> GT-003
- standard cross-camera FALSE association: GT-002 <-> GT-003
- multi_agent Q4 (temporal): FN=['GT-001'] FP_preds=[0] P=0.000 R=0.000
- multi_agent Q7 (temporal): FN=['GT-001', 'GT-003'] FP_preds=- P=n/a R=0.000
- multi_agent cross-camera FALSE association: GT-001 <-> GT-003
- multi_agent cross-camera FALSE association: GT-002 <-> GT-003

## Usage

```json
{
  "live_api_calls": 0,
  "tokens_in": null,
  "tokens_out": null,
  "cost_usd_measured": 0.0,
  "note": "Offline run: no live model calls; cost $0."
}
```

## Known limitations

- ALL footage is synthetic FFmpeg color blocks; results do NOT represent real-world CCTV performance.
- No real labeled footage was available; no real annotations were created or invented.
- Tiny sample: 3 GT events, 8 queries. Per-event-type statistics are reported with sample counts and flagged low-sample (n<5).
- No coverage for occlusion, low light, incomplete footage, unsynchronized camera clocks, or timestamp uncertainty (synthetic timestamps are exact).
- No confirmed-valid cross-camera pair exists, so cross-camera recall is undefined; only false-association rate is measurable.
- Q4/Q7/Q8 encode pure-time-filter intent, but investigate() scores leftover keyword terms; see per-query notes for the observed behavior gap.
