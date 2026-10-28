#!/usr/bin/env python3
"""Build deterministic reference predictions for offline evaluation (Phase 5).

Creates a fixture database mirroring the v2 dataset's synthetic scenario,
runs BOTH investigation modes against it (Standard `investigate()` and the
multi-agent orchestrator), and writes eval/reference_predictions.json.

Determinism + safety guarantees:
- Fixed recording/event IDs (no random UUIDs in the output).
- API keys are stripped in-process, so LLM agents take the MOCKED path and
  NO network calls are possible. Zero API cost.
- No backend server required; imports the backend app directly.

Volatile fields (investigation_id, trace_id, latencies, timestamps) are
excluded from the stable prediction payload; run metadata is stored
separately under "run_meta" and labeled non-comparable.

Usage:
    backend/.venv/bin/python eval/build_reference.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT))

# Offline safety: remove API keys BEFORE importing app modules so no live
# model call can happen anywhere in this process.
for _var in ("MODEL_API_KEY", "META_API_KEY"):
    os.environ.pop(_var, None)

from eval.dataset import load_v2  # noqa: E402
from app.database import create_tables, get_db  # noqa: E402
from app.investigate import investigate  # noqa: E402
from app.correlator import correlate_across_cameras  # noqa: E402
from app.agents.orchestrator import InvestigationOrchestrator  # noqa: E402

EVAL_DIR = Path(__file__).parent
REFERENCE_FILE = EVAL_DIR / "reference_predictions.json"

# Fixed fixture IDs (stable across runs).
FIXTURE_RECORDINGS = {
    "rec-entrance": "eval-ref-entrance",
    "rec-hallway": "eval-ref-hallway",
    "rec-parking": "eval-ref-parking",
}
FIXTURE_EVENTS = {
    "GT-001": "EV-REF-001",
    "GT-002": "EV-REF-002",
    "GT-003": "EV-REF-003",
}

# GT-faithful synthetic scene content: disjoint descriptions per camera so
# retrieval is unambiguous and cross-camera appearance overlap is zero.
# Phase 4: observations (not events) are seeded — one per second across each
# GT window — and the REAL event extractor builds the events. Single-frame
# confirmation is covered by unit tests (backend/tests/test_phase4_visual.py).
FIXTURE_CONTENT = {
    "GT-001": {
        "object_label": "person figure",
        "object_color": "red",
        "scene_description": "Red person-like figure detected near entrance",
        "window": (3.0, 8.0),
    },
    "GT-002": {
        "object_label": "bag object",
        "object_color": "blue",
        "scene_description": "Blue bag object detected in hallway",
        "window": (5.0, 12.0),
    },
    "GT-003": {
        "object_label": "vehicle",
        "object_color": "green",
        "scene_description": "Green vehicle detected in parking lot",
        "window": (2.0, 6.0),
    },
}


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


async def _seed_fixture_db(db_path: str, ds: dict) -> None:
    """Seed recordings + frame-level observations, then run the real pipeline.

    Observations mirror 1fps model outputs across each GT window. The real
    event extractor groups them into events (multi-frame confirmation drops
    the single-frame noise probe), and event IDs are renamed to fixed values
    for deterministic output.
    """
    from app.event_extractor import extract_events_for_recording

    await create_tables(db_path)
    rec_by_key = {r["key"]: r for r in ds["recordings"]}
    gt_by_id = {e["id"]: e for e in ds["events"]}
    async with get_db(db_path) as db:
        for key, rid in FIXTURE_RECORDINGS.items():
            rec = rec_by_key[key]
            await db.execute(
                """INSERT OR REPLACE INTO recordings(
                    recording_id, original_filename, upload_timestamp,
                    recording_datetime, recording_timezone, video_path,
                    processing_status, frame_count, camera_name, camera_location,
                    created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, f"{key}.mp4", "2024-01-15T00:00:00+00:00",
                 rec["recording_start"], rec["recording_timezone"],
                 f"videos/{key}.mp4", "INDEXED", 10,
                 rec["camera_name"], rec["camera_location"],
                 "2024-01-15T00:00:00+00:00", "2024-01-15T00:00:00+00:00"))
        obs_n = 0
        for gt_id, content in FIXTURE_CONTENT.items():
            gt = gt_by_id[gt_id]
            rid = FIXTURE_RECORDINGS[gt["recording_key"]]
            start, end = content["window"]
            ts = start
            while ts <= end + 1e-9:
                await db.execute(
                    """INSERT INTO observations(
                        observation_id, recording_id, frame_id,
                        video_timestamp_seconds, recording_datetime, recording_timezone,
                        object_label, object_attributes, scene_description,
                        source_frame_path, detection_score, model_version,
                        index_version, content_hash, created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"obs-{gt_id}-{ts:.0f}", rid, f"{rid}_{ts:.3f}", ts,
                     rec_by_key[gt["recording_key"]]["recording_start"],
                     rec_by_key[gt["recording_key"]]["recording_timezone"],
                     content["object_label"],
                     json.dumps({"color": content["object_color"]}),
                     content["scene_description"],
                     f"frames/{rid}/frame_{ts:.0f}.jpg", 0.8, "mock-v1",
                     "1", f"hash-{gt_id}-{ts:.0f}",
                     "2024-01-15T00:00:00+00:00"))
                obs_n += 1
                ts += 1.0
        await db.commit()
    print(f"  Seeded {obs_n} window observations")

    # Run the real extractor per recording.
    total_events = 0
    for rid in FIXTURE_RECORDINGS.values():
        total_events += await extract_events_for_recording(rid, db_path)
    print(f"  Extractor produced {total_events} events "
          f"(expected {len(FIXTURE_EVENTS)})")

    # Rename to fixed event IDs for deterministic reference output
    # (one event per recording in this fixture).
    async with get_db(db_path) as db:
        for rid in FIXTURE_RECORDINGS.values():
            gt_id = next(e["id"] for e in ds["events"]
                         if FIXTURE_RECORDINGS[e["recording_key"]] == rid)
            await db.execute(
                "UPDATE events SET event_id = ? WHERE recording_id = ?",
                (FIXTURE_EVENTS[gt_id], rid))
        await db.commit()


def _event_to_pred(ev) -> dict:
    d = ev.model_dump() if hasattr(ev, "model_dump") else dict(ev)
    return {
        "event_id": d.get("event_id"),
        "recording_id": d.get("recording_id"),
        "camera_name": d.get("camera_name"),
        "timestamp_seconds": d.get("timestamp_seconds"),
        "start_seconds": d.get("start_seconds"),
        "end_seconds": d.get("end_seconds"),
        "timestamp_absolute": d.get("timestamp_absolute"),
        "event_type": d.get("event_type"),
        "action_description": d.get("action_description"),
        "scene_description": d.get("scene_description"),
        "object_descriptions": d.get("object_descriptions", []),
        "observed_facts": d.get("observed_facts", []),
        "inferred_claims": d.get("inferred_claims", []),
        "confidence": d.get("confidence"),
        "evidence_frames": d.get("evidence_frames", []),
        "cross_camera_links": d.get("cross_camera_links", []),
    }


async def main() -> None:
    ds = load_v2()
    tmp = tempfile.mkdtemp(prefix="realitytrace-ref-")
    db_path = str(Path(tmp) / "fixture.db")
    await _seed_fixture_db(db_path, ds)

    recording_ids = list(FIXTURE_RECORDINGS.values())
    n_links = await correlate_across_cameras(db_path, recording_ids)

    standard: dict[str, dict] = {}
    multi_agent: dict[str, dict] = {}
    orchestrator = InvestigationOrchestrator(db_path=db_path, budget_usd=0.01)

    for q in ds["queries"]:
        qid = q["id"]
        # Standard mode (pure retrieval — deterministic, no model calls).
        t0 = time.monotonic()
        result = await investigate(query=q["query"], db_path=db_path,
                                   recording_ids=recording_ids)
        std_ms = (time.monotonic() - t0) * 1000
        standard[qid] = {
            "query": q["query"],
            "events": [_event_to_pred(ev) for ev in result.events],
            "evidence_gaps": result.evidence_gaps,
            "timestamp_ordering_reliable": result.timestamp_ordering_reliable,
            "latency_ms_noncomparable": round(std_ms, 1),
        }
        # Multi-agent mode (LLM agents take the MOCKED path — no keys, no network).
        t0 = time.monotonic()
        ma_result = await orchestrator.run(query=q["query"], recording_ids=recording_ids)
        ma_ms = (time.monotonic() - t0) * 1000
        multi_agent[qid] = {
            "query": q["query"],
            "agents": ma_result["agents"],
            "final_report": ma_result["final_report"],
            "status": ma_result["status"],
            "total_cost_usd": ma_result["total_cost_usd"],
            "model_config": ma_result.get("model_config"),
            "retrieved_event_ids": ma_result.get("retrieved_event_ids", []),
            "retrieved_evidence_ids": ma_result.get("retrieved_evidence_ids", []),
            "retrieved_events": ma_result.get("retrieved_events", []),
            "latency_ms_noncomparable": round(ma_ms, 1),
        }

    payload = {
        "dataset_id": ds["dataset_id"],
        "dataset_version": ds["version"],
        "code_revision": _code_revision(),
        "generated_note": ("Deterministic offline reference. Fixture DB mirrors the "
                           "synthetic v2 scenario with fixed IDs. No model calls were "
                           "made; LLM agents ran in MOCKED/SKIPPED states."),
        "fixture": {
            "recording_ids": FIXTURE_RECORDINGS,
            "event_ids": FIXTURE_EVENTS,
            "cross_camera_links_created": n_links,
        },
        "model_config": {
            "investigation_model": "muse-spark-1.3",
            "vision_model": "muse-spark-1.3",
            "mode": "offline-mocked (no API keys; zero live calls)",
        },
        "standard": standard,
        "multi_agent": multi_agent,
        "live_api_calls": 0,
        "live_tokens": None,
        "live_cost_usd": 0.0,
    }
    REFERENCE_FILE.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {REFERENCE_FILE} ({len(standard)} queries x 2 modes, "
          f"{n_links} CC links in fixture)")


if __name__ == "__main__":
    asyncio.run(main())
