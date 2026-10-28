#!/usr/bin/env python3
"""Repeatable performance benchmark for Standard vs Multi-Agent mode (Phase 8).

Runs every dataset query against both modes through the real HTTP app
(in-process ASGI transport — no server, no network, no model calls) and
records machine-readable latencies, per-agent durations, model-call counts,
and cold/warm cache behavior.

    backend/.venv/bin/python eval/benchmark.py [--repeats N]

Outputs: eval/benchmark_report.json

Honesty notes (also embedded in the report):
- Latencies are wall-clock on THIS machine with MOCKED model calls (LLM
  agents return instantly without API keys). They measure framework overhead
  (retrieval, orchestration, serialization, cache), NOT model latency.
- Live model latency/cost require the opt-in live path (compare.py --live).
- p50 is reported with its sample count; p95 only when n>=20.
- Model-call counts are exact (instrumented); token/cost figures are 0/None
  offline because no live provider usage exists.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).parent
REPO_ROOT = EVAL_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

# Offline safety: no live model calls anywhere in this process.
for _var in ("MODEL_API_KEY", "META_API_KEY"):
    os.environ.pop(_var, None)

from eval.dataset import composition, load_v2  # noqa: E402
import eval.build_reference as build_reference  # noqa: E402

REPORT_FILE = EVAL_DIR / "benchmark_report.json"


def _code_revision() -> str | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5,
                              cwd=str(REPO_ROOT))
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return None


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None, "p95": None}
    ordered = sorted(values)
    p95 = None
    if len(ordered) >= 20:
        import math
        p95 = ordered[min(math.ceil(0.95 * len(ordered)) - 1, len(ordered) - 1)]
    return {
        "n": len(values),
        "min_ms": round(min(values), 2),
        "median_ms": round(statistics.median(values), 2),
        "max_ms": round(max(values), 2),
        "p95_ms": round(p95, 2) if p95 is not None else None,
        "p95_note": None if p95 is not None else "n<20: p95 not meaningful",
    }


async def _build_fixture_db() -> tuple[str, dict]:
    ds = load_v2()
    tmp = tempfile.mkdtemp(prefix="realitytrace-bench-")
    db_path = str(Path(tmp) / "fixture.db")
    await build_reference._seed_fixture_db(db_path, ds)
    recording_ids = list(build_reference.FIXTURE_RECORDINGS.values())
    from app.correlator import correlate_across_cameras
    n_links = await correlate_across_cameras(db_path, recording_ids)
    print(f"Fixture: {len(ds['queries'])} queries, {n_links} CC links")
    return db_path, ds


async def _run_pass(app, ds: dict, pass_name: str) -> dict[str, dict]:
    """One benchmark pass over all queries x both modes via ASGI transport."""
    import httpx
    from app.database import get_db
    results: dict[str, dict] = {"standard": {}, "multi_agent": {}}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://bench") as client:
        for q in ds["queries"]:
            # Standard mode.
            t0 = time.monotonic()
            std_resp = await client.post("/api/investigations/", json={"query": q["query"]})
            std_ms = (time.monotonic() - t0) * 1000
            std_body = std_resp.json()
            results["standard"][q["id"]] = {
                "status_code": std_resp.status_code,
                "latency_ms": round(std_ms, 2),
                "cache_hit": bool(std_body.get("cache_hit", False)),
                "n_events": len(std_body.get("events", [])),
                "llm_calls": 0,
            }
            # Multi-agent mode.
            t0 = time.monotonic()
            ma_resp = await client.post(
                "/api/investigations/multi-agent",
                json={"query": q["query"], "budget_usd": 0.01})
            ma_ms = (time.monotonic() - t0) * 1000
            ma_body = ma_resp.json()
            agent_durations = {a["agent_name"]: a["duration_ms"]
                               for a in ma_body.get("agents", [])}
            results["multi_agent"][q["id"]] = {
                "status_code": ma_resp.status_code,
                "latency_ms": round(ma_ms, 2),
                "cache_hit": bool(ma_body.get("cache_hit", False)),
                "n_events": len(ma_body.get("retrieved_events", [])),
                "llm_calls": int(ma_body.get("llm_calls_made", 0) or 0),
                "agent_statuses": {a["agent_name"]: a["status"]
                                   for a in ma_body.get("agents", [])},
                "agent_durations_ms": agent_durations,
                "orchestrator_ms": ma_body.get("total_duration_ms"),
            }
    return results


async def main_async(repeats: int) -> dict:
    db_path, ds = await _build_fixture_db()

    # Point the app at the fixture database.
    data_dir = str(Path(db_path).parent)
    os.environ["DATA_DIR"] = data_dir
    # The fixture db file must be named findit.db for Settings.db_path.
    fixture_final = Path(data_dir) / "findit.db"
    Path(db_path).rename(fixture_final)
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import create_app
    app = create_app()

    passes: dict[str, dict] = {}
    for i in range(repeats):
        name = "cold" if i == 0 else f"warm_{i}"
        passes[name] = await _run_pass(app, ds, name)
        print(f"Pass {name} done")

    # Summaries per mode/pass.
    summary: dict[str, dict] = {}
    for mode in ("standard", "multi_agent"):
        summary[mode] = {}
        for name, data in passes.items():
            latencies = [v["latency_ms"] for v in data[mode].values()]
            misses = [v for v in data[mode].values() if not v["cache_hit"]]
            hits = [v for v in data[mode].values() if v["cache_hit"]]
            summary[mode][name] = {
                "latency": _summary(latencies),
                "cache_hits": len(hits),
                "n_queries": len(data[mode]),
                # New calls actually executed this pass (hits execute none).
                "llm_calls_new": sum(v["llm_calls"] for v in misses),
                # Calls the cache avoided (stored values replayed on hits).
                "llm_calls_avoided": sum(v["llm_calls"] for v in hits),
            }
    # Per-agent duration medians (multi-agent, cold pass).
    agent_meds: dict[str, float] = {}
    cold = passes.get("cold", {}).get("multi_agent", {})
    agent_names: set[str] = set()
    for v in cold.values():
        agent_names.update((v.get("agent_durations_ms") or {}).keys())
    for agent in sorted(agent_names):
        vals = [v["agent_durations_ms"][agent] for v in cold.values()
                if agent in (v.get("agent_durations_ms") or {})]
        agent_meds[agent] = round(statistics.median(vals), 2) if vals else 0.0

    return {
        "dataset": composition(ds),
        "code_revision": _code_revision(),
        "evaluation_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {"repeats": repeats, "passes": sorted(passes),
                   "llm_mode": "mocked (no API keys; 0 live calls, $0)",
                   "transport": "in-process ASGI (no network)"},
        "summary": summary,
        "agent_duration_median_ms_cold": agent_meds,
        "passes": passes,
        "honesty": {
            "latency_measures": ("Framework overhead only (retrieval, orchestration, "
                                 "serialization, cache) with instant mocked model calls. "
                                 "Not comparable across machines; not production capacity."),
            "live_costs": ("Token/cost figures are unavailable offline (null/0). "
                           "Use compare.py --live for provider-measured usage."),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RealityTrace Phase 8 benchmark")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", default=str(REPORT_FILE))
    args = parser.parse_args()
    import asyncio
    report = asyncio.run(main_async(max(1, args.repeats)))
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(f"Wrote {args.out}")
    for mode, per_pass in report["summary"].items():
        for name, stats in per_pass.items():
            lat = stats["latency"]
            print(f"  {mode}/{name}: median={lat['median_ms']}ms "
                  f"(n={lat['n']}) hits={stats['cache_hits']} "
                  f"new_calls={stats['llm_calls_new']} "
                  f"avoided={stats['llm_calls_avoided']}")


if __name__ == "__main__":
    main()
