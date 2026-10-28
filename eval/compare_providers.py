#!/usr/bin/env python3
"""Meta vs Baseten investigation-provider comparison (text-agent quality/cost).

Scope: compares the multi-agent investigation LLM path (VideoIntelligence,
ObjectActionAnalyst, ContradictionHunter, EvidenceAuditor, ReportSynthesis)
with identical pre-indexed fixture events. Vision indexing is NOT recompared
(fixture DB is shared); a full vision matrix needs per-provider re-indexing.

Dry-run (default, free): runs every dataset query per provider leg with mocked
LLM calls to verify mechanics end-to-end. Labeled dry_run; no quality claims.

    backend/.venv/bin/python eval/compare_providers.py
    backend/.venv/bin/python eval/compare_providers.py --max-queries 3

Live (explicit opt-in, spends real credits on BOTH providers):
    REALITYTRACE_LIVE_EVAL=1 backend/.venv/bin/python eval/compare_providers.py \\
        --live [--max-queries 2]

Live safeguards: requires BOTH --live AND REALITYTRACE_LIVE_EVAL=1, checks the
real-DB budget floor, caps queries (default 2), runs sequentially, and reports
measured token/cost deltas. Baseten cost shows as unavailable (contract
pricing) — token counts are exact when provider-reported.

Outputs (always rewritten):
    eval/provider_comparison_report.json  — machine-readable report
    eval/provider_comparison_report.md    — human-readable summary
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
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

from eval.dataset import load_v2  # noqa: E402
# build_reference strips Meta keys at import (offline safety). Stash first so
# --live can restore them; dry-run keeps them stripped. Values are never
# printed or logged — same handling as the normal environment.
_STASHED_KEYS = {
    var: os.environ.get(var)
    for var in ("MODEL_API_KEY", "META_API_KEY", "BASETEN_API_KEY")
}
import eval.build_reference as build_reference  # noqa: E402
import eval.compare as compare  # noqa: E402

REPORT_JSON = EVAL_DIR / "provider_comparison_report.json"
REPORT_MD = EVAL_DIR / "provider_comparison_report.md"
LIVE_ENV_VAR = "REALITYTRACE_LIVE_EVAL"
DEFAULT_MIN_REMAINING_USD = 5.0
PROVIDERS = ("meta", "baseten")


def _code_revision() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:
        return None


async def _seed_leg_db() -> tuple[str, str]:
    """Fresh temp data dir with a seeded fixture DB. Returns (data_dir, db_path)."""
    from app.database import get_db
    ds = load_v2()
    tmp = tempfile.mkdtemp(prefix="realitytrace-prov-")
    seed_path = str(Path(tmp) / "fixture.db")
    await build_reference._seed_fixture_db(seed_path, ds)
    recording_ids = list(build_reference.FIXTURE_RECORDINGS.values())
    from app.correlator import correlate_across_cameras
    await correlate_across_cameras(seed_path, recording_ids)
    final = str(Path(tmp) / "findit.db")
    Path(seed_path).rename(final)
    return tmp, final


async def _usage_totals(db_path: str) -> dict[str, float]:
    from app.database import get_db
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(tokens_in),0),"
            " COALESCE(SUM(tokens_out),0), COALESCE(SUM(estimated_cost_usd),0.0)"
            " FROM model_usage")).fetchone()
    return {"calls": row[0], "tokens_in": row[1],
            "tokens_out": row[2], "cost_usd": round(float(row[3]), 6)}


def _real_db_spend() -> float | None:
    try:
        from app.config import get_settings
        from app.provider import get_total_spent
        return asyncio.run(get_total_spent(get_settings().db_path))
    except Exception:
        return None


async def _run_leg(provider: str, queries: list[dict], live: bool) -> dict[str, Any]:
    """Run one provider leg in-process; return per-query predictions + stats."""
    import httpx
    from app.config import get_settings
    from app.main import create_app

    data_dir, db_path = await _seed_leg_db()
    old_provider = os.environ.get("INVESTIGATION_PROVIDER")
    os.environ["INVESTIGATION_PROVIDER"] = provider
    old_data_dir = os.environ.get("DATA_DIR")
    os.environ["DATA_DIR"] = data_dir
    get_settings.cache_clear()
    try:
        app = create_app()
        per_query: dict[str, dict] = {}
        latencies: list[float] = []
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://provbench") as client:
            for q in queries:
                t0 = time.monotonic()
                resp = await client.post(
                    "/api/investigations/multi-agent",
                    json={"query": q["query"], "budget_usd": 0.50})
                dt_ms = (time.monotonic() - t0) * 1000
                body = resp.json()
                latencies.append(dt_ms)
                per_query[q["id"]] = {
                    "query": q["query"],
                    "status_code": resp.status_code,
                    "latency_ms": round(dt_ms, 1),
                    "events": body.get("retrieved_events", []),
                    "agents": body.get("agents", []),
                    "final_report": body.get("final_report", {}),
                    "total_cost_usd": body.get("total_cost_usd", 0.0),
                }
        usage = await _usage_totals(db_path)
        return {"per_query": per_query, "usage": usage,
                "latencies_ms": [round(v, 1) for v in latencies]}
    finally:
        if old_provider is None:
            os.environ.pop("INVESTIGATION_PROVIDER", None)
        else:
            os.environ["INVESTIGATION_PROVIDER"] = old_provider
        if old_data_dir is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = old_data_dir
        get_settings.cache_clear()
        shutil.rmtree(data_dir, ignore_errors=True)


def _to_score_inputs(leg: dict) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    preds, payloads = {}, {}
    for qid, pq in leg["per_query"].items():
        preds[qid] = pq["events"]
        payloads[qid] = {"agents": pq["agents"],
                         "final_report": pq.get("final_report", {})}
    return preds, payloads


def _write_markdown(report: dict) -> str:
    lines = ["# Provider comparison: meta vs baseten (investigation LLM path)",
             "",
             f"- dry_run: `{report['dry_run']}`",
             f"- code_revision: `{report.get('code_revision')}`",
             f"- dataset: `{report['dataset_id']} v{report['dataset_version']}`",
             f"- queries: `{report['n_queries']}`",
             ""]
    for provider in PROVIDERS:
        leg = report["legs"][provider]
        lines.append(f"## {provider}")
        lines.append("")
        det = leg["scores"]["event_detection"].get("overall", {})
        lines.append(f"- detection P/R/F1: `{det.get('precision')}/{det.get('recall')}/"
                     f"{det.get('f1')}` (tp={det.get('tp')} fp={det.get('fp')} "
                     f"fn={det.get('fn')})")
        ev = leg["scores"]["evidence_quality"]
        lines.append(f"- evidence_coverage: `{ev.get('evidence_coverage')}` "
                     f"reference_validity: `{ev.get('reference_validity_rate')}`")
        lines.append(f"- usage: `{leg['usage']}`")
        lat = leg["latencies_ms"]
        lines.append(f"- latency_ms (n={len(lat)}): `{lat}`")
        lines.append("")
    lines.append("Baseten cost_usd is provider-unreported (contract pricing); "
                 "0.0 means unavailable, not free.")
    return "\n".join(lines) + "\n"


async def main_async(args: argparse.Namespace) -> dict:
    live = bool(args.live)
    if live and os.environ.get(LIVE_ENV_VAR) != "1":
        raise SystemExit(f"Live mode requires {LIVE_ENV_VAR}=1 in the environment.")
    if live:
        for key, value in _STASHED_KEYS.items():
            if value is not None:
                os.environ[key] = value
    else:
        for var in ("MODEL_API_KEY", "META_API_KEY", "BASETEN_API_KEY"):
            os.environ.pop(var, None)

    ds = load_v2()
    queries = ds["queries"][:args.max_queries] if args.max_queries else ds["queries"]
    if live:
        from app.config import get_settings
        from app.provider_config import resolve_text_spec
        _spec_settings = get_settings()
        if not resolve_text_spec(_spec_settings, provider="meta").api_key:
            raise SystemExit(
                "Live mode needs MODEL_API_KEY (or META_API_KEY) for the meta leg.")
        if not resolve_text_spec(_spec_settings, provider="baseten").api_key:
            raise SystemExit(
                "Live mode needs BASETEN_API_KEY for the baseten leg.")
        spent = _real_db_spend()
        if spent is not None:
            from app.config import get_settings
            remaining = get_settings().budget_limit_usd - spent
            if remaining < args.min_remaining_usd:
                raise SystemExit(
                    f"Budget floor: ${remaining:.2f} remaining < "
                    f"${args.min_remaining_usd:.2f}. Aborting before spending.")

    fixture_ids: dict[str, str] = build_reference.FIXTURE_RECORDINGS
    recording_index = list(ds.get("recordings", []))
    for rec in ds.get("recordings", []):
        fid = fixture_ids.get(rec["key"])
        if fid:
            recording_index.append({"key": fid, "recording_id": fid,
                                    "duration_seconds": rec["duration_seconds"]})
    legs: dict[str, Any] = {}
    for provider in PROVIDERS:
        print(f"[{provider}] leg start (live={live})", flush=True)
        leg = await _run_leg(provider, queries, live)
        preds, payloads = _to_score_inputs(leg)
        section = compare.score_mode(provider, preds, payloads, ds, recording_index)
        legs[provider] = {"scores": section, "usage": leg["usage"],
                          "latencies_ms": leg["latencies_ms"],
                          "n_queries": len(queries)}

    report = {
        "dry_run": not live,
        "code_revision": _code_revision(),
        "dataset_id": ds.get("dataset_id"),
        "dataset_version": ds.get("version"),
        "n_queries": len(queries),
        "scope": ("multi-agent text path only; fixture events pre-indexed, "
                  "vision provider fixed"),
        "legs": legs,
        "note": ("Dry-run legs use mocked LLM calls: scores exercise mechanics "
                 "only." if not live else
                 "Live legs spent real credits on both providers; see usage."),
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2))
    REPORT_MD.write_text(_write_markdown(report))
    print(f"Wrote {REPORT_JSON} and {REPORT_MD}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--min-remaining-usd", type=float,
                        default=DEFAULT_MIN_REMAINING_USD)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
