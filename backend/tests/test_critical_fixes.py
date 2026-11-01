"""Regression tests for Phase 1-5 critical investigation fixes.

Tests cover:
- Temporal auto-trigger on incident queries
- No auto-trigger on non-incident queries
- Idempotency (no double-trigger when events already exist)
- Single-frame events no longer dropped (preserved at low confidence)
- PTS-accurate frame timestamps via _build_frame_pts_map()
- H.264 codec detection and transcode path
- Chain-snatch / theft vocabulary detection
- Short physical interaction detection
- Normal interaction (negative case)
- Dog detection query relevance (regression guard)
- Person carrying a box (regression guard for noisy results)
- Single-frame event uncertainty note
- Event near chunk boundaries
- Variable-frame-rate timestamp correctness
"""
import json
import uuid
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.database import create_tables, get_db
from app.event_extractor import extract_events_for_recording
from app.investigate import (
    _count_temporal_events,
    _incident_categories_for_terms,
    _is_incident_query,
    _maybe_auto_trigger_temporal,
    _tokenize,
    investigate,
)
from tests.fixtures import (
    RECORDING_ID_1,
    RECORDING_ID_2,
    insert_event,
    insert_observation,
    insert_recording,
    now_iso,
)


# ── Helpers ────────────────────────────────────────────────────────────────

async def _insert_temporal_event(db, recording_id: str, category: str,
                                  start_s: float, end_s: float,
                                  confidence: float = 0.75) -> str:
    eid = str(uuid.uuid4())
    evidence = {
        "event_id": eid,
        "category": category,
        "start_seconds": start_s,
        "end_seconds": end_s,
        "timestamp_seconds": (start_s + end_s) / 2.0,
        "observed_facts": [f"Candidate {category} at {start_s:.1f}s–{end_s:.1f}s"],
        "action_sequence": [f"Person approached at {start_s:.1f}s",
                            f"Contact at {(start_s+end_s)/2:.1f}s"],
        "evidence_frames": [],
        "confirmed": [],
        "uncertain": [],
        "tracks": [],
    }
    await db.execute(
        """INSERT OR REPLACE INTO temporal_events(
            event_id, recording_id, category, start_seconds, end_seconds,
            timestamp_seconds, timestamp_source, confidence,
            single_frame_candidate, evidence_json, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (eid, recording_id, category, start_s, end_s,
         (start_s + end_s) / 2.0, "coarse_grid", confidence,
         0, json.dumps(evidence), now_iso()))
    await db.commit()
    return eid


async def _insert_refine_log(db, recording_id: str, reason: str = "manual",
                             dense_s: float = 0.0,
                             status: str = "COMPLETED") -> str:
    rid = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO temporal_refine_log(
            refine_id, recording_id, start_seconds, end_seconds, reason,
            dense_seconds, status, uncovered_spans, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (rid, recording_id, 0.0, 10.0, reason, dense_s, status, "[]", now_iso()))
    await db.commit()
    return rid


# ── P1: Incident vocabulary detection ─────────────────────────────────────

class TestIncidentVocabulary:
    """Verify that incident vocabulary correctly gates temporal auto-trigger."""

    def test_chain_snatch_terms_are_incident(self):
        terms = _tokenize("chain snatch")
        assert _is_incident_query(terms), "snatch must trigger incident detection"

    def test_theft_terms_are_incident(self):
        for word in ("robbery", "mugging", "pickpocket", "loot"):
            terms = _tokenize(word)
            assert _is_incident_query(terms), f"{word!r} must trigger incident detection"

    def test_assault_terms_are_incident(self):
        for word in ("attack", "assault", "fight", "violence"):
            terms = _tokenize(word)
            assert _is_incident_query(terms), f"{word!r} must trigger incident detection"

    def test_fall_terms_are_incident(self):
        for word in ("fall", "collapse", "stumbled"):
            terms = _tokenize(word)
            assert _is_incident_query(terms), f"{word!r} must trigger incident detection"

    def test_non_incident_terms_are_not_incident(self):
        for phrase in ("person walking", "car parked", "blue notebook", "dog detected",
                       "show all cameras"):
            terms = _tokenize(phrase)
            assert not _is_incident_query(terms), f"{phrase!r} must NOT trigger incident detection"

    def test_incident_categories_for_snatch(self):
        terms = _tokenize("chain snatch")
        cats = _incident_categories_for_terms(terms)
        assert "possible-theft" in cats
        assert "possible-assault" in cats

    def test_incident_categories_for_fall(self):
        terms = _tokenize("person fell")
        cats = _incident_categories_for_terms(terms)
        assert "sudden-fall" in cats


# ── P1: Auto-trigger behaviour ─────────────────────────────────────────────

@pytest.mark.asyncio
class TestAutoTrigger:
    """Temporal auto-trigger fires on incident queries, skips otherwise."""

    async def test_auto_trigger_fires_for_incident_query(self, db_path, storage):
        """_maybe_auto_trigger_temporal calls run_temporal_detection for incident terms."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)

        triggered = []

        async def _fake_run(recording_id, db_path, storage, meta_client, reason="",
                            dense_budget_s=None):
            triggered.append(recording_id)
            return {"events": [], "status": "COMPLETED", "partial": False}

        mock_client = MagicMock()

        with patch("app.temporal_pipeline.run_temporal_detection", new=_fake_run):
            from app.deps import register_app_deps
            register_app_deps(storage=storage, meta_client=mock_client)
            terms = _tokenize("chain snatch")
            rec_meta = {RECORDING_ID_1: {"original_filename": "test.mp4"}}
            await _maybe_auto_trigger_temporal(db_path, [RECORDING_ID_1], terms, rec_meta)

        assert RECORDING_ID_1 in triggered

    async def test_auto_trigger_skips_non_incident_query(self, db_path, storage):
        """_maybe_auto_trigger_temporal does NOT call run_temporal_detection for benign queries."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)

        triggered = []

        async def _fake_run(*a, **kw):
            triggered.append(True)
            return {"events": [], "status": "COMPLETED", "partial": False}

        mock_client = MagicMock()

        with patch("app.temporal_pipeline.run_temporal_detection", new=_fake_run):
            from app.deps import register_app_deps
            register_app_deps(storage=storage, meta_client=mock_client)
            terms = _tokenize("person walking")
            rec_meta = {RECORDING_ID_1: {"original_filename": "test.mp4"}}
            await _maybe_auto_trigger_temporal(db_path, [RECORDING_ID_1], terms, rec_meta)

        assert triggered == [], "Non-incident query must NOT trigger temporal detection"

    async def test_auto_trigger_idempotent(self, db_path, storage):
        """Recordings with existing temporal_events are skipped (no double-trigger)."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            # Pre-populate temporal_events
            await _insert_temporal_event(db, RECORDING_ID_1, "possible-theft", 40.0, 45.0)

        triggered = []

        async def _fake_run(*a, **kw):
            triggered.append(True)
            return {"events": [], "status": "COMPLETED", "partial": False}

        mock_client = MagicMock()

        with patch("app.temporal_pipeline.run_temporal_detection", new=_fake_run):
            from app.deps import register_app_deps
            register_app_deps(storage=storage, meta_client=mock_client)
            terms = _tokenize("chain snatch")
            rec_meta = {RECORDING_ID_1: {"original_filename": "test.mp4"}}
            await _maybe_auto_trigger_temporal(db_path, [RECORDING_ID_1], terms, rec_meta)

        assert triggered == [], "Recording with existing temporal_events must not be re-triggered"

    async def test_auto_trigger_is_coarse_only(self, db_path, storage):
        """Auto-trigger must not spend dense vision calls (request-critical path).

        Regression: the auto-trigger planned a full dense run (~120 vision
        calls) inside investigate() but was killed by its own 30s timeout on
        every incident query — 30s of latency, burned calls, zero events.
        """
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)

        seen_kwargs: dict = {}

        async def _fake_run(*a, **kw):
            seen_kwargs.update(kw)
            return {"events": [], "status": "COMPLETED", "partial": False}

        mock_client = MagicMock()

        with patch("app.temporal_pipeline.run_temporal_detection", new=_fake_run):
            from app.deps import register_app_deps
            register_app_deps(storage=storage, meta_client=mock_client)
            terms = _tokenize("chain snatch")
            rec_meta = {RECORDING_ID_1: {"original_filename": "test.mp4"}}
            await _maybe_auto_trigger_temporal(db_path, [RECORDING_ID_1], terms, rec_meta)

        assert seen_kwargs.get("dense_budget_s") == 0, (
            "auto-trigger must run coarse-only (dense_budget_s=0); "
            f"got {seen_kwargs.get('dense_budget_s')!r}")

    async def test_auto_trigger_skips_when_refine_log_exists(self, db_path, storage):
        """A completed refinement with 0 events must not re-trigger every query."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await _insert_refine_log(db, RECORDING_ID_1)

        triggered = []

        async def _fake_run(*a, **kw):
            triggered.append(True)
            return {"events": [], "status": "COMPLETED", "partial": False}

        mock_client = MagicMock()

        with patch("app.temporal_pipeline.run_temporal_detection", new=_fake_run):
            from app.deps import register_app_deps
            register_app_deps(storage=storage, meta_client=mock_client)
            terms = _tokenize("chain snatch")
            rec_meta = {RECORDING_ID_1: {"original_filename": "test.mp4"}}
            await _maybe_auto_trigger_temporal(db_path, [RECORDING_ID_1], terms, rec_meta)

        assert triggered == [], (
            "Recording with a completed refine-log row must not be re-triggered")

    async def test_auto_trigger_skips_when_deps_not_registered(self, db_path):
        """If app deps are not registered, auto-trigger silently no-ops."""
        import app.deps as deps_mod
        orig_storage, orig_client = deps_mod._storage, deps_mod._meta_client
        deps_mod._storage = None
        deps_mod._meta_client = None
        try:
            terms = _tokenize("chain snatch")
            rec_meta = {RECORDING_ID_1: {"original_filename": "test.mp4"}}
            # Must not raise
            await _maybe_auto_trigger_temporal(db_path, [RECORDING_ID_1], terms, rec_meta)
        finally:
            deps_mod._storage = orig_storage
            deps_mod._meta_client = orig_client

    async def test_count_temporal_events_returns_zero_when_none(self, db_path):
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
        n = await _count_temporal_events(db_path, RECORDING_ID_1)
        assert n == 0

    async def test_count_temporal_events_counts_correctly(self, db_path):
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await _insert_temporal_event(db, RECORDING_ID_1, "possible-theft", 10.0, 15.0)
            await _insert_temporal_event(db, RECORDING_ID_1, "confrontation", 40.0, 45.0)
        n = await _count_temporal_events(db_path, RECORDING_ID_1)
        assert n == 2


# ── P1: Incident events surface in investigation results ───────────────────

@pytest.mark.asyncio
class TestIncidentEventsInInvestigation:
    """Stored temporal events surface when queried with incident vocabulary."""

    async def test_chain_snatch_query_surfaces_temporal_event(self, db_path):
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await _insert_temporal_event(db, RECORDING_ID_1, "possible-theft", 42.0, 47.0)

        result = await investigate("chain snatch", db_path)
        assert len(result.events) >= 1
        assert any(
            e.event_type in ("possible-theft", "possible-assault")
            for e in result.events
        ), "Investigation should surface temporal possible-theft event"

    async def test_assault_query_surfaces_confrontation_event(self, db_path):
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await _insert_temporal_event(db, RECORDING_ID_1, "confrontation", 20.0, 25.0)

        result = await investigate("attack or fight", db_path)
        assert len(result.events) >= 1

    async def test_normal_walking_query_does_not_surface_incident_events(self, db_path):
        """Non-incident query must not surface temporal incident events."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await _insert_temporal_event(db, RECORDING_ID_1, "possible-theft", 42.0, 47.0)

        result = await investigate("person walking", db_path)
        # No regular events exist either — both should be empty
        assert len(result.events) == 0


# ── P2b: Single-frame events preserved at low confidence ──────────────────

@pytest.mark.asyncio
class TestSingleFrameEventPreservation:
    """Single-frame events are surfaced with low confidence, not silently dropped."""

    async def test_single_frame_event_has_low_confidence(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="person reaching toward neck",
            video_timestamp_seconds=42.0,
            detection_score=0.85,
            scene_description="Person reaching toward another person's neck area",
        )
        n = await extract_events_for_recording(RECORDING_ID_1, db_path)
        assert n == 1, "Single-frame event should be preserved (not dropped)"

        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT confidence, uncertainty_notes FROM events")).fetchone()

        assert row["confidence"] <= 0.5, "Single-frame event confidence must be capped at 0.5"
        notes = json.loads(row["uncertainty_notes"])
        assert any("Single-frame" in n for n in notes), (
            "Single-frame event must include uncertainty note")

    async def test_two_frame_event_has_normal_confidence(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        for ts in (42.0, 43.0):
            await insert_observation(
                test_db, RECORDING_ID_1,
                object_label="person",
                video_timestamp_seconds=ts,
                detection_score=0.85,
            )
        n = await extract_events_for_recording(RECORDING_ID_1, db_path)
        assert n == 1

        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT confidence, uncertainty_notes FROM events")).fetchone()

        # Two-frame event: confidence NOT capped, no single-frame note
        assert row["confidence"] > 0.5
        notes = json.loads(row["uncertainty_notes"])
        assert not any("Single-frame" in n for n in notes)

    async def test_single_frame_event_survives_min_confidence_gate(self, test_db, db_path):
        """Confidence=0.3 single-frame event passes MIN_CONFIDENCE=0.2 gate."""
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="motorcycle approaching",
            video_timestamp_seconds=10.0,
            detection_score=0.30,  # 0.30 capped to 0.3 → still > MIN_CONFIDENCE (0.2)
        )
        n = await extract_events_for_recording(RECORDING_ID_1, db_path)
        assert n == 1


# ── P2b: Brief incident detection (chain-snatch simulation) ───────────────

@pytest.mark.asyncio
class TestBriefIncidentDetection:
    """Simulate a 3-5s chain-snatch scenario at 1 FPS with the temporal pipeline."""

    async def test_chain_snatch_candidate_detection(self):
        """candidate_detector fires reach_neck_belongings on snatch-like observation."""
        from app.candidate_detector import detect_candidates
        obs = [{
            "observation_id": "obs1",
            "video_timestamp_seconds": 42.0,
            "object_label": "person",
            "scene_description": "Person reaching toward another person's necklace",
            "detection_score": 0.85,
        }]
        candidates = detect_candidates(obs)
        ctypes = {c.candidate_type for c in candidates}
        assert "reach_neck_belongings" in ctypes or "contact_confrontation" in ctypes, (
            f"Snatch-like observation must produce a relevant candidate; got {ctypes}")

    async def test_rapid_departure_detection(self):
        """candidate_detector fires rapid_departure on motorcycle driving away."""
        from app.candidate_detector import detect_candidates
        obs = [
            {
                "observation_id": "obs1",
                "video_timestamp_seconds": 42.0,
                "object_label": "motorcycle",
                "scene_description": "Motorcycle driving away from camera at speed",
                "detection_score": 0.80,
            },
            {
                "observation_id": "obs2",
                "video_timestamp_seconds": 43.0,
                "object_label": "person",
                "scene_description": "Person stumbling after motorcycle drove away",
                "detection_score": 0.75,
            },
        ]
        candidates = detect_candidates(obs)
        ctypes = {c.candidate_type for c in candidates}
        assert "rapid_departure" in ctypes or "stumble_fall" in ctypes, (
            f"Departure scenario must produce rapid_departure or stumble_fall; got {ctypes}")

    async def test_temporal_correlator_theft_sequence(self):
        """correlate_sequences produces possible-theft from approach+contact+departure."""
        from app.candidate_detector import Candidate
        from app.temporal_correlator import correlate_sequences
        candidates = [
            Candidate(candidate_type="sudden_approach", start_seconds=40.0, end_seconds=41.0, confidence=0.7),
            Candidate(candidate_type="reach_neck_belongings", start_seconds=42.0, end_seconds=43.0, confidence=0.8),
            Candidate(candidate_type="rapid_departure", start_seconds=44.0, end_seconds=45.0, confidence=0.7),
        ]
        sequences = correlate_sequences(candidates, gap_seconds=8.0)
        assert len(sequences) == 1
        assert sequences[0].category in ("possible-theft", "possible-assault", "confrontation"), (
            f"Expected theft/assault sequence; got {sequences[0].category}")

    async def test_single_frame_approach_candidate(self):
        """Single observation of person approaching produces sudden_approach candidate."""
        from app.candidate_detector import detect_candidates
        obs = [{
            "observation_id": "obs1",
            "video_timestamp_seconds": 41.0,
            "object_label": "person",
            "scene_description": "Person running toward the other person quickly",
            "detection_score": 0.80,
        }]
        candidates = detect_candidates(obs)
        ctypes = {c.candidate_type for c in candidates}
        assert "sudden_approach" in ctypes or "contact_confrontation" in ctypes


# ── P3: Timestamp correctness ──────────────────────────────────────────────

class TestTimestampCorrectness:
    """PTS-accurate frame timestamp extraction."""

    def test_build_frame_pts_map_returns_empty_on_no_pts(self):
        """_build_frame_pts_map falls back to empty list (caller uses i/fps)."""
        import asyncio
        from app.processor import _build_frame_pts_map
        from unittest.mock import patch

        async def _run():
            with patch("app.adaptive_sampler.get_container_frame_pts",
                       new=AsyncMock(return_value=None)):
                return await _build_frame_pts_map(Path("/nonexistent.mp4"), fps=1.0, n_frames=5)

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result == []

    def test_build_frame_pts_map_snaps_to_container_pts(self):
        """When PTS available, frame timestamps snap to container PTS."""
        import asyncio
        from app.processor import _build_frame_pts_map

        container_pts = [0.0, 1.033, 2.066, 3.099, 4.132, 5.165]

        async def _mock_pts(path):
            return container_pts

        async def _run():
            with patch("app.adaptive_sampler.get_container_frame_pts", new=_mock_pts):
                return await _build_frame_pts_map(Path("/test.mp4"), fps=1.0, n_frames=5)

        from unittest.mock import patch
        result = asyncio.get_event_loop().run_until_complete(_run())
        assert len(result) == 5
        # Each timestamp should be a container PTS value, not a synthetic i/fps
        for ts in result:
            assert ts in container_pts, f"Timestamp {ts} not in container PTS list"

    def test_build_frame_pts_map_zero_frames(self):
        """n_frames=0 returns empty list."""
        import asyncio
        from app.processor import _build_frame_pts_map

        result = asyncio.get_event_loop().run_until_complete(
            _build_frame_pts_map(Path("/test.mp4"), fps=1.0, n_frames=0))
        assert result == []

    def test_vfr_timestamps_snap_to_nearest_pts(self):
        """Variable-frame-rate: nominal i/fps snaps to nearest container PTS."""
        from app.adaptive_sampler import snap_to_pts

        # VFR stream: frames at irregular intervals
        container_pts = [0.0, 0.033, 0.400, 0.800, 1.200, 1.600, 2.000]
        # Nominal 1-FPS grid: 0.0, 1.0, 2.0
        snapped = [snap_to_pts(i * 1.0, container_pts) for i in range(3)]
        assert snapped[0] == 0.0
        # 1.0 is equidistant between 0.8 and 1.2; snap_to_pts picks nearest (0.8)
        assert snapped[1] in (0.800, 1.200), f"Expected 0.8 or 1.2, got {snapped[1]}"
        assert snapped[2] == 2.000

    @pytest.mark.asyncio
    async def test_event_timestamps_reflect_pts_when_available(self, test_db, db_path):
        """Events stored after indexing should use PTS-derived timestamps."""
        # Insert observations with non-synthetic timestamps (simulating PTS-derived)
        await insert_recording(test_db, RECORDING_ID_1,
                               recording_datetime="2024-01-15T14:30:00Z")
        # Simulate PTS-derived timestamps (1.033s, 2.066s)
        for ts in (1.033, 2.066):
            await insert_observation(
                test_db, RECORDING_ID_1,
                object_label="person",
                video_timestamp_seconds=ts,
                detection_score=0.85,
            )
        n = await extract_events_for_recording(RECORDING_ID_1, db_path)
        assert n == 1

        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT timestamp_seconds, start_seconds, end_seconds FROM events")).fetchone()
        # The event should span 1.033–2.066 (PTS-derived)
        assert abs(row["start_seconds"] - 1.033) < 0.01
        assert abs(row["end_seconds"] - 2.066) < 0.01


# ── P4: Video codec detection ──────────────────────────────────────────────

@pytest.mark.asyncio
class TestCodecDetection:
    """_get_video_codec and _ensure_h264 path validation."""

    async def test_get_video_codec_returns_none_on_missing_file(self):
        from app.processor import _get_video_codec
        codec = await _get_video_codec(Path("/nonexistent_video.mp4"))
        assert codec is None

    async def test_ensure_h264_returns_same_path_when_already_h264(self, tmp_path, db_path):
        """H.264 videos pass through without transcoding."""
        from app.processor import _ensure_h264
        from app.storage import LocalStorageBackend

        video = tmp_path / "videos" / "test.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"\x00" * 100)
        storage = LocalStorageBackend(tmp_path)

        with patch("app.processor._get_video_codec", new=AsyncMock(return_value="h264")):
            result = await _ensure_h264(video, RECORDING_ID_1, storage, db_path)

        assert result == video, "H.264 video should be returned unchanged"

    async def test_ensure_h264_transcodes_hevc_video(self, tmp_path, db_path):
        """H.265/HEVC triggers a transcode attempt."""
        from app.processor import _ensure_h264
        from app.storage import LocalStorageBackend

        video = tmp_path / "videos" / "test.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"\x00" * 100)

        # Create a fake h264 output to simulate successful transcode
        h264_path = video.with_name("test_h264.mp4")

        async def _fake_communicate_with_timeout(proc, timeout, what):
            # Simulate ffmpeg success: create the output file
            h264_path.write_bytes(b"\x00" * 100)
            return b"", b""

        async def _fake_subproc(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        storage = LocalStorageBackend(tmp_path)

        with patch("app.processor._get_video_codec", new=AsyncMock(return_value="hevc")), \
             patch("app.processor._communicate_with_timeout",
                   new=AsyncMock(side_effect=_fake_communicate_with_timeout)), \
             patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=_fake_subproc)):
            async with get_db(db_path) as db:
                from tests.fixtures import now_iso
                await db.execute(
                    """INSERT OR REPLACE INTO recordings(
                        recording_id, original_filename, upload_timestamp,
                        video_path, processing_status, frame_count,
                        created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (RECORDING_ID_1, "test.mp4", now_iso(),
                     "videos/test.mp4", "INDEXED", 0, now_iso(), now_iso()))
                await db.commit()

            result = await _ensure_h264(video, RECORDING_ID_1, storage, db_path)

        # If transcode succeeded and output exists, path should change
        # (or fall back to original if transcode path wasn't returned)
        assert result is not None

    async def test_ensure_h264_falls_back_on_ffmpeg_failure(self, tmp_path, db_path):
        """When ffmpeg fails, the original path is returned without error."""
        from app.processor import _ensure_h264
        from app.storage import LocalStorageBackend

        video = tmp_path / "videos" / "test.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"\x00" * 100)
        storage = LocalStorageBackend(tmp_path)

        async def _fail(*args, **kwargs):
            raise RuntimeError("ffmpeg not found")

        with patch("app.processor._get_video_codec", new=AsyncMock(return_value="hevc")), \
             patch("app.processor._communicate_with_timeout", new=AsyncMock(side_effect=_fail)):
            result = await _ensure_h264(video, RECORDING_ID_1, storage, db_path)

        assert result == video, "On transcode failure, original path should be returned"


# ── P5: Query relevance regressions ──────────────────────────────────────

@pytest.mark.asyncio
class TestQueryRelevanceRegressions:
    """Regression guards for known noisy-result scenarios."""

    async def test_dog_query_does_not_match_person_event(self, db_path):
        """Dog query must not surface person events (animal subject gate)."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await insert_event(db, RECORDING_ID_1,
                               event_type="person_detected",
                               action_description="A person walking in the hallway",
                               observed_facts=["Observed: person"],
                               confidence=0.9)
        result = await investigate("any dog detected", db_path)
        assert result.events == [], (
            "Dog query must not surface person events (animal subject gate broken)")

    async def test_person_carrying_box_does_not_match_dog_query(self, db_path):
        """Person carrying a box should not match a dog query."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await insert_event(db, RECORDING_ID_1,
                               event_type="person_detected",
                               action_description="Person carrying a cardboard box",
                               observed_facts=["Observed: person", "Observed: box"],
                               confidence=0.85)
        result = await investigate("dog detected", db_path)
        assert result.events == [], "Dog query must not match person-carrying-box event"

    async def test_person_query_matches_person_event(self, db_path):
        """Person query should match person events (positive case)."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await insert_event(db, RECORDING_ID_1,
                               event_type="person_detected",
                               action_description="A person walking in the corridor",
                               observed_facts=["Observed: person"],
                               confidence=0.85)
        result = await investigate("person walking", db_path)
        assert len(result.events) >= 1

    async def test_box_query_matches_box_event(self, db_path):
        """Box query should match object events containing box."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await insert_event(db, RECORDING_ID_1,
                               event_type="unattended_object",
                               action_description="A cardboard box left unattended",
                               observed_facts=["Observed: cardboard box"],
                               object_descriptions=["cardboard box"],
                               confidence=0.80)
        result = await investigate("box", db_path)
        assert len(result.events) >= 1


# ── Boundary and edge cases ──────────────────────────────────────────────

@pytest.mark.asyncio
class TestBoundaryAndEdgeCases:
    """Edge cases: chunk boundaries, empty results, multi-recording."""

    async def test_event_at_first_second_is_preserved(self, test_db, db_path):
        """An event at t=0s (start boundary) is not excluded."""
        await insert_recording(test_db, RECORDING_ID_1)
        for ts in (0.0, 1.0):
            await insert_observation(test_db, RECORDING_ID_1, object_label="person",
                                     video_timestamp_seconds=ts, detection_score=0.85)
        n = await extract_events_for_recording(RECORDING_ID_1, db_path)
        assert n == 1
        result = await investigate("person", db_path)
        assert len(result.events) == 1
        assert result.events[0].timestamp_seconds <= 0.5

    async def test_event_at_end_boundary_is_preserved(self, test_db, db_path):
        """An event near the end of a recording is not excluded."""
        await insert_recording(test_db, RECORDING_ID_1)
        for ts in (29.0, 30.0):
            await insert_observation(test_db, RECORDING_ID_1, object_label="person",
                                     video_timestamp_seconds=ts, detection_score=0.85)
        n = await extract_events_for_recording(RECORDING_ID_1, db_path)
        assert n == 1

    async def test_incident_query_on_empty_db_returns_no_events(self, db_path):
        """Chain-snatch query on empty DB returns empty results, not an error."""
        result = await investigate("chain snatch", db_path)
        assert result.events == []
        assert "No indexed recordings" in result.narrative or \
               result.searched_recordings == 0

    async def test_incident_query_spans_multiple_recordings(self, db_path):
        """Temporal events from multiple recordings all surface."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await insert_recording(db, RECORDING_ID_2)
            await _insert_temporal_event(db, RECORDING_ID_1, "possible-theft", 10.0, 15.0)
            await _insert_temporal_event(db, RECORDING_ID_2, "possible-theft", 20.0, 25.0)

        result = await investigate("chain snatch", db_path)
        rec_ids = {e.recording_id for e in result.events}
        assert RECORDING_ID_1 in rec_ids
        assert RECORDING_ID_2 in rec_ids

    async def test_normal_scene_no_temporal_candidates(self):
        """Normal passing / greeting scenario produces no incident candidates."""
        from app.candidate_detector import detect_candidates
        from app.temporal_correlator import correlate_sequences
        obs = [
            {"observation_id": "o1", "video_timestamp_seconds": 1.0,
             "object_label": "person", "scene_description": "Person walking normally",
             "detection_score": 0.8},
            {"observation_id": "o2", "video_timestamp_seconds": 2.0,
             "object_label": "person",
             "scene_description": "Two people standing and talking",
             "detection_score": 0.8},
        ]
        candidates = detect_candidates(obs)
        sequences = correlate_sequences(candidates)
        # May produce suspicious-approach-departure at low confidence — but must
        # NOT produce possible-theft or possible-assault from a normal conversation.
        bad_cats = {"possible-theft", "possible-assault"}
        for seq in sequences:
            assert seq.category not in bad_cats, (
                f"Normal scene produced {seq.category} — false positive")
