"""Temporal-detection regression suite (scripted synthetic fixtures).

Covers the incident-like multi-stage sequence, brief interactions,
boundary events, degraded footage (low-res / night), normal interactions
that must NOT alert, and no-incident videos. All scenarios are scripted
observations plus optional synthetic ffmpeg videos — see
``temporal_regression_fixtures.py`` for the documented fidelity limits.
ffmpeg-dependent tests skip honestly when the binary is absent.
"""
import shutil
import time
import uuid

import pytest

from tests.temporal_regression_fixtures import (
    NEGATIVE_SCENARIOS,
    POSITIVE_SCENARIOS,
    SCENARIO_SPECS,
    make_regression_video,
    seed_observations,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg binary not available")


def _new_recording_id() -> str:
    return str(uuid.uuid4())


async def _insert_recording(db, recording_id, duration, video_rel="videos/missing.mp4"):
    now = "2026-01-01T00:00:00+00:00"
    await db.execute(
        """INSERT INTO recordings(recording_id, original_filename,
            upload_timestamp, video_path, processing_status, frame_count,
            video_duration_seconds, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (recording_id, "regression.mp4", now, video_rel, "INDEXED", 0,
         duration, now, now))
    await db.commit()


async def _run(db_path, storage, mock_client, recording_id, duration,
               script, start=0.0, end=None):
    from app.database import get_db
    from app.provider import build_accounted_client
    from app.temporal_pipeline import run_temporal_detection
    async with get_db(db_path) as db:
        await _insert_recording(db, recording_id, duration)
        await seed_observations(db, recording_id, script)
    client = build_accounted_client(
        delegate=mock_client, db_path=db_path, provider="meta")
    t0 = time.perf_counter()
    result = await run_temporal_detection(
        recording_id=recording_id, db_path=db_path, storage=storage,
        meta_client=client, start=start,
        end=duration if end is None else end, reason="regression")
    result["_wall_seconds"] = time.perf_counter() - t0
    return result


# ── Incident-like multi-stage sequence ───────────────────────────────────────

class TestIncidentSequence:
    async def test_multi_stage_sequence_detected_as_one_event(
            self, db_path, storage, mock_client):
        spec = SCENARIO_SPECS["incident-multi-stage"]
        result = await _run(db_path, storage, mock_client,
                            _new_recording_id(), spec["duration"], spec["script"])
        assert len(result["events"]) == 1
        ev = result["events"][0]
        assert ev["category"] == "possible-assault"
        assert set(spec["expected_stages"]) <= set(ev["stages"])
        assert ev["start_seconds"] == pytest.approx(2.0, abs=0.01)
        assert ev["end_seconds"] == pytest.approx(9.0, abs=0.01)
        assert ev["single_frame_candidate"] is False
        assert ev["confirming_frame_count"] >= 2

    async def test_incident_event_carries_evidence_and_no_verdict(
            self, db_path, storage, mock_client):
        from app.temporal_correlator import is_crime_verdict
        spec = SCENARIO_SPECS["incident-multi-stage"]
        result = await _run(db_path, storage, mock_client,
                            _new_recording_id(), spec["duration"], spec["script"])
        ev = result["events"][0]
        assert not is_crime_verdict(ev["category"])
        assert ev["evidence_frames"], "during/before/after frames required"
        roles = {f["role"] for f in ev["evidence_frames"]}
        assert "during" in roles
        assert ev["observed_facts"], "observed facts required"
        assert ev["clip"] is not None
        assert ev["clip"]["provenance"]["event_start"] == pytest.approx(2.0)
        assert ev["clip"]["provenance"]["event_end"] == pytest.approx(9.0)
        assert 0.0 <= ev["confidence"] <= 1.0

    @needs_ffmpeg
    async def test_incident_with_video_uses_container_pts(
            self, db_path, storage, mock_client, tmp_path):
        spec = SCENARIO_SPECS["incident-multi-stage"]
        rec_id = _new_recording_id()
        video = make_regression_video(
            storage.videos_dir / f"{rec_id}.mp4",
            spec["duration"], spec["video"])
        assert video.exists()
        from app.database import get_db
        from app.provider import build_accounted_client
        from app.temporal_pipeline import run_temporal_detection
        async with get_db(db_path) as db:
            await _insert_recording(
                db, rec_id, spec["duration"],
                video_rel=f"videos/{rec_id}.mp4")
            await seed_observations(db, rec_id, spec["script"])
        client = build_accounted_client(
            delegate=mock_client, db_path=db_path, provider="meta")
        result = await run_temporal_detection(
            recording_id=rec_id, db_path=db_path, storage=storage,
            meta_client=client, start=0.0, end=spec["duration"],
            reason="regression-video")
        assert result["timestamp_source"] == "container_pts"
        assert len(result["events"]) == 1
        assert result["events"][0]["category"] == "possible-assault"


# ── Brief interactions ───────────────────────────────────────────────────────

class TestBriefInteractions:
    async def test_single_frame_candidate_kept_and_flagged(
            self, db_path, storage, mock_client):
        spec = SCENARIO_SPECS["brief-contact"]
        result = await _run(db_path, storage, mock_client,
                            _new_recording_id(), spec["duration"], spec["script"])
        assert len(result["events"]) == 1
        ev = result["events"][0]
        assert ev["single_frame_candidate"] is True
        assert ev["confidence"] < 0.6
        assert ev["category"] == "confrontation"

    async def test_include_single_frame_filter_excludes_brief_event(
            self, db_path, storage, mock_client):
        from app.temporal_pipeline import get_temporal_events
        spec = SCENARIO_SPECS["brief-contact"]
        rec_id = _new_recording_id()
        await _run(db_path, storage, mock_client, rec_id,
                   spec["duration"], spec["script"])
        kept = await get_temporal_events(
            rec_id, db_path, include_single_frame=True)
        dropped = await get_temporal_events(
            rec_id, db_path, include_single_frame=False)
        assert len(kept) == 1
        assert dropped == []


# ── Boundary events ──────────────────────────────────────────────────────────

class TestBoundaryEvents:
    async def test_event_at_recording_start(self, db_path, storage, mock_client):
        spec = SCENARIO_SPECS["boundary-start"]
        result = await _run(db_path, storage, mock_client,
                            _new_recording_id(), spec["duration"], spec["script"])
        assert len(result["events"]) == 1
        ev = result["events"][0]
        assert ev["start_seconds"] == pytest.approx(0.2, abs=0.01)
        assert ev["category"] == "sudden-fall"

    async def test_event_at_recording_end(self, db_path, storage, mock_client):
        spec = SCENARIO_SPECS["boundary-end"]
        result = await _run(db_path, storage, mock_client,
                            _new_recording_id(), spec["duration"], spec["script"])
        assert len(result["events"]) == 1
        ev = result["events"][0]
        assert ev["end_seconds"] == pytest.approx(29.6, abs=0.01)

    async def test_span_overlap_boundary_queries(
            self, db_path, storage, mock_client):
        from app.temporal_pipeline import get_temporal_events
        spec = SCENARIO_SPECS["boundary-end"]
        rec_id = _new_recording_id()
        await _run(db_path, storage, mock_client, rec_id,
                   spec["duration"], spec["script"])
        # Touching the event end counts as overlap; disjoint does not.
        assert len(await get_temporal_events(rec_id, db_path, start=29.6)) == 1
        assert await get_temporal_events(rec_id, db_path, end=29.1) == []
        assert len(await get_temporal_events(
            rec_id, db_path, start=0.0, end=30.0)) == 1


# ── Degraded footage (low-res / night) ───────────────────────────────────────

class TestDegradedFootage:
    @needs_ffmpeg
    @pytest.mark.parametrize("scenario", ["low-res", "night"])
    async def test_degraded_video_still_detects(
            self, db_path, storage, mock_client, tmp_path, scenario):
        spec = SCENARIO_SPECS[scenario]
        rec_id = _new_recording_id()
        video = make_regression_video(
            storage.videos_dir / f"{rec_id}.mp4",
            spec["duration"], spec["video"])
        assert video.exists() and video.stat().st_size > 0
        from app.database import get_db
        from app.provider import build_accounted_client
        from app.temporal_pipeline import run_temporal_detection
        async with get_db(db_path) as db:
            await _insert_recording(
                db, rec_id, spec["duration"], video_rel=f"videos/{rec_id}.mp4")
            await seed_observations(db, rec_id, spec["script"])
        client = build_accounted_client(
            delegate=mock_client, db_path=db_path, provider="meta")
        result = await run_temporal_detection(
            recording_id=rec_id, db_path=db_path, storage=storage,
            meta_client=client, start=0.0, end=spec["duration"],
            reason="regression-degraded")
        assert result["timestamp_source"] == "container_pts"
        assert len(result["events"]) == spec["expect_events"]
        assert result["events"][0]["category"] == spec["expected_category"]


# ── Normal interactions: must NOT alert ──────────────────────────────────────

class TestNormalInteractionsNoAlert:
    def test_benign_scripts_produce_no_candidates(self):
        from app.candidate_detector import detect_candidates
        for scenario in ("normal-passing", "normal-greeting", "empty-scene"):
            obs = [{"video_timestamp_seconds": ts, "object_label": label,
                    "scene_description": scene, "detection_score": score,
                    "observation_id": f"{scenario}-{ts}",
                    "source_frame_path": "frames/x.jpg"}
                   for ts, label, scene, score in
                   SCENARIO_SPECS[scenario]["script"]]
            assert detect_candidates(obs, []) == [], scenario

    @pytest.mark.parametrize("scenario", ["normal-passing", "normal-greeting"])
    async def test_normal_interactions_yield_zero_events(
            self, db_path, storage, mock_client, scenario):
        spec = SCENARIO_SPECS[scenario]
        result = await _run(db_path, storage, mock_client,
                            _new_recording_id(), spec["duration"], spec["script"])
        assert result["events"] == []


# ── No-incident videos ───────────────────────────────────────────────────────

class TestNoIncidentVideo:
    async def test_empty_scene_yields_zero_events(
            self, db_path, storage, mock_client):
        spec = SCENARIO_SPECS["empty-scene"]
        result = await _run(db_path, storage, mock_client,
                            _new_recording_id(), spec["duration"], spec["script"])
        assert result["events"] == []

    @needs_ffmpeg
    async def test_static_video_completes_with_zero_events(
            self, db_path, storage, mock_client, tmp_path):
        spec = SCENARIO_SPECS["empty-scene"]
        rec_id = _new_recording_id()
        video_path = storage.videos_dir / f"{rec_id}.mp4"
        make_regression_video(video_path, spec["duration"], spec["video"])
        from app.database import get_db
        from app.motion_prefilter import detect_candidate_windows
        from app.provider import build_accounted_client
        from app.temporal_pipeline import run_temporal_detection
        async with get_db(db_path) as db:
            await _insert_recording(
                db, rec_id, spec["duration"], video_rel=f"videos/{rec_id}.mp4")
            await seed_observations(db, rec_id, spec["script"])
        windows = await detect_candidate_windows(
            video_path, duration=spec["duration"])
        assert windows == []
        client = build_accounted_client(
            delegate=mock_client, db_path=db_path, provider="meta")
        result = await run_temporal_detection(
            recording_id=rec_id, db_path=db_path, storage=storage,
            meta_client=client, start=0.0, end=spec["duration"],
            reason="regression-static")
        assert result["events"] == []
        assert result["status"] == "COMPLETED"
        assert result["uncovered_spans"] == []


# ── Pipeline hygiene guards ──────────────────────────────────────────────────

class TestRegressionHygiene:
    def test_product_code_has_no_fixture_strings(self):
        from pathlib import Path as _Path
        app_dir = _Path(__file__).resolve().parent.parent / "app"
        haystacks = [p.read_text() for p in app_dir.rglob("*.py")]
        blob = "\n".join(haystacks)
        for scenario in list(POSITIVE_SCENARIOS) + list(NEGATIVE_SCENARIOS):
            # Quoted-id match: bare generic words (e.g. "low-res" inside
            # "low-resolution") legitimately occur in product prompts.
            assert f'"{scenario}"' not in blob, f"fixture id leaked: {scenario}"
            assert f"'{scenario}'" not in blob, f"fixture id leaked: {scenario}"
        assert "temporal_regression_fixtures" not in blob
        assert "REG-T" not in blob and "REG-N" not in blob

    async def test_pipeline_latency_recorded_and_bounded(
            self, db_path, storage, mock_client):
        spec = SCENARIO_SPECS["incident-multi-stage"]
        result = await _run(db_path, storage, mock_client,
                            _new_recording_id(), spec["duration"], spec["script"])
        assert result["_wall_seconds"] < 120.0
