"""Tests for whole-recording narrative understanding (Phase 10)."""
import hashlib
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from app.meta_client import (
    MockMetaClient,
    ModelResponseError,
    validate_whole_recording_narrative,
)
from app.processor import _run_whole_recording_narrative
from app.database import create_tables, get_db


# ── Helpers ──────────────────────────────────────────────────────────────────

_MINIMAL_NARRATIVE = {
    "scene": "indoor corridor, overhead camera, daytime",
    "narrative_steps": [
        {
            "step": 1,
            "time_range": "0:00-0:10",
            "start_seconds": 0.0,
            "end_seconds": 10.0,
            "description": "Empty corridor visible throughout.",
        }
    ],
    "key_observations": ["No activity detected"],
    "open_questions": [],
    "entities": [],
}


async def _insert_recording(db_path: str, rid: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO recordings("
            "recording_id,original_filename,upload_timestamp,"
            "video_path,processing_status,frame_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (rid, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "INDEXED", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()


# ── validate_whole_recording_narrative ───────────────────────────────────────

class TestValidateWholeRecordingNarrative:
    def test_valid_minimal(self):
        result = validate_whole_recording_narrative(_MINIMAL_NARRATIVE)
        assert result is _MINIMAL_NARRATIVE

    def test_valid_without_entities(self):
        data = {
            "scene": "parking lot",
            "narrative_steps": [],
            "key_observations": [],
            "open_questions": ["Camera angle obscured"],
        }
        assert validate_whole_recording_narrative(data) is data

    def test_non_dict_raises(self):
        with pytest.raises(ModelResponseError) as exc_info:
            validate_whole_recording_narrative(["not", "a", "dict"])
        assert exc_info.value.code == "schema_violation"

    def test_missing_scene_raises(self):
        data = {"narrative_steps": [], "key_observations": []}
        with pytest.raises(ModelResponseError) as exc_info:
            validate_whole_recording_narrative(data)
        assert exc_info.value.code == "schema_violation"

    def test_missing_narrative_steps_raises(self):
        data = {"scene": "corridor", "key_observations": []}
        with pytest.raises(ModelResponseError) as exc_info:
            validate_whole_recording_narrative(data)
        assert exc_info.value.code == "schema_violation"

    def test_narrative_steps_not_list_raises(self):
        data = {"scene": "corridor", "narrative_steps": "not a list"}
        with pytest.raises(ModelResponseError) as exc_info:
            validate_whole_recording_narrative(data)
        assert exc_info.value.code == "schema_violation"

    def test_key_observations_not_list_raises(self):
        data = {
            "scene": "corridor",
            "narrative_steps": [],
            "key_observations": "not a list",
        }
        with pytest.raises(ModelResponseError) as exc_info:
            validate_whole_recording_narrative(data)
        assert exc_info.value.code == "schema_violation"


# ── MockMetaClient.analyze_whole_recording ───────────────────────────────────

class TestMockAnalyzeWholeRecording:
    @pytest.mark.asyncio
    async def test_returns_valid_structure(self):
        client = MockMetaClient()
        frame = b"fake_frame_data"
        result = await client.analyze_whole_recording([frame], [0.0])
        assert "scene" in result
        assert "narrative_steps" in result
        assert isinstance(result["narrative_steps"], list)
        assert "key_observations" in result

    @pytest.mark.asyncio
    async def test_default_narrative_has_one_step(self):
        client = MockMetaClient()
        result = await client.analyze_whole_recording([b"frame1", b"frame2"], [0.0, 5.0])
        assert len(result["narrative_steps"]) >= 1

    @pytest.mark.asyncio
    async def test_sentinel_key_returns_injected_narrative(self):
        frame = b"special_frame"
        h = hashlib.sha256(frame).hexdigest()
        injected = {
            "scene": "parking garage, injected",
            "narrative_steps": [
                {"step": 1, "time_range": "0:00-0:30",
                 "start_seconds": 0.0, "end_seconds": 30.0,
                 "description": "Injected narrative step."}
            ],
            "key_observations": ["injected observation"],
            "open_questions": [],
            "entities": [],
        }
        client = MockMetaClient(scenario_map={f"narrative:{h}": injected})
        result = await client.analyze_whole_recording([frame, b"other"], [0.0, 5.0])
        assert result["scene"] == "parking garage, injected"

    @pytest.mark.asyncio
    async def test_empty_frames_raises(self):
        client = MockMetaClient()
        with pytest.raises(ValueError, match="requires at least one frame"):
            await client.analyze_whole_recording([], [])

    @pytest.mark.asyncio
    async def test_mismatched_lengths_raises(self):
        client = MockMetaClient()
        with pytest.raises(ValueError, match="equal length"):
            await client.analyze_whole_recording([b"frame"], [0.0, 1.0])

    @pytest.mark.asyncio
    async def test_increments_call_count(self):
        client = MockMetaClient()
        await client.analyze_whole_recording([b"f"], [0.0])
        await client.analyze_whole_recording([b"f2"], [1.0])
        assert client._call_count == 2


# ── _run_whole_recording_narrative ───────────────────────────────────────────

class TestRunWholeRecordingNarrative:
    @pytest.mark.asyncio
    async def test_stores_narrative_in_db(self, tmp_path: Path):
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)

        # Create a fake frame file
        frame_dir = tmp_path / "frames" / rid
        frame_dir.mkdir(parents=True)
        frame_file = frame_dir / "frame_000001.jpg"
        frame_file.write_bytes(b"fake_jpeg_data")
        rel_path = f"frames/{rid}/frame_000001.jpg"

        storage = MagicMock()
        storage.data_dir = tmp_path

        client = MockMetaClient()
        errors: list = []

        result = await _run_whole_recording_narrative(
            recording_id=rid,
            db_path=db_path,
            storage=storage,
            meta_client=client,
            indexed_frame_paths=[(0.0, rel_path)],
            errors=errors,
        )
        assert result is True
        assert errors == []

        async with aiosqlite.connect(db_path) as db:
            row = await (await db.execute(
                "SELECT recording_narrative FROM recordings WHERE recording_id=?",
                (rid,)
            )).fetchone()
        assert row is not None
        narrative = json.loads(row[0])
        assert "scene" in narrative
        assert "narrative_steps" in narrative

    @pytest.mark.asyncio
    async def test_samples_up_to_20_frames(self, tmp_path: Path):
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)

        # Create 40 fake frame files
        frame_dir = tmp_path / "frames" / rid
        frame_dir.mkdir(parents=True)
        paths = []
        for i in range(40):
            f = frame_dir / f"frame_{i:06d}.jpg"
            f.write_bytes(b"fake_jpeg_data")
            paths.append((float(i), f"frames/{rid}/frame_{i:06d}.jpg"))

        storage = MagicMock()
        storage.data_dir = tmp_path

        call_count_tracker = []

        class CountingClient(MockMetaClient):
            async def analyze_whole_recording(self, frames, timestamps):
                call_count_tracker.append(len(frames))
                return await super().analyze_whole_recording(frames, timestamps)

        client = CountingClient()
        await _run_whole_recording_narrative(
            recording_id=rid, db_path=db_path, storage=storage,
            meta_client=client, indexed_frame_paths=paths, errors=[],
        )
        # Should have sampled at most 20 frames
        assert len(call_count_tracker) == 1
        assert call_count_tracker[0] <= 20

    @pytest.mark.asyncio
    async def test_skips_if_no_method(self, tmp_path: Path):
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)

        frame_dir = tmp_path / "frames" / rid
        frame_dir.mkdir(parents=True)
        f = frame_dir / "frame_000001.jpg"
        f.write_bytes(b"fake_jpeg_data")

        storage = MagicMock()
        storage.data_dir = tmp_path

        # Client without analyze_whole_recording
        class NoNarrativeClient:
            model_version = "test-v1"
            async def analyze_frame(self, data, **kw):
                return {}

        result = await _run_whole_recording_narrative(
            recording_id=rid, db_path=db_path, storage=storage,
            meta_client=NoNarrativeClient(),
            indexed_frame_paths=[(0.0, f"frames/{rid}/frame_000001.jpg")],
            errors=[],
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_empty_paths(self, tmp_path: Path):
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)
        storage = MagicMock()
        storage.data_dir = tmp_path
        result = await _run_whole_recording_narrative(
            recording_id=rid, db_path=db_path, storage=storage,
            meta_client=MockMetaClient(), indexed_frame_paths=[], errors=[],
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_appends_error_on_model_failure(self, tmp_path: Path):
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)

        frame_dir = tmp_path / "frames" / rid
        frame_dir.mkdir(parents=True)
        f = frame_dir / "frame_000001.jpg"
        f.write_bytes(b"fake_jpeg_data")

        storage = MagicMock()
        storage.data_dir = tmp_path

        class FailingClient(MockMetaClient):
            async def analyze_whole_recording(self, frames, timestamps):
                raise RuntimeError("model timeout")

        errors: list = []
        result = await _run_whole_recording_narrative(
            recording_id=rid, db_path=db_path, storage=storage,
            meta_client=FailingClient(),
            indexed_frame_paths=[(0.0, f"frames/{rid}/frame_000001.jpg")],
            errors=errors,
        )
        assert result is False
        assert len(errors) == 1
        assert "model timeout" in errors[0]
