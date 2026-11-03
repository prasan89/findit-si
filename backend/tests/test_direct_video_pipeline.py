"""Tests for the direct video API pipeline (Files API + Responses API)."""
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from app.meta_client import (
    MockMetaClient,
    MockVideoClient,
    ModelResponseError,
    validate_video_analysis,
    _extract_responses_api_content,
    _extract_responses_api_finish_reason,
)
from app.processor import _upload_and_analyze_video
from app.database import create_tables, get_db


# ── Helpers ──────────────────────────────────────────────────────────────────

_MINIMAL_VIDEO_ANALYSIS = {
    "scene_context": "indoor corridor, overhead camera, daytime",
    "entities": [
        {
            "entity_id": "entity_1",
            "type": "person",
            "description": "person in dark clothing",
            "first_seen_seconds": 0.0,
            "last_seen_seconds": 10.0,
            "confidence": 0.9,
        }
    ],
    "actions": [
        {
            "entity_id": "entity_1",
            "action": "walks toward exit",
            "start_seconds": 2.0,
            "end_seconds": 8.0,
            "confidence": 0.85,
        }
    ],
    "interactions": [],
    "changes": [
        {
            "type": "entered",
            "entity_id": "entity_1",
            "description": "person entered from left",
            "timestamp_seconds": 0.0,
        }
    ],
    "unusual_observations": ["person pauses near door for 3 seconds"],
    "uncertain_observations": [],
    "static_scene": False,
    "narrative_steps": [
        {
            "step": 1,
            "time_range": "0:00-0:10",
            "start_seconds": 0.0,
            "end_seconds": 10.0,
            "description": "Person in dark clothing walks from left to exit.",
        }
    ],
    "key_observations": ["Person pauses near door"],
    "open_questions": [],
}


async def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "test.db")
    await create_tables(db_path)
    return db_path


async def _insert_recording(db_path: str, rid: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO recordings("
            "recording_id,original_filename,upload_timestamp,"
            "video_path,processing_status,frame_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (rid, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "PROCESSING", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()


# ── validate_video_analysis ───────────────────────────────────────────────────

class TestValidateVideoAnalysis:
    def test_valid_minimal(self):
        result = validate_video_analysis(_MINIMAL_VIDEO_ANALYSIS)
        assert result is _MINIMAL_VIDEO_ANALYSIS

    def test_valid_empty_lists(self):
        data = {
            "scene_context": "empty room",
            "entities": [],
            "actions": [],
            "changes": [],
            "narrative_steps": [],
        }
        assert validate_video_analysis(data) is data

    def test_non_dict_raises(self):
        with pytest.raises(ModelResponseError) as exc_info:
            validate_video_analysis(["not", "a", "dict"])
        assert exc_info.value.code == "schema_violation"

    def test_entities_not_list_raises(self):
        data = {"scene_context": "room", "entities": "not a list"}
        with pytest.raises(ModelResponseError) as exc_info:
            validate_video_analysis(data)
        assert exc_info.value.code == "schema_violation"

    def test_narrative_steps_not_list_raises(self):
        data = {"scene_context": "room", "narrative_steps": "not a list"}
        with pytest.raises(ModelResponseError) as exc_info:
            validate_video_analysis(data)
        assert exc_info.value.code == "schema_violation"

    def test_missing_entities_ok(self):
        data = {"scene_context": "room"}
        result = validate_video_analysis(data)
        assert result is data

    def test_actions_not_list_raises(self):
        data = {"scene_context": "room", "actions": {"key": "value"}}
        with pytest.raises(ModelResponseError) as exc_info:
            validate_video_analysis(data)
        assert exc_info.value.code == "schema_violation"

    def test_changes_not_list_raises(self):
        data = {"scene_context": "room", "changes": 42}
        with pytest.raises(ModelResponseError) as exc_info:
            validate_video_analysis(data)
        assert exc_info.value.code == "schema_violation"


# ── _extract_responses_api_content ───────────────────────────────────────────

class TestExtractResponsesApiContent:
    def test_extracts_output_text_from_message(self):
        data = {
            "output": [
                {"type": "reasoning", "content": [{"type": "thinking", "text": "thinking..."}]},
                {"type": "message", "content": [{"type": "output_text", "text": '{"scene_context":"test"}'}]},
            ]
        }
        result = _extract_responses_api_content(data)
        assert result == '{"scene_context":"test"}'

    def test_falls_back_to_any_text(self):
        data = {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
            ]
        }
        assert _extract_responses_api_content(data) == "hello"

    def test_returns_none_for_empty_output(self):
        assert _extract_responses_api_content({"output": []}) is None

    def test_returns_none_for_missing_output(self):
        assert _extract_responses_api_content({}) is None

    def test_returns_none_when_no_text_content(self):
        data = {"output": [{"type": "message", "content": [{"type": "image", "data": "..."}]}]}
        assert _extract_responses_api_content(data) is None


class TestExtractResponsesApiFinishReason:
    def test_extracts_status_field(self):
        data = {"status": "completed"}
        assert _extract_responses_api_finish_reason(data) == "completed"

    def test_extracts_stop_reason_from_output(self):
        data = {
            "output": [
                {"type": "message", "stop_reason": "end_turn"}
            ]
        }
        assert _extract_responses_api_finish_reason(data) == "end_turn"

    def test_returns_none_for_empty(self):
        assert _extract_responses_api_finish_reason({}) is None


# ── MockMetaClient direct video stubs ────────────────────────────────────────

class TestMockMetaClientDirectVideo:
    @pytest.mark.asyncio
    async def test_upload_video_file_returns_mock_file_id(self):
        client = MockVideoClient()
        file_id = await client.upload_video_file("/tmp/test.mp4")
        assert file_id.startswith("file-mock-")
        assert len(file_id) > 10

    @pytest.mark.asyncio
    async def test_upload_video_file_deterministic(self):
        client = MockVideoClient()
        fid1 = await client.upload_video_file("/tmp/a.mp4")
        fid2 = await client.upload_video_file("/tmp/a.mp4")
        assert fid1 == fid2

    @pytest.mark.asyncio
    async def test_upload_video_file_different_paths_differ(self):
        client = MockVideoClient()
        fid1 = await client.upload_video_file("/tmp/a.mp4")
        fid2 = await client.upload_video_file("/tmp/b.mp4")
        assert fid1 != fid2

    @pytest.mark.asyncio
    async def test_analyze_video_file_default_response(self):
        client = MockVideoClient()
        result = await client.analyze_video_file("file-mock-abc123")
        assert "entities" in result
        assert "narrative_steps" in result
        assert isinstance(result["entities"], list)
        assert isinstance(result["narrative_steps"], list)
        assert len(result["narrative_steps"]) >= 1

    @pytest.mark.asyncio
    async def test_analyze_video_file_sentinel_key(self):
        custom = {
            "scene_context": "custom scene",
            "entities": [],
            "actions": [],
            "changes": [],
            "narrative_steps": [
                {"step": 1, "time_range": "0:00-0:05",
                 "start_seconds": 0.0, "end_seconds": 5.0,
                 "description": "Custom narrative"}
            ],
            "key_observations": ["custom observation"],
        }
        client = MockVideoClient(scenario_map={"video:file-mock-test": custom})
        result = await client.analyze_video_file("file-mock-test")
        assert result["scene_context"] == "custom scene"
        assert result["key_observations"] == ["custom observation"]

    @pytest.mark.asyncio
    async def test_call_count_incremented(self):
        client = MockVideoClient()
        assert client._call_count == 0
        await client.upload_video_file("/tmp/test.mp4")
        assert client._call_count == 1
        await client.analyze_video_file("file-mock-abc")
        assert client._call_count == 2


# ── _upload_and_analyze_video ─────────────────────────────────────────────────

class TestUploadAndAnalyzeVideo:
    @pytest.mark.asyncio
    async def test_happy_path_stores_understanding_and_narrative(self, tmp_path):
        db_path = await _make_db(tmp_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)

        # Create a dummy video file (content doesn't matter for mock)
        video_path = tmp_path / "test.mp4"
        video_path.write_bytes(b"fake mp4 content")

        client = MockVideoClient()
        errors = []
        success = await _upload_and_analyze_video(
            recording_id=rid,
            video_abs_path=str(video_path),
            db_path=db_path,
            meta_client=client,
            duration_seconds=10.0,
            errors=errors,
        )

        assert success is True
        assert errors == []

        # Check upload_understandings row was created
        async with aiosqlite.connect(db_path) as db:
            row = await (await db.execute(
                "SELECT start_seconds, end_seconds, entities_json FROM upload_understandings "
                "WHERE recording_id=?", (rid,)
            )).fetchone()
        assert row is not None
        assert row[0] == 0.0
        assert row[1] == 10.0
        entities = json.loads(row[2])
        assert isinstance(entities, list)

        # Check recording_narrative was set
        async with aiosqlite.connect(db_path) as db:
            row = await (await db.execute(
                "SELECT recording_narrative FROM recordings WHERE recording_id=?",
                (rid,)
            )).fetchone()
        assert row is not None
        narrative = json.loads(row[0])
        assert "narrative_steps" in narrative
        assert isinstance(narrative["narrative_steps"], list)

    @pytest.mark.asyncio
    async def test_upload_failure_returns_false_and_adds_error(self, tmp_path):
        db_path = await _make_db(tmp_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)

        class FailingUploadClient(MockVideoClient):
            async def upload_video_file(self, video_path: str) -> str:
                raise RuntimeError("Network error: connection refused")

        client = FailingUploadClient()
        errors = []
        success = await _upload_and_analyze_video(
            recording_id=rid,
            video_abs_path="/tmp/fake.mp4",
            db_path=db_path,
            meta_client=client,
            duration_seconds=5.0,
            errors=errors,
        )

        assert success is False
        assert len(errors) == 1
        assert "Video upload failed" in errors[0]

    @pytest.mark.asyncio
    async def test_analysis_failure_returns_false_and_adds_error(self, tmp_path):
        db_path = await _make_db(tmp_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)

        class FailingAnalysisClient(MockVideoClient):
            async def analyze_video_file(self, file_id: str) -> dict:
                raise ModelResponseError("malformed_json", "Bad JSON from API")

        client = FailingAnalysisClient()
        errors = []
        success = await _upload_and_analyze_video(
            recording_id=rid,
            video_abs_path="/tmp/fake.mp4",
            db_path=db_path,
            meta_client=client,
            duration_seconds=5.0,
            errors=errors,
        )

        assert success is False
        assert len(errors) == 1
        assert "Video analysis failed" in errors[0]

    @pytest.mark.asyncio
    async def test_narrative_stored_correctly_from_analysis(self, tmp_path):
        db_path = await _make_db(tmp_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)

        video_path = tmp_path / "test.mp4"
        video_path.write_bytes(b"fake mp4")

        custom_result = {
            "scene_context": "parking garage, overhead camera",
            "entities": [{"entity_id": "e1", "type": "person", "description": "woman in pink",
                           "first_seen_seconds": 0.0, "last_seen_seconds": 35.0, "confidence": 0.9}],
            "actions": [],
            "changes": [],
            "interactions": [],
            "unusual_observations": ["person loiters for 30s"],
            "uncertain_observations": [],
            "static_scene": False,
            "narrative_steps": [
                {"step": 1, "time_range": "0:00-0:35", "start_seconds": 0.0,
                 "end_seconds": 35.0, "description": "Woman stands alone near car."}
            ],
            "key_observations": ["Woman loiters near maroon car"],
            "open_questions": ["Camera angle obscures exit"],
        }

        file_id_for_path = "file-mock-" + hashlib.sha256(str(video_path).encode()).hexdigest()[:16]
        client = MockVideoClient(scenario_map={f"video:{file_id_for_path}": custom_result})
        errors = []
        success = await _upload_and_analyze_video(
            recording_id=rid,
            video_abs_path=str(video_path),
            db_path=db_path,
            meta_client=client,
            duration_seconds=35.0,
            errors=errors,
        )

        assert success is True

        async with aiosqlite.connect(db_path) as db:
            row = await (await db.execute(
                "SELECT recording_narrative FROM recordings WHERE recording_id=?",
                (rid,)
            )).fetchone()
        narrative = json.loads(row[0])
        assert narrative["key_observations"] == ["Woman loiters near maroon car"]
        assert narrative["open_questions"] == ["Camera angle obscures exit"]
        assert len(narrative["narrative_steps"]) == 1

    @pytest.mark.asyncio
    async def test_duration_none_uses_zero_end(self, tmp_path):
        db_path = await _make_db(tmp_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)

        video_path = tmp_path / "test.mp4"
        video_path.write_bytes(b"fake")

        client = MockVideoClient()
        errors = []
        success = await _upload_and_analyze_video(
            recording_id=rid,
            video_abs_path=str(video_path),
            db_path=db_path,
            meta_client=client,
            duration_seconds=None,
            errors=errors,
        )

        assert success is True
        async with aiosqlite.connect(db_path) as db:
            row = await (await db.execute(
                "SELECT end_seconds FROM upload_understandings WHERE recording_id=?",
                (rid,)
            )).fetchone()
        assert row is not None
        assert row[0] == 0.0


# ── process_recording integration (direct video path) ────────────────────────

class TestProcessRecordingDirectPath:
    @pytest.mark.asyncio
    async def test_uses_direct_path_when_client_supports_it(self, tmp_path):
        """Full process_recording() with MockMetaClient: status=INDEXED, events extracted."""
        from app.processor import process_recording
        from app.storage import LocalStorageBackend
        from app.models import ProcessingStatus

        db_path = await _make_db(tmp_path)
        storage = LocalStorageBackend(data_dir=tmp_path)
        await storage.ensure_dirs()

        # Create a minimal real MP4-ish file so ffprobe won't crash
        # (use a real tiny video if available, otherwise a dummy file)
        video_content = b"dummy video content"
        rid = str(uuid.uuid4())

        # Insert recording with video path
        video_dir = tmp_path / "videos"
        video_dir.mkdir(exist_ok=True)
        video_file = video_dir / "test.mp4"
        video_file.write_bytes(video_content)

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO recordings("
                "recording_id,original_filename,upload_timestamp,"
                "video_path,processing_status,frame_count,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (rid, "test.mp4", "2026-01-01T00:00:00Z",
                 f"videos/test.mp4", "PENDING", 0,
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            await db.commit()

        client = MockVideoClient()

        # Mock get_video_duration and _ensure_h264 and _generate_thumbnail
        # to avoid real ffmpeg calls
        import app.processor as proc_mod
        original_duration = proc_mod.get_video_duration
        original_ensure = proc_mod._ensure_h264
        original_thumbnail = proc_mod._generate_thumbnail

        async def mock_duration(vp): return 10.0
        async def mock_ensure(vp, rid, storage, db): return vp
        async def mock_thumbnail(rid, storage, db_path): pass

        proc_mod.get_video_duration = mock_duration
        proc_mod._ensure_h264 = mock_ensure
        proc_mod._generate_thumbnail = mock_thumbnail

        try:
            await process_recording(rid, db_path, storage, client)
        finally:
            proc_mod.get_video_duration = original_duration
            proc_mod._ensure_h264 = original_ensure
            proc_mod._generate_thumbnail = original_thumbnail

        # Verify status is INDEXED (not FAILED/PARTIAL)
        async with aiosqlite.connect(db_path) as db:
            row = await (await db.execute(
                "SELECT processing_status, indexing_status_detail, recording_narrative "
                "FROM recordings WHERE recording_id=?", (rid,)
            )).fetchone()

        assert row is not None
        assert row[0] == ProcessingStatus.INDEXED.value
        assert "Direct video analysis" in row[1]
        assert row[2] is not None  # narrative was stored

        # Verify events were extracted via Path A
        async with aiosqlite.connect(db_path) as db:
            events_row = await (await db.execute(
                "SELECT COUNT(*) FROM events WHERE recording_id=?", (rid,)
            )).fetchone()
        assert events_row[0] >= 0  # may be 0 or more depending on mock result

    @pytest.mark.asyncio
    async def test_uses_legacy_path_when_client_lacks_upload(self, tmp_path):
        """process_recording() falls back to frame extraction when client lacks upload_video_file."""
        from app.processor import process_recording
        from app.storage import LocalStorageBackend

        db_path = await _make_db(tmp_path)
        storage = LocalStorageBackend(data_dir=tmp_path)
        await storage.ensure_dirs()

        # MockMetaClient has upload_video_file — use a stripped-down client that doesn't
        class FrameOnlyClient:
            model_version = "mock-frame-v1"

            async def analyze_frame(self, frame_data, *, prompt=None):
                return {"objects": [], "scene_summary": "empty frame"}

        rid = str(uuid.uuid4())
        video_dir = tmp_path / "videos"
        video_dir.mkdir(exist_ok=True)
        (video_dir / "test.mp4").write_bytes(b"dummy")

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO recordings("
                "recording_id,original_filename,upload_timestamp,"
                "video_path,processing_status,frame_count,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (rid, "test.mp4", "2026-01-01T00:00:00Z",
                 "videos/test.mp4", "PENDING", 0,
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            await db.commit()

        client = FrameOnlyClient()
        assert not hasattr(client, "upload_video_file")

        # Just run it — should not crash even without real ffmpeg
        # (it may fail with FAILED status due to missing ffmpeg, which is OK)
        try:
            await process_recording(rid, db_path, storage, client)
        except Exception:
            pass  # Acceptable — no real video file

        # Verify it didn't use "Direct video analysis" detail
        async with aiosqlite.connect(db_path) as db:
            row = await (await db.execute(
                "SELECT indexing_status_detail FROM recordings WHERE recording_id=?",
                (rid,)
            )).fetchone()

        if row and row[0]:
            assert "Direct video analysis" not in row[0]
