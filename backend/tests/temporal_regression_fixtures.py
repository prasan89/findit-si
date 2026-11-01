"""Synthetic temporal-regression fixtures (TEST-ONLY helper, not product code).

Why synthetic: the workspace contains no labeled incident footage. The
videos under ``backend/data/videos/`` are unlabeled uploads with no ground
truth, and ``eval/`` ships only synthetic color-block clips, so every
temporal-regression scenario below is generated: observation scripts drive
the detector deterministically, and optional ffmpeg videos exercise the
container-PTS / motion-prefilter path. Fidelity limits are documented per
scenario in :data:`SCENARIO_SPECS` and surfaced as ``unresolved-fidelity``
by the eval runner — a green run here means "pipeline behaves on scripted
inputs", never "validated on real incidents".

Scenario vocabulary lives ONLY in this module and the eval dataset. Product
code under ``backend/app/`` must not reference scenario ids, scripted scene
sentences, or fixture filenames (enforced by
``test_product_code_has_no_fixture_strings``).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Observation scripts: (timestamp_s, object_label, scene_description, score).
# Positive scripts use generic observable-action language; negative scripts
# describe ordinary scenes that must not produce candidates.
# ---------------------------------------------------------------------------

# Multi-stage incident-like sequence: approach -> contact -> removal ->
# departure. Gaps are small enough to chain into one sequence (default
# sequence gap 8s) with distinct stage boundaries.
SCRIPT_INCIDENT_MULTI_STAGE = (
    (2.0, "person", "a person rushing toward another person near the gate", 0.90),
    (4.0, "person", "two people grabbing each other close together", 0.92),
    (4.8, "person", "two people pushing and shoving each other", 0.88),
    (6.5, "bag", "a bag being taken from a bench by a passing person", 0.85),
    (9.0, "person", "a person sprinting away from the area quickly", 0.90),
)

# Brief interaction: one frame only. Must be kept (not dropped) and flagged.
SCRIPT_BRIEF_CONTACT = (
    (5.0, "person", "a person grabbing another person's arm briefly", 0.80),
)

# Boundary: event at the very start of the recording.
SCRIPT_BOUNDARY_START = (
    (0.2, "person", "a person tripping and falling to the ground", 0.85),
    (0.6, "person", "a person lying on the ground after falling down", 0.82),
)

# Boundary: event ending at the recording's declared duration (30s).
SCRIPT_BOUNDARY_END = (
    (29.2, "person", "a person running away from the platform fast", 0.87),
    (29.6, "person", "a person sprinting away toward the far exit", 0.84),
)

# Degraded-footage scripts reuse observable actions; the video specs below
# carry the degradation (resolution / lighting).
SCRIPT_LOW_RES_CONTACT = (
    (3.0, "person", "two people grabbing each other near the doorway", 0.80),
    (3.5, "person", "two people pushing each other in the doorway", 0.78),
)
SCRIPT_NIGHT_APPROACH_DEPART = (
    (2.0, "person", "a person running toward another person in the dark", 0.75),
    (7.0, "person", "a person fleeing the area into the darkness", 0.75),
)

# Normal interactions: must NOT produce candidates or events.
SCRIPT_NORMAL_PASSING = (
    (2.0, "person", "a person walking slowly past a bench", 0.90),
    (6.0, "person", "two people standing apart talking calmly", 0.88),
    (10.0, "person", "a person sitting on a bench reading a newspaper", 0.92),
)
SCRIPT_NORMAL_GREETING = (
    (3.0, "person", "two people shaking hands briefly then stepping apart", 0.85),
    (8.0, "person", "a person waving hello to a neighbor across the path", 0.90),
)
SCRIPT_EMPTY_SCENE = (
    (2.0, "corridor", "an empty corridor with no people visible", 0.95),
    (8.0, "corridor", "the same empty corridor, nothing moving", 0.95),
)

# ---------------------------------------------------------------------------
# Scenario specs: expectations consumed by tests AND eval/temporal_eval.py.
# expected_spans: list of [start, end] the pipeline must report (span overlap
# matching); expected_category: required category for single-event scenarios.
# ---------------------------------------------------------------------------

SCENARIO_SPECS: dict[str, dict] = {
    "incident-multi-stage": {
        "script": SCRIPT_INCIDENT_MULTI_STAGE,
        "duration": 30.0,
        "expect_events": 1,
        "expected_spans": [[2.0, 9.0]],
        "expected_category": "possible-assault",
        "expected_stages": ["approach", "contact", "removal", "departure"],
        "video": {"cuts": [2.0, 9.0], "size": "320x240"},
        "fidelity": "scripted observations, not real incident footage",
    },
    "brief-contact": {
        "script": SCRIPT_BRIEF_CONTACT,
        "duration": 15.0,
        "expect_events": 1,
        "expected_spans": [[5.0, 5.0]],
        "expected_category": "confrontation",
        "single_frame": True,
        "video": None,
        "fidelity": "scripted single observation; no real brief-event clip",
    },
    "boundary-start": {
        "script": SCRIPT_BOUNDARY_START,
        "duration": 20.0,
        "expect_events": 1,
        "expected_spans": [[0.2, 0.6]],
        "expected_category": "sudden-fall",
        "video": None,
        "fidelity": "scripted observations at t≈0",
    },
    "boundary-end": {
        "script": SCRIPT_BOUNDARY_END,
        "duration": 30.0,
        "expect_events": 1,
        "expected_spans": [[29.2, 29.6]],
        "expected_category": "suspicious-approach-departure",
        "video": None,
        "fidelity": "scripted observations at t≈duration",
    },
    "low-res": {
        "script": SCRIPT_LOW_RES_CONTACT,
        "duration": 12.0,
        "expect_events": 1,
        "expected_spans": [[3.0, 3.5]],
        "expected_category": "confrontation",
        "video": {"cuts": [3.0], "size": "160x120"},
        "fidelity": "downscaled synthetic video; scripted observations",
    },
    "night": {
        "script": SCRIPT_NIGHT_APPROACH_DEPART,
        "duration": 12.0,
        "expect_events": 1,
        "expected_spans": [[2.0, 7.0]],
        "expected_category": "suspicious-approach-departure",
        "video": {"cuts": [2.0, 7.0], "size": "320x240", "night": True},
        "fidelity": "darkened synthetic video; scripted observations",
    },
    "normal-passing": {
        "script": SCRIPT_NORMAL_PASSING,
        "duration": 15.0,
        "expect_events": 0,
        "expected_spans": [],
        "video": None,
        "fidelity": "scripted benign observations",
    },
    "normal-greeting": {
        "script": SCRIPT_NORMAL_GREETING,
        "duration": 15.0,
        "expect_events": 0,
        "expected_spans": [],
        "video": None,
        "fidelity": "scripted benign observations",
    },
    "empty-scene": {
        "script": SCRIPT_EMPTY_SCENE,
        "duration": 10.0,
        "expect_events": 0,
        "expected_spans": [],
        "video": {"static": True, "size": "320x240"},
        "fidelity": "static synthetic video; no motion expected",
    },
}

POSITIVE_SCENARIOS = [k for k, v in SCENARIO_SPECS.items() if v["expect_events"] > 0]
NEGATIVE_SCENARIOS = [k for k, v in SCENARIO_SPECS.items() if v["expect_events"] == 0]


async def seed_observations(db, recording_id: str, script) -> int:
    """Insert scripted observations for a recording. Returns row count."""
    now = "2026-01-01T00:00:00+00:00"
    for i, (ts, label, scene, score) in enumerate(script):
        await db.execute(
            """INSERT INTO observations(
                observation_id, recording_id, frame_id,
                video_timestamp_seconds, object_label, object_attributes,
                scene_description, source_frame_path, detection_score,
                model_version, index_version, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"{recording_id}-obs-{i}", recording_id, f"frame-{i}",
             float(ts), label, "{}", scene,
             f"frames/{recording_id}/{i:04d}.jpg", float(score),
             "regression-script-v1", "1", now))
    await db.commit()
    return len(script)


def make_regression_video(path: Path, duration: float, spec: dict) -> Path:
    """Build a synthetic fixture video with ffmpeg.

    spec keys: cuts (scene-cut timestamps), size (WxH), night (darken),
    static (single flat segment, no cuts). Cuts are hard color changes so
    the ffmpeg scene detector fires deterministically. Raises RuntimeError
    when ffmpeg is unavailable — callers must skip honestly, never fake.
    """
    import shutil

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg binary not available")
    size = spec.get("size", "320x240")
    night = spec.get("night", False)
    rate = 10
    filt = ",eq=brightness=-0.7" if night else ""
    path.parent.mkdir(parents=True, exist_ok=True)

    if spec.get("static"):
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
               f"color=c=gray:duration={duration}:size={size}:rate={rate}",
               "-pix_fmt", "yuv420p", str(path)]
        subprocess.run(cmd, check=True, capture_output=True)
        return path

    cuts = sorted(float(c) for c in spec.get("cuts", []))
    bounds = [0.0] + cuts + [duration]
    colors = ["blue", "red", "green", "yellow", "purple", "orange"]
    inputs: list[str] = []
    for i in range(len(bounds) - 1):
        seg = bounds[i + 1] - bounds[i]
        if seg <= 0:
            continue
        inputs += ["-f", "lavfi", "-i",
                   f"color=c={colors[i % len(colors)]}:duration={seg}"
                   f":size={size}:rate={rate}"]
    n = (len(inputs) // 4) or 1
    if n == 1:
        cmd = (["ffmpeg", "-y", "-v", "error"] + inputs +
               (["-vf", filt.lstrip(",")] if filt else []) +
               ["-pix_fmt", "yuv420p", str(path)])
    else:
        chain = "".join(f"[{i}]" for i in range(n))
        vf = f"{chain}concat=n={n}:v=1{filt}"
        cmd = (["ffmpeg", "-y", "-v", "error"] + inputs +
               ["-filter_complex", vf, "-pix_fmt", "yuv420p", str(path)])
    subprocess.run(cmd, check=True, capture_output=True)
    return path
