#!/usr/bin/env python3
"""Temporal-detection regression eval (synthetic scripted scenarios).

Runs every scenario in eval/temporal_dataset.json through the real
``run_temporal_detection`` pipeline (in-process, mocked vision, no network,
no API keys) and scores:

- recall / precision / F1 (event matching on category + span)
- localization accuracy (timestamp + boundary errors)
- false-positive rate on the negative scenarios (must-not-alert)
- grounding (evidence-frame validity within recording bounds)
- latency (wall-clock per pipeline run on THIS machine, mocked vision)

    backend/.venv/bin/python eval/temporal_eval.py [--report PATH]

Outputs: eval/temporal_eval_report.json (default)

Honesty notes (also embedded in the report):
- All scenarios are scripted synthetic observations; video fixtures are
  ffmpeg color segments. Metrics measure scripted-input behavior, NOT
  real-incident performance (unresolved-fidelity).
- ffmpeg-dependent scenarios are SKIPPED (not passed) when the binary is
  absent; skips are reported explicitly and excluded from denominators.
- Latencies measure framework overhead with mocked vision, not model cost.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).parent
REPO_ROOT = EVAL_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "tests"))

# Offline safety: no live model calls anywhere in this process.
for _var in ("MODEL_API_KEY", "META_API_KEY"):
    os.environ.pop(_var, None)

from eval import metrics as M  # noqa: E402
from tests.temporal_regression_fixtures import (  # noqa: E402
    SCENARIO_SPECS,
    make_regression_video,
    seed_observations,
)

DATASET_FILE = EVAL_DIR / "temporal_dataset.json"
DEFAULT_REPORT = EVAL_DIR / "temporal_eval_report.json"


def _code_revision() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(REPO_ROOT))
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return None


async def _run_scenario(scenario: str, gt_entry: dict[str, Any],
                        tmp_root: Path) -> dict[str, Any]:
    """Seed one scenario and run the real pipeline. Returns raw outcome."""
    from app.database import create_tables, get_db
    from app.meta_client import MockMetaClient
    from app.provider import build_accounted_client
    from app.storage import LocalStorageBackend
    from app.temporal_pipeline import run_temporal_detection

    spec = SCENARIO_SPECS[scenario]
    duration = float(gt_entry.get("duration_seconds", spec["duration"]))
    work = tmp_root / scenario
    work.mkdir(parents=True, exist_ok=True)
    db_path = str(work / "eval.db")
    await create_tables(db_path)
    storage = LocalStorageBackend(work)
    await storage.ensure_dirs()

    rec_id = str(uuid.uuid4())
    video_rel = "videos/missing.mp4"
    if spec.get("video") is not None:
        if shutil.which("ffmpeg") is None:
            return {"scenario": scenario, "gt_id": gt_entry.get("id"),
                    "skipped": True,
                    "skip_reason": "ffmpeg binary not available"}
        make_regression_video(
            storage.videos_dir / f"{rec_id}.mp4", duration, spec["video"])
        video_rel = f"videos/{rec_id}.mp4"

    now = "2026-01-01T00:00:00+00:00"
    async with get_db(db_path) as db:
        await db.execute(
            """INSERT INTO recordings(recording_id, original_filename,
                upload_timestamp, video_path, processing_status,
                frame_count, video_duration_seconds, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (rec_id, "temporal-eval.mp4", now, video_rel, "INDEXED", 0,
             duration, now, now))
        await db.commit()
        await seed_observations(db, rec_id, spec["script"])

    delegate = MockMetaClient(scenario_map={})
    client = build_accounted_client(
        delegate=delegate, db_path=db_path, provider="meta")
    t0 = time.perf_counter()
    result = await run_temporal_detection(
        recording_id=rec_id, db_path=db_path, storage=storage,
        meta_client=client, start=0.0, end=duration, reason="temporal-eval")
    wall = time.perf_counter() - t0
    return {"scenario": scenario, "gt_id": gt_entry.get("id"),
            "skipped": False, "recording_id": rec_id,
            "duration_seconds": duration,
            "events": result["events"], "partial": result["partial"],
            "status": result["status"],
            "uncovered_spans": result["uncovered_spans"],
            "timestamp_source": result["timestamp_source"],
            "wall_seconds": wall}


def _to_prediction(ev: dict[str, Any], scenario: str) -> dict[str, Any]:
    return {"event_id": ev.get("event_id"), "scenario": scenario,
            "event_type": ev.get("category"),
            "timestamp_seconds": ev.get("timestamp_seconds"),
            "start_seconds": ev.get("start_seconds"),
            "end_seconds": ev.get("end_seconds"),
            "confidence": ev.get("confidence")}


async def main_async(report_path: Path) -> dict[str, Any]:
    dataset = json.loads(DATASET_FILE.read_text())
    rules = dataset.get("match_rules", {})
    gt_events = list(dataset.get("events", []))
    negatives = list(dataset.get("negative_scenarios", []))

    outcomes: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="temporal-eval-") as tmp:
        tmp_root = Path(tmp)
        for gt in gt_events:
            outcomes.append(await _run_scenario(gt["scenario"], gt, tmp_root))
        for neg in negatives:
            outcomes.append(await _run_scenario(
                neg["scenario"], {"id": neg["id"],
                                  "duration_seconds": neg["duration_seconds"]},
                tmp_root))

    by_gt = {o["gt_id"]: o for o in outcomes}
    skipped = sorted(o["gt_id"] for o in outcomes if o.get("skipped"))
    ran = [o for o in outcomes if not o.get("skipped")]

    # Detection scoring over runnable GT only; negative scenarios contribute
    # predictions (potential FPs) but no GT.
    runnable_gt = [g for g in gt_events if g["id"] not in set(skipped)]
    predictions: list[dict[str, Any]] = []
    for o in ran:
        for ev in o["events"]:
            predictions.append(_to_prediction(ev, o["scenario"]))
    detection = M.score_event_detection(
        runnable_gt, predictions,
        time_tolerance_seconds=float(rules.get("time_tolerance_seconds", 2.0)),
        require_camera=bool(rules.get("require_camera", False)),
        require_event_type=bool(rules.get("require_event_type", True)),
        keyword_overlap_min=int(rules.get("keyword_overlap_min", 0)))
    temporal = M.score_temporal_accuracy(
        detection["matching"], runnable_gt, predictions)

    # False-positive accounting on negatives.
    neg_pred_counts = {o["gt_id"]: len(o["events"]) for o in ran
                       if o["gt_id"].startswith("REG-N")}
    neg_ran = [g for g in (n["id"] for n in negatives) if g not in set(skipped)]
    fp_on_negatives = sum(neg_pred_counts.get(g, 0) for g in neg_ran)
    fp_rate = (fp_on_negatives / len(neg_ran)) if neg_ran else None

    # Grounding: every predicted evidence frame must resolve inside the
    # recording bounds. Claims carry dict refs {recording_id,
    # timestamp_seconds}; validity is bounds-existence only.
    claims: list[dict[str, Any]] = []
    recordings_index: list[dict[str, Any]] = []
    for o in ran:
        recordings_index.append({"key": o["recording_id"],
                                 "recording_id": o["recording_id"],
                                 "duration_seconds": o["duration_seconds"]})
        for ev in o["events"]:
            refs = [{"recording_id": o["recording_id"],
                     "timestamp_seconds": f.get("timestamp_seconds")}
                    for f in (ev.get("evidence_frames") or [])]
            clip = ev.get("clip") or {}
            if clip.get("start_seconds") is not None:
                refs.append({"recording_id": o["recording_id"],
                             "timestamp_seconds": clip.get("start_seconds")})
            claims.append({"claim_id": f"{o['gt_id']}:{ev.get('event_id')}",
                           "text": f"{ev.get('category')} "
                                   f"{ev.get('start_seconds')}-"
                                   f"{ev.get('end_seconds')}",
                           "evidence": refs, "supported": None,
                           "contradicted": None})
    grounding = M.score_evidence_quality(claims, recordings_index)

    latencies = [o["wall_seconds"] for o in ran]
    latency = {"n": len(latencies),
               "mean_seconds": statistics.fmean(latencies) if latencies else None,
               "max_seconds": max(latencies) if latencies else None,
               "min_seconds": min(latencies) if latencies else None,
               "per_scenario": {o["gt_id"]: round(o["wall_seconds"], 3)
                                for o in ran}}

    report = {
        "dataset": {"dataset_id": dataset.get("dataset_id"),
                    "version": dataset.get("version")},
        "code_revision": _code_revision(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "coverage": {"gt_total": len(gt_events),
                     "negatives_total": len(negatives),
                     "ran": sorted(o["gt_id"] for o in ran),
                     "skipped": skipped,
                     "skip_note": ("Skipped scenarios are excluded from every "
                                   "denominator; a skip is not a pass.")},
        "detection": detection["overall"],
        "by_event_type": detection["by_event_type"],
        "matching": {"matched_gt_ids": detection["matching"]["matched_gt_ids"],
                     "false_negative_gt_ids": detection["matching"]["false_negative_gt_ids"],
                     "false_positive_pred_indices": detection["matching"]["false_positive_pred_indices"]},
        "localization": {
            "relative_error": temporal["relative_error"],
            "start_boundary_error": temporal["start_boundary_error"],
            "end_boundary_error": temporal["end_boundary_error"],
            "n_matched": temporal["n_matched"],
            "n_missing_boundary": temporal["n_missing_boundary"],
            "per_match": temporal["per_match"]},
        "false_positives": {
            "fp_on_negatives": fp_on_negatives,
            "negatives_evaluated": len(neg_ran),
            "fp_per_negative_scenario": fp_rate,
            "per_negative": {g: neg_pred_counts.get(g, 0) for g in neg_ran}},
        "grounding": {
            "claims_total": grounding["n_claims"],
            "claims_with_evidence": grounding["claims_with_evidence"],
            "coverage": grounding["evidence_coverage"],
            "reference_validity": grounding["reference_validity_rate"],
            "references_total": grounding["n_references"],
            "references_valid": grounding["n_valid_references"],
            "invalid_refs": grounding["invalid_references"],
            "support_rate": grounding["evidence_support_rate"],
            "support_note": ("support_rate is None: no human annotations "
                             "exist; validity is bounds-existence only.")},
        "latency": latency,
        "per_scenario": [
            {"gt_id": o["gt_id"], "scenario": o["scenario"],
             "n_events": len(o["events"]),
             "categories": [e.get("category") for e in o["events"]],
             "status": o["status"], "partial": o["partial"],
             "timestamp_source": o["timestamp_source"],
             "wall_seconds": round(o["wall_seconds"], 3)}
            for o in ran],
        "honesty": {
            "fidelity": "unresolved-fidelity: scripted synthetic observations "
                        "and ffmpeg color-segment videos only; no labeled "
                        "incident footage in the workspace.",
            "vision": "mocked (MockMetaClient); dense video frames add no "
                      "observations. Metrics measure pipeline logic, not "
                      "model perception.",
            "latency": "wall-clock on this machine with mocked vision; "
                       "framework overhead only."},
    }
    report_path.write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = asyncio.run(main_async(args.report))
    det = report["detection"]
    loc = report["localization"]["relative_error"]
    fp = report["false_positives"]
    gr = report["grounding"]
    lat = report["latency"]
    print(f"scenarios ran={len(report['coverage']['ran'])} "
          f"skipped={report['coverage']['skipped'] or 'none'}")
    print(f"recall={det['recall']} precision={det['precision']} "
          f"f1={det['f1']} (tp={det['tp']} fp={det['fp']} fn={det['fn']})")
    print(f"localization mean_abs_err={loc['mean']}s n={loc['n']}")
    print(f"false_positives on negatives={fp['fp_on_negatives']}/"
          f"{fp['negatives_evaluated']} "
          f"(fp/scenario={fp['fp_per_negative_scenario']})")
    print(f"grounding coverage={gr['coverage']} "
          f"validity={gr['reference_validity']} "
          f"support_rate={gr['support_rate']}")
    print(f"latency mean={lat['mean_seconds']:.3f}s "
          f"max={lat['max_seconds']:.3f}s n={lat['n']}")
    print(f"report: {args.report}")


if __name__ == "__main__":
    main()
