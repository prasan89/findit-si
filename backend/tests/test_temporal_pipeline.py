"""Tests for the backend temporal event detection pipeline.

Covers: adaptive sampling plans (coarse + motion-triggered dense, PTS
preserved), candidate typing, event-centered clip spans that never split,
clip analysis through the provider abstraction, sequence correlation with
observation-only categories, per-event evidence JSON + API routes, and
coarse-to-fine cost bounds. No network, no API keys; ffmpeg-dependent
tests generate a synthetic fixture and skip when the binary is absent.
"""
import shutil

import pytest

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg binary not available")

TEST_RECORDING_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"


# ── (a) adaptive sampling: spikes → windows ──────────────────────────────────

class TestMotionPrefilter:
    def test_spikes_grouped_padded_and_clamped(self):
        from app.motion_prefilter import spikes_to_windows
        duration = 40.0
        windows = spikes_to_windows(
            [2.0, 2.4, 20.0], pad_before=5.0, pad_after=5.0,
            duration=duration, min_gap=2.0)
        assert len(windows) == 2
        assert windows[0].start_seconds == pytest.approx(0.0)
        assert windows[0].end_seconds == pytest.approx(7.4)
        assert windows[1].start_seconds == pytest.approx(15.0)
        assert windows[1].end_seconds == pytest.approx(25.0)
        assert all(w.reason in ("motion_spike", "scene_change") for w in windows)

    def test_empty_spikes_yield_no_windows(self):
        from app.motion_prefilter import spikes_to_windows
        assert spikes_to_windows([], 5.0, 5.0, 30.0, 2.0) == []

    def test_window_end_clamped_to_duration(self):
        from app.motion_prefilter import spikes_to_windows
        windows = spikes_to_windows(
            [29.0], pad_before=5.0, pad_after=5.0,
            duration=30.0, min_gap=2.0)
        assert len(windows) == 1
        assert windows[0].end_seconds == pytest.approx(30.0)

    @needs_ffmpeg
    async def test_detect_candidate_windows_finds_scene_cut(self, tmp_path):
        import subprocess
        from app.motion_prefilter import detect_candidate_windows
        video = tmp_path / "cuts.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "testsrc=duration=2:size=320x240:rate=10",
             "-f", "lavfi", "-i",
             "color=c=red:duration=2:size=320x240:rate=10",
             "-filter_complex", "[0][1]concat=n=2:v=1",
             "-pix_fmt", "yuv420p", str(video)],
            check=True, capture_output=True)
        windows = await detect_candidate_windows(
            video, duration=4.0, pad_before=1.0, pad_after=1.0, min_gap=0.5)
        assert windows, "hard scene cut must produce a candidate window"
        assert any(w.start_seconds <= 2.0 <= w.end_seconds for w in windows)


# ── (a) adaptive sampling: coarse + dense plan with PTS ─────────────────────

class TestAdaptiveSampler:
    def test_coarse_grid_plus_dense_windows(self):
        from app.adaptive_sampler import build_sample_plan
        from app.motion_prefilter import CandidateWindow
        windows = [CandidateWindow(10.0, 14.0, reason="motion_spike", score=0.9)]
        plan = build_sample_plan(
            duration=20.0, windows=windows, coarse_fps=1.0, dense_fps=4.0,
            max_dense_seconds=60.0, container_pts=None)
        dense = [p for p in plan.samples if p.density == "dense"]
        coarse = [p for p in plan.samples if p.density == "coarse"]
        assert dense, "dense samples expected inside the motion window"
        assert coarse, "coarse context expected outside the motion window"
        assert all(10.0 <= p.timestamp_seconds <= 14.0 for p in dense)
        assert plan.cost["dense_seconds"] == pytest.approx(4.0)
        assert plan.cost["capped"] is False

    def test_dense_budget_cap_truncates_and_marks_capped(self):
        from app.adaptive_sampler import build_sample_plan
        from app.motion_prefilter import CandidateWindow
        windows = [CandidateWindow(0.0, 30.0, reason="motion_spike", score=0.9)]
        plan = build_sample_plan(
            duration=60.0, windows=windows, coarse_fps=1.0, dense_fps=4.0,
            max_dense_seconds=5.0, container_pts=None)
        assert plan.cost["capped"] is True
        assert plan.cost["dense_seconds"] <= 5.0 + 1e-6
        assert plan.uncovered_spans, "truncated windows must be reported"

    def test_plan_snaps_to_container_pts_when_available(self):
        from app.adaptive_sampler import build_sample_plan, snap_to_pts
        pts = [round(i * 0.1, 3) for i in range(200)]
        assert snap_to_pts(1.04, pts) == pytest.approx(1.0)
        plan = build_sample_plan(
            duration=10.0, windows=[], coarse_fps=1.0, dense_fps=4.0,
            max_dense_seconds=60.0, container_pts=pts)
        assert plan.samples
        assert all(s.timestamp_source == "container_pts" for s in plan.samples)
        assert all(s.pts_seconds in pts for s in plan.samples)

    @needs_ffmpeg
    async def test_container_pts_listed_from_real_video(self, tmp_path):
        import subprocess
        from app.adaptive_sampler import get_container_frame_pts
        video = tmp_path / "pts.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "testsrc=duration=2:size=320x240:rate=10",
             "-pix_fmt", "yuv420p", str(video)],
            check=True, capture_output=True)
        pts = await get_container_frame_pts(video)
        assert pts and len(pts) >= 15
        assert pts == sorted(pts)
        assert pts[0] == pytest.approx(0.0, abs=0.05)


# ── (b) candidate typing ─────────────────────────────────────────────────────

class TestCandidateDetector:
    def _obs(self, ts, label, scene, score=0.9):
        return {"video_timestamp_seconds": ts, "object_label": label,
                "scene_description": scene, "detection_score": score,
                "observation_id": f"obs-{ts}", "source_frame_path": "frames/x.jpg"}

    def test_all_six_candidate_types_detected(self):
        from app.candidate_detector import detect_candidates, CANDIDATE_TYPES
        assert set(CANDIDATE_TYPES) == {
            "sudden_approach", "contact_confrontation",
            "reach_neck_belongings", "stumble_fall",
            "rapid_departure", "object_removal"}
        obs = [
            self._obs(1.0, "person", "a person rushing toward another person quickly"),
            self._obs(5.0, "person", "two people grabbing each other close together"),
            self._obs(9.0, "person", "a hand reaching toward the neck of another person"),
            self._obs(13.0, "person", "a person tripping and collapsing to the ground"),
            self._obs(17.0, "person", "a person sprinting away from the scene fast"),
            self._obs(21.0, "bag", "a bag being taken from a bench by a passing person"),
        ]
        got = {c.candidate_type for c in detect_candidates(obs, [])}
        assert got == set(CANDIDATE_TYPES)

    def test_single_frame_candidate_flagged_not_dropped(self):
        from app.candidate_detector import detect_candidates
        obs = [self._obs(3.0, "person", "a person collapsing to the ground")]
        cands = detect_candidates(obs, [])
        assert len(cands) == 1
        assert cands[0].single_frame_candidate is True
        assert cands[0].confidence < 0.6

    def test_quiet_scene_yields_no_candidates(self):
        from app.candidate_detector import detect_candidates
        obs = [self._obs(3.0, "chair", "an empty room with a chair")]
        assert detect_candidates(obs, []) == []


# ── (c) event-centered clips never split ─────────────────────────────────────

class TestClipService:
    def test_overlapping_expanded_spans_merge_into_one_clip(self):
        from app.clip_service import build_event_clips
        spans = [(10.0, 12.0), (13.0, 15.0)]
        clips = build_event_clips(spans, before=5.0, after=5.0, duration=60.0)
        assert len(clips) == 1
        assert clips[0].start_seconds == pytest.approx(5.0)
        assert clips[0].end_seconds == pytest.approx(20.0)

    def test_disjoint_spans_stay_separate_and_clamped(self):
        from app.clip_service import build_event_clips
        clips = build_event_clips([(2.0, 3.0), (50.0, 52.0)], before=5.0,
                                  after=5.0, duration=55.0)
        assert len(clips) == 2
        assert clips[0].start_seconds == pytest.approx(0.0)
        assert clips[1].end_seconds == pytest.approx(55.0)

    def test_clip_carries_timestamp_and_frame_provenance(self):
        from app.clip_service import build_event_clips
        clips = build_event_clips([(10.0, 12.0)], before=2.0, after=2.0,
                                  duration=60.0, timestamp_source="container_pts")
        prov = clips[0].provenance
        assert prov["timestamp_source"] == "container_pts"
        assert prov["event_start"] == pytest.approx(10.0)
        assert prov["event_end"] == pytest.approx(12.0)


# ── (d) clip analysis via provider abstraction ───────────────────────────────

class TestClipAnalysis:
    async def test_mock_clip_analysis_schema(self):
        from app.meta_client import MockMetaClient, validate_clip_analysis
        from tests.fixtures import (FRAME_DATA_BLUE_NOTEBOOK, FRAME_DATA_RED_CAR,
                                    make_scenario_map)
        client = MockMetaClient(scenario_map=make_scenario_map())
        result = await client.analyze_clip(
            [FRAME_DATA_BLUE_NOTEBOOK, FRAME_DATA_RED_CAR], [1.0, 2.0])
        validate_clip_analysis(result)
        assert result["action_sequence"], "action sequence must be non-empty"
        assert "confirmed" in result and "uncertain" in result
        blob = str(result).lower()
        for banned in ("guilty", "crime confirmed", "convicted", "definitively stole"):
            assert banned not in blob

    async def test_accounted_clip_analysis_records_usage(self, db_path):
        from app.meta_client import MockMetaClient
        from app.provider import AccountedFrameAnalyzer, get_usage_summary
        from tests.fixtures import make_scenario_map
        delegate = MockMetaClient(scenario_map=make_scenario_map())
        client = AccountedFrameAnalyzer(
            delegate=delegate, db_path=db_path, provider="meta",
            budget_limit_usd=150.0, budget_reserve_usd=10.0)
        result = await client.analyze_clip([b"frame-bytes-a", b"frame-bytes-b"],
                                           [0.5, 1.5])
        assert "action_sequence" in result
        from app.database import get_db
        async with get_db(db_path) as db:
            row = await (await db.execute(
                "SELECT operation, images_processed, success FROM model_usage"
            )).fetchone()
        assert row[0] == "analyze_clip"
        assert row[1] == 2
        assert row[2] == 1


# ── (e) correlation into observation-only sequences ──────────────────────────

class TestTemporalCorrelator:
    def _cand(self, ctype, start, end, conf=0.7):
        from app.candidate_detector import Candidate
        return Candidate(candidate_type=ctype, start_seconds=start,
                         end_seconds=end, confidence=conf, signals=(ctype,),
                         observation_ids=(), single_frame_candidate=False)

    def test_sequence_categories_cover_all_five_plus_other(self):
        from app.temporal_correlator import (correlate_sequences,
                                            SEQUENCE_CATEGORIES)
        assert set(SEQUENCE_CATEGORIES) == {
            "possible-assault", "possible-theft", "confrontation",
            "sudden-fall", "suspicious-approach-departure", "other-anomaly"}
        seqs = correlate_sequences([
            self._cand("sudden_approach", 0.0, 0.8),
            self._cand("contact_confrontation", 1.0, 3.0),
            self._cand("object_removal", 20.0, 22.0),
            self._cand("stumble_fall", 40.0, 42.0),
            self._cand("sudden_approach", 60.0, 61.0),
            self._cand("rapid_departure", 62.0, 63.0),
        ], gap_seconds=5.0)
        cats = {s.category for s in seqs}
        assert {"possible-assault", "possible-theft", "sudden-fall",
                "suspicious-approach-departure"} <= cats

    def test_optional_stages_present_and_no_crime_verdict(self):
        from app.temporal_correlator import correlate_sequences, is_crime_verdict
        seqs = correlate_sequences([
            self._cand("sudden_approach", 1.0, 2.0),
            self._cand("contact_confrontation", 3.0, 5.0),
            self._cand("rapid_departure", 6.0, 8.0),
        ], gap_seconds=5.0)
        assert len(seqs) == 1
        assert set(seqs[0].stages) >= {"approach", "contact", "departure"}
        assert not is_crime_verdict(seqs[0].category)
        assert "assault" not in seqs[0].category or "possible-" in seqs[0].category


# ── (f) pipeline: span-overlap query + evidence JSON + cost bounds ───────────

class TestTemporalPipeline:
    def test_span_overlap_not_midpoint(self):
        from app.temporal_pipeline import spans_overlap
        assert spans_overlap(10.0, 20.0, 15.0, 25.0) is True
        assert spans_overlap(10.0, 20.0, 20.0, 30.0) is True
        assert spans_overlap(10.0, 20.0, 21.0, 30.0) is False
        # Midpoint (15) inside bounds but span check is what matters;
        # span fully outside bounds is excluded even if near.
        assert spans_overlap(0.0, 5.0, 10.0, 20.0) is False

    async def test_refine_budget_reports_partial_with_uncovered_spans(
            self, db_path, storage, mock_client):
        from app.temporal_pipeline import run_temporal_detection
        from app.provider import build_accounted_client
        from tests.fixtures import insert_recording, RECORDING_ID_1
        from app.database import get_db
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1, video_path="videos/missing.mp4")
        client = build_accounted_client(
            delegate=mock_client, db_path=db_path, provider="meta")
        result = await run_temporal_detection(
            recording_id=RECORDING_ID_1, db_path=db_path, storage=storage,
            meta_client=client, start=0.0, end=30.0, dense_budget_s=0.0,
            reason="test")
        assert result["partial"] is True
        assert result["uncovered_spans"] == [[0.0, 30.0]]


# ── (f) API routes ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestTemporalApi:
    async def test_events_and_frame_and_refine(self, client, tmp_data_dir):
        from app.database import get_db, create_tables
        app_db = str(tmp_data_dir / "findit.db")
        await create_tables(app_db)
        now = "2026-01-01T00:00:00+00:00"
        async with get_db(app_db) as db:
            await db.execute(
                """INSERT INTO recordings(recording_id, original_filename,
                    upload_timestamp, video_path, processing_status,
                    frame_count, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (TEST_RECORDING_ID, "test.mp4", now, "videos/missing.mp4",
                 "INDEXED", 0, now, now))
            await db.commit()

        resp = await client.get(
            f"/api/recordings/{TEST_RECORDING_ID}/temporal-events")
        assert resp.status_code == 200
        body = resp.json()
        assert body["recording_id"] == TEST_RECORDING_ID
        assert "events" in body and "timestamp_source" in body

        resp = await client.get(
            f"/api/recordings/{TEST_RECORDING_ID}/frame?t=5.0")
        assert resp.status_code in (200, 404)

        resp = await client.post(
            f"/api/recordings/{TEST_RECORDING_ID}/refine",
            json={"start": 0.0, "end": 5.0, "reason": "test probe"})
        assert resp.status_code in (200, 202)

    @needs_ffmpeg
    async def test_clip_endpoint_serves_synthetic_video(
            self, client, tmp_data_dir):
        import subprocess
        from app.database import get_db, create_tables
        videos_dir = tmp_data_dir / "videos"
        videos_dir.mkdir(parents=True, exist_ok=True)
        video_path = videos_dir / f"{TEST_RECORDING_ID}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             "testsrc=duration=6:size=320x240:rate=10",
             "-pix_fmt", "yuv420p", str(video_path)],
            check=True, capture_output=True)
        app_db = str(tmp_data_dir / "findit.db")
        await create_tables(app_db)
        now = "2026-01-01T00:00:00+00:00"
        async with get_db(app_db) as db:
            await db.execute(
                """INSERT INTO recordings(recording_id, original_filename,
                    upload_timestamp, video_path, processing_status,
                    frame_count, video_duration_seconds,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (TEST_RECORDING_ID, "synth.mp4", now,
                 f"videos/{TEST_RECORDING_ID}.mp4", "INDEXED", 0, 6.0,
                 now, now))
            await db.commit()

        resp = await client.get(
            f"/api/recordings/{TEST_RECORDING_ID}/clip?start=1.0&end=3.0")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("video/mp4")
        assert len(resp.content) > 1000
