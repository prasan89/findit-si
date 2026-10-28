# RealityTrace Evaluation Report (Phase 5)

- Evaluation mode: **offline**
- Dataset: realitytrace-eval v2.1.0 (3 synthetic / 0 real recordings, 3 events, 11 queries)
- Code revision: 01c156f
- Timestamp: 2026-10-28T14:25:07Z
- Model/config: `{"investigation_model": "muse-spark-1.3", "vision_model": "muse-spark-1.3", "mode": "offline-mocked (no API keys; zero live calls)"}`

> All footage is synthetic color blocks. Results do NOT represent real-world CCTV performance.

## Query-level results

| Query | Kind | Mode | P | R | F1 | Returned | Expected |
|---|---|---|---|---|---|---|---|
| Q1 | positive | standard | 1.000 | 1.000 | 1.000 | 1 | GT-001 |
| Q2 | positive | standard | 1.000 | 1.000 | 1.000 | 1 | GT-002 |
| Q3 | positive | standard | 1.000 | 1.000 | 1.000 | 1 | GT-003 |
| Q4 | temporal | standard | 0.333 | 1.000 | 0.500 | 3 | GT-001 |
| Q5 | negative | standard | n/a | n/a | n/a | 0 | - |
| Q6 | ambiguous | standard | n/a | n/a | n/a | 1 | - |
| Q7 | temporal | standard | 1.000 | 1.000 | 1.000 | 2 | GT-001,GT-003 |
| Q8 | temporal | standard | n/a | n/a | n/a | 0 | - |
| Q9 | temporal | standard | 1.000 | 1.000 | 1.000 | 1 | GT-002 |
| Q10 | temporal | standard | 1.000 | 1.000 | 1.000 | 1 | GT-001 |
| Q11 | temporal | standard | 1.000 | 1.000 | 1.000 | 1 | GT-002 |
| Q1 | positive | multi_agent | 1.000 | 1.000 | 1.000 | 1 | GT-001 |
| Q2 | positive | multi_agent | 1.000 | 1.000 | 1.000 | 1 | GT-002 |
| Q3 | positive | multi_agent | 1.000 | 1.000 | 1.000 | 1 | GT-003 |
| Q4 | temporal | multi_agent | 0.333 | 1.000 | 0.500 | 3 | GT-001 |
| Q5 | negative | multi_agent | n/a | n/a | n/a | 0 | - |
| Q6 | ambiguous | multi_agent | n/a | n/a | n/a | 1 | - |
| Q7 | temporal | multi_agent | 1.000 | 1.000 | 1.000 | 2 | GT-001,GT-003 |
| Q8 | temporal | multi_agent | n/a | n/a | n/a | 0 | - |
| Q9 | temporal | multi_agent | 1.000 | 1.000 | 1.000 | 1 | GT-002 |
| Q10 | temporal | multi_agent | 1.000 | 1.000 | 1.000 | 1 | GT-001 |
| Q11 | temporal | multi_agent | 1.000 | 1.000 | 1.000 | 1 | GT-002 |

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
| standard | n/a | n/a | n/a | 0 (n/a of 0) | 0 | 0 |
| multi_agent | n/a | n/a | n/a | 0 (n/a of 0) | 0 | 0 |

## Evidence quality

| Mode | Coverage | Ref validity | Support | Unsupported | Contradiction |
|---|---|---|---|---|---|
| standard | 1.000 (12/12) | 1.000 (12/12) | n/a | n/a | n/a |
| multi_agent | 1.000 (24/24) | 1.000 (24/24) | n/a | n/a | n/a |

_Support/contradiction are null without human annotations; reference existence is never treated as support._

## Observability (multi-agent)

- Agent runs: 88 ({"completed": 51, "skipped": 10, "failed": 0, "mocked": 27, "timed_out": 0})
- Total agent time: 45.3ms
- Measured tokens: in=0 out=0
- Measured cost: $0.0
- Cache: hits=None misses=None (unavailable=True)
- Retries: None, timeouts: None
- Skips: 10 (see JSON for reasons)

## Top failures

- standard Q4 (temporal): FN=- FP_preds=[1, 2] P=0.333 R=1.000
- multi_agent Q4 (temporal): FN=- FP_preds=[1, 2] P=0.333 R=1.000

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
- Temporal queries (between/before/after/first/last) use video-relative MM:SS and pure time filtering since Phase 4; absolute clock-time queries (e.g. 'between 10:00 and 10:15' as wall time) remain unsupported.
- Q4 preserves a v1 expectation quirk ([GT-001] though all three midpoints fall in 0-15s); see its notes.
