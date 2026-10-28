# Provider comparison: meta vs baseten (investigation LLM path)

- dry_run: `False`
- code_revision: `d1dbdd3`
- dataset: `realitytrace-eval v2.1.0`
- queries: `2`

## meta

- detection P/R/F1: `1.0/0.6666666666666666/0.8` (tp=2 fp=0 fn=1)
- evidence_coverage: `1.0` reference_validity: `1.0`
- usage: `{'calls': 6, 'tokens_in': 1556, 'tokens_out': 5384, 'cost_usd': 0.024827}`
- latency_ms (n=2): `[10301.7, 9017.4]`

## baseten

- detection P/R/F1: `1.0/0.6666666666666666/0.8` (tp=2 fp=0 fn=1)
- evidence_coverage: `1.0` reference_validity: `1.0`
- usage: `{'calls': 6, 'tokens_in': 2378, 'tokens_out': 5664, 'cost_usd': 0.0}`
- latency_ms (n=2): `[8266.2, 7887.6]`

Baseten cost_usd is provider-unreported (contract pricing); 0.0 means unavailable, not free.
