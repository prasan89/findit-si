#!/usr/bin/env python3
"""Versioned evaluation-dataset loader (Phase 5).

- Native format: eval/dataset_v2.json (dataset_id + version).
- Backwards compatible: eval/ground_truth.json (unversioned v1) is upgraded
  to the v2 shape on load via `load_v1_as_v2()`.

No I/O beyond reading the dataset files; no model calls.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).parent
DATASET_V2_FILE = EVAL_DIR / "dataset_v2.json"
GROUND_TRUTH_V1_FILE = EVAL_DIR / "ground_truth.json"

CURRENT_VERSION = "2.0.0"
CURRENT_DATASET_ID = "realitytrace-eval"


def _midpoint(ts_range: list[float]) -> float:
    return (float(ts_range[0]) + float(ts_range[1])) / 2.0


def load_v2(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the v2 dataset. Raises ValueError on schema violations."""
    raw = json.loads((path or DATASET_V2_FILE).read_text())
    _validate_v2(raw)
    return raw


def _validate_v2(ds: dict[str, Any]) -> None:
    if ds.get("dataset_id") != CURRENT_DATASET_ID:
        raise ValueError(f"Unexpected dataset_id: {ds.get('dataset_id')!r}")
    if not ds.get("version"):
        raise ValueError("Dataset is missing required 'version'")

    rec_keys = set()
    for rec in ds.get("recordings", []):
        for field in ("key", "camera_name", "recording_start", "recording_timezone",
                      "duration_seconds"):
            if field not in rec:
                raise ValueError(f"Recording missing {field!r}: {rec}")
        rec_keys.add(rec["key"])

    event_ids = set()
    for ev in ds.get("events", []):
        for field in ("id", "recording_key", "camera_name", "event_type",
                      "start_seconds", "end_seconds", "expected_keywords"):
            if field not in ev:
                raise ValueError(f"Event missing {field!r}: {ev}")
        if ev["recording_key"] not in rec_keys:
            raise ValueError(f"Event {ev['id']} references unknown recording "
                             f"{ev['recording_key']!r}")
        if float(ev["start_seconds"]) > float(ev["end_seconds"]):
            raise ValueError(f"Event {ev['id']} has start > end")
        event_ids.add(ev["id"])

    for q in ds.get("queries", []):
        for field in ("id", "query", "expected_events"):
            if field not in q:
                raise ValueError(f"Query missing {field!r}: {q}")
        for gid in list(q.get("expected_events", [])) + list(q.get("ambiguous_events", [])):
            if gid not in event_ids:
                raise ValueError(f"Query {q['id']} references unknown event {gid!r}")

    cc = ds.get("cross_camera_associations", {})
    for section in ("valid", "invalid", "ambiguous"):
        entries = cc.get(section, [])
        pairs = entries if section == "valid" else entries
        for entry in pairs:
            pair = entry if isinstance(entry, list) else entry.get("pair", [])
            if len(pair) != 2 or any(g not in event_ids for g in pair):
                raise ValueError(f"Invalid cross-camera pair in {section!r}: {entry!r}")


def load_v1_as_v2(path: Path | None = None) -> dict[str, Any]:
    """Upgrade the unversioned v1 ground_truth.json to the v2 shape.

    v1 lacks recording linkage, absolute timestamps, negative/ambiguous
    queries, and labeled associations, so the upgrade derives what it can
    (recording starts, absolute times, GT-001<->GT-002 as AMBIGUOUS per the
    v2 annotation policy) and marks the rest unavailable.
    """
    v1 = json.loads((path or GROUND_TRUTH_V1_FILE).read_text())

    recordings = []
    cam_to_key: dict[str, str] = {}
    for i, cam in enumerate(v1.get("cameras", [])):
        key = f"rec-{cam['camera_name'].lower().replace(' ', '-')}"
        cam_to_key[cam["camera_name"]] = key
        recordings.append({
            "key": key,
            "clip": cam.get("clip", ""),
            "camera_id": f"cam-{cam['camera_name'].lower().replace(' ', '-')}",
            "camera_name": cam["camera_name"],
            "camera_location": cam.get("camera_location", ""),
            "recording_start": cam["recording_datetime"],
            "recording_timezone": cam.get("recording_timezone", "UTC"),
            "duration_seconds": float(cam.get("duration_seconds", 0.0)),
            "synthetic": True,
        })

    events = []
    for ev in v1.get("ground_truth_events", []):
        rec = next((r for r in recordings
                    if r["camera_name"] == ev["camera"]), recordings[0] if recordings else None)
        start, end = float(ev["timestamp_seconds_range"][0]), float(ev["timestamp_seconds_range"][1])
        abs_start = abs_end = None
        try:
            base = datetime.fromisoformat(rec["recording_start"])
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
            abs_start = datetime.fromtimestamp(base.timestamp() + start,
                                               tz=timezone.utc).isoformat()
            abs_end = datetime.fromtimestamp(base.timestamp() + end,
                                             tz=timezone.utc).isoformat()
        except Exception:
            pass
        events.append({
            "id": ev["id"],
            "dataset_version": CURRENT_VERSION,
            "recording_key": rec["key"] if rec else "",
            "camera_id": rec["camera_id"] if rec else "",
            "camera_name": ev["camera"],
            "recording_start": rec["recording_start"] if rec else "",
            "recording_timezone": rec["recording_timezone"] if rec else "UTC",
            "event_type": ev.get("event_type", "unknown"),
            "description": ev.get("notes", ""),
            "start_seconds": start,
            "end_seconds": end,
            "timestamp_seconds_range": [start, end],
            "absolute_start": abs_start,
            "absolute_end": abs_end,
            "absolute_time_reliable": abs_start is not None,
            "expected_keywords": list(ev.get("expected_keywords", [])),
            "evidence": [{"role": "during", "timestamp_seconds": _midpoint([start, end]),
                          "note": "Derived midpoint (v1 upgrade)."}],
            "visible_in_single_camera": bool(ev.get("visible_in_single_camera", True)),
            "limitations": ["synthetic_proxy"],
            "upgraded_from_v1": True,
        })

    queries = []
    for q in v1.get("queries", []):
        queries.append({
            "id": q["id"],
            "query": q["query"],
            "kind": "positive",
            "expected_events": list(q.get("expected_events", [])),
            "expected_not_returned": list(q.get("expected_not_returned", [])),
            "ambiguous_events": [],
            "min_expected_score": q.get("min_expected_score", 0.5),
        })

    # v1 cross_camera_candidate GT-001 <-> GT-002 becomes AMBIGUOUS under the
    # v2 annotation policy (identity unverifiable from color blocks).
    cc_pairs = set()
    for ev in v1.get("ground_truth_events", []):
        cand = ev.get("cross_camera_candidate")
        if cand:
            cc_pairs.add(tuple(sorted((ev["id"], cand))))
    ambiguous = [{"pair": list(p),
                  "reason": "Upgraded from v1 cross_camera_candidate; identity unverifiable."}
                 for p in sorted(cc_pairs)]

    return {
        "dataset_id": CURRENT_DATASET_ID,
        "version": f"{CURRENT_VERSION}+v1-upgrade",
        "supersedes": "eval/ground_truth.json (v1, upgraded in memory)",
        "footage_provenance": {
            "source": "synthetic",
            "real_footage_available": False,
        },
        "recordings": recordings,
        "events": events,
        "queries": queries,
        "cross_camera_associations": {
            "valid": [],
            "invalid": [],
            "invalid_note": "v1 does not label invalid pairs; use v2 for false-association scoring.",
            "ambiguous": ambiguous,
        },
        "expected_gaps": list(v1.get("expected_gaps", [])),
        "match_rules": dict(_default_match_rules()),
        "known_limitations": [
            "Upgraded from unversioned v1 ground truth; negative/ambiguous queries and invalid cross-camera labels are unavailable.",
        ],
    }


def _default_match_rules() -> dict[str, Any]:
    return {
        "time_tolerance_seconds": 2.0,
        "require_camera": True,
        "require_event_type": False,
        "keyword_overlap_min": 1,
        "assignment": "Greedy 1:1 assignment: GT events in id order take the lowest-timestamp-error unmatched prediction satisfying all rules.",
    }


def recording_by_key(ds: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((r for r in ds.get("recordings", []) if r["key"] == key), None)


def event_by_id(ds: dict[str, Any], event_id: str) -> dict[str, Any] | None:
    return next((e for e in ds.get("events", []) if e["id"] == event_id), None)


def composition(ds: dict[str, Any]) -> dict[str, Any]:
    """Return dataset size/composition summary (synthetic vs real split)."""
    recordings = ds.get("recordings", [])
    synthetic = sum(1 for r in recordings if r.get("synthetic", True))
    kinds: dict[str, int] = {}
    for q in ds.get("queries", []):
        kinds[q.get("kind", "positive")] = kinds.get(q.get("kind", "positive"), 0) + 1
    cc = ds.get("cross_camera_associations", {})
    return {
        "dataset_id": ds.get("dataset_id"),
        "version": ds.get("version"),
        "n_recordings": len(recordings),
        "n_recordings_synthetic": synthetic,
        "n_recordings_real": len(recordings) - synthetic,
        "n_events": len(ds.get("events", [])),
        "n_queries": len(ds.get("queries", [])),
        "queries_by_kind": kinds,
        "n_cc_valid": len(cc.get("valid", [])),
        "n_cc_invalid": len(cc.get("invalid", [])),
        "n_cc_ambiguous": len(cc.get("ambiguous", [])),
    }
