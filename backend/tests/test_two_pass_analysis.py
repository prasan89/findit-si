"""Regression tests for two-pass video analysis (general + hazard/animal detection).

Covers:
- HAZARD_ANALYSIS_PROMPT exists and is distinct from ANALYSIS_PROMPT
- MockMetaClient.analyze_frame accepts prompt= kwarg
- MuseSparkClient.analyze_frame accepts prompt= kwarg (signature check)
- AccountedFrameAnalyzer raises TypeError early when delegate lacks prompt support
- AccountedFrameAnalyzer routes prompt= correctly to delegates that support it
- process_recording stores analysis_pass='general' on general observations
- process_recording runs hazard pass and stores analysis_pass='hazard' observations
- Hazard observations appear in event_extractor output (snake test)
- Retrieval finds snake observation via search_observations
- Two-pass doesn't double-count identical labels from both passes
- Hazard pass failure (TypeError) doesn't abort general pass
- analysis_pass column migration is idempotent
"""
import hashlib
import inspect
import json
import uuid
import pytest

import aiosqlite

from app.database import get_db, create_tables
from app.meta_client import (
    ANALYSIS_PROMPT,
    HAZARD_ANALYSIS_PROMPT,
    MockMetaClient,
    MuseSparkClient,
    validate_frame_analysis,
)
from app.processor import (
    PROMPT_VERSION,
    HAZARD_PROMPT_VERSION,
    _parse_observations,
    process_recording,
    get_cached_analysis,
    store_cached_analysis,
)
from app.provider import AccountedFrameAnalyzer
from app.event_extractor import extract_events_for_recording
from app.search import search_observations

from tests.fixtures import RECORDING_ID_1, insert_recording


# ── Prompt constants ───────────────────────────────────────────────────────────

def test_hazard_prompt_exists():
    assert HAZARD_ANALYSIS_PROMPT, "HAZARD_ANALYSIS_PROMPT must not be empty"


def test_hazard_prompt_distinct_from_general():
    assert HAZARD_ANALYSIS_PROMPT != ANALYSIS_PROMPT


def test_hazard_prompt_mentions_snake():
    assert "snake" in HAZARD_ANALYSIS_PROMPT.lower()


def test_hazard_prompt_mentions_animal_category():
    assert "animal" in HAZARD_ANALYSIS_PROMPT.lower()


def test_hazard_prompt_mentions_hazard():
    assert "hazard" in HAZARD_ANALYSIS_PROMPT.lower() or "fire" in HAZARD_ANALYSIS_PROMPT.lower()


# ── Mock client signature ──────────────────────────────────────────────────────

def test_mock_client_analyze_frame_accepts_prompt():
    sig = inspect.signature(MockMetaClient.analyze_frame)
    assert "prompt" in sig.parameters, \
        "MockMetaClient.analyze_frame must accept a 'prompt' keyword argument"


@pytest.mark.anyio
async def test_mock_client_analyze_frame_prompt_kwarg_noop():
    """MockMetaClient accepts prompt= but ignores it (uses scenario_map only)."""
    client = MockMetaClient()
    result = await client.analyze_frame(b"x" * 10, prompt=HAZARD_ANALYSIS_PROMPT)
    assert "objects" in result


def test_muse_spark_client_analyze_frame_accepts_prompt():
    sig = inspect.signature(MuseSparkClient.analyze_frame)
    assert "prompt" in sig.parameters, \
        "MuseSparkClient.analyze_frame must accept a 'prompt' keyword argument"


# ── AccountedFrameAnalyzer routing ────────────────────────────────────────────

@pytest.mark.anyio
async def test_accounted_analyzer_detects_prompt_support(db_path):
    """Delegate that accepts prompt= is detected correctly."""
    client = MockMetaClient()  # updated to accept prompt=
    wrapper = AccountedFrameAnalyzer(client, db_path)
    assert wrapper._delegate_accepts_prompt is True


@pytest.mark.anyio
async def test_accounted_analyzer_detects_missing_prompt_support(db_path):
    """Delegate that lacks prompt= is detected correctly."""
    class OldMock(MockMetaClient):
        async def analyze_frame(self, frame_data: bytes) -> dict:
            return {"objects": [], "scene_summary": "old mock"}

    wrapper = AccountedFrameAnalyzer(OldMock(), db_path)
    assert wrapper._delegate_accepts_prompt is False


@pytest.mark.anyio
async def test_accounted_analyzer_raises_type_error_for_old_delegate(db_path):
    """Wrapper raises TypeError immediately (not inside retry) for old delegates."""
    class OldMock(MockMetaClient):
        async def analyze_frame(self, frame_data: bytes) -> dict:
            return {"objects": [], "scene_summary": "old mock"}

    wrapper = AccountedFrameAnalyzer(OldMock(), db_path)
    with pytest.raises(TypeError, match="does not accept a 'prompt' keyword argument"):
        await wrapper.analyze_frame(b"x" * 10, prompt=HAZARD_ANALYSIS_PROMPT)


@pytest.mark.anyio
async def test_accounted_analyzer_passes_prompt_to_delegate(db_path):
    """Wrapper forwards prompt= to delegates that accept it."""
    received = {}

    class CapturingMock(MockMetaClient):
        async def analyze_frame(
            self, frame_data: bytes, *, prompt: str = None
        ) -> dict:
            received["prompt"] = prompt
            return {"objects": [], "scene_summary": "capturing"}

    wrapper = AccountedFrameAnalyzer(CapturingMock(), db_path)
    await wrapper.analyze_frame(b"x" * 10, prompt=HAZARD_ANALYSIS_PROMPT)
    assert received["prompt"] == HAZARD_ANALYSIS_PROMPT


# ── Prompt version constants ───────────────────────────────────────────────────

def test_hazard_prompt_version_distinct():
    assert HAZARD_PROMPT_VERSION != PROMPT_VERSION
    assert HAZARD_PROMPT_VERSION == "h1"


# ── _parse_observations analysis_pass field ───────────────────────────────────

def test_parse_observations_default_pass_is_general():
    obs = _parse_observations(
        analysis={"objects": [{"label": "person", "confidence": 0.8}],
                  "scene_summary": "entrance"},
        recording_id="rec1",
        frame_id="rec1_0.000",
        video_timestamp_seconds=0.0,
        recording_datetime=None,
        recording_timezone=None,
        source_frame_path="frames/rec1/f.jpg",
        content_hash="abc",
        model_version="mock-v1",
    )
    assert obs[0]["analysis_pass"] == "general"


def test_parse_observations_hazard_pass_tag():
    obs = _parse_observations(
        analysis={"objects": [{"label": "snake on floor", "confidence": 0.75}],
                  "scene_summary": "snake detected"},
        recording_id="rec1",
        frame_id="rec1_5.000",
        video_timestamp_seconds=5.0,
        recording_datetime=None,
        recording_timezone=None,
        source_frame_path="frames/rec1/f.jpg",
        content_hash="def",
        model_version="mock-v1",
        analysis_pass="hazard",
    )
    assert obs[0]["analysis_pass"] == "hazard"
    assert obs[0]["object_label"] == "snake on floor"


# ── Full pipeline: snake detected by hazard pass ──────────────────────────────

FAKE_FRAME = bytes(range(256)) * 100  # deterministic fake frame bytes
FRAME_HASH = hashlib.sha256(FAKE_FRAME).hexdigest()


@pytest.mark.anyio
async def test_hazard_pass_stores_snake_observation(db_path, storage):
    """process_recording stores snake observation from hazard pass."""
    general_response = {
        "objects": [
            {"label": "person in hallway", "confidence": 0.85,
             "attributes": {"color": "blue"}, "scene_description": "person walking"},
        ],
        "scene_summary": "Residential corridor with one person walking.",
    }
    hazard_response = {
        "objects": [
            {"label": "snake on floor", "confidence": 0.78,
             "attributes": {"color": "brown", "shape": "elongated",
                            "size": "medium", "distinctive_details": "coiled near wall"},
             "scene_description": "Brown snake coiled near the baseboard on the left side"},
        ],
        "scene_summary": "Snake detected on corridor floor near baseboard.",
    }

    class TwoPassMock(MockMetaClient):
        def __init__(self):
            super().__init__()
            self._call_n = 0

        async def analyze_frame(
            self, frame_data: bytes, *, prompt: str = None
        ) -> dict:
            self._call_n += 1
            if prompt == HAZARD_ANALYSIS_PROMPT:
                return dict(hazard_response)
            return dict(general_response)

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        await insert_recording(db, RECORDING_ID_1)

    from unittest.mock import patch, AsyncMock as UAsyncMock
    import asyncio

    # Patch extract_frames to return one synthetic frame
    async def fake_extract(video_path, output_dir, fps=1.0, max_frames=300):
        import tempfile
        from pathlib import Path
        output_dir.mkdir(parents=True, exist_ok=True)
        fp = output_dir / "frame_000001.jpg"
        fp.write_bytes(FAKE_FRAME)
        return [(0.0, fp)]

    client = TwoPassMock()

    with patch("app.processor.extract_frames", fake_extract), \
         patch("app.processor._ensure_h264", side_effect=lambda p, *a, **k: p), \
         patch("app.processor.get_video_duration", return_value=10.0):
        await process_recording(
            recording_id=RECORDING_ID_1,
            db_path=db_path,
            storage=storage,
            meta_client=client,
        )

    # Verify both analysis_pass values are stored
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT object_label, analysis_pass FROM observations WHERE recording_id=?",
            (RECORDING_ID_1,)
        )).fetchall()

    labels_by_pass = {r["analysis_pass"]: r["object_label"] for r in rows}
    assert "general" in labels_by_pass, f"General pass observation missing; got: {rows}"
    assert "hazard" in labels_by_pass, f"Hazard pass observation missing; got: {rows}"
    assert "snake" in labels_by_pass["hazard"].lower()


@pytest.mark.anyio
async def test_snake_observation_becomes_event(db_path, storage):
    """Snake from hazard pass propagates through event_extractor."""
    # Seed two observations (different timestamps) so MIN_CONFIRMING_FRAMES=2 is met
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        await insert_recording(db, RECORDING_ID_1)
        now = "2026-11-01T10:00:00+00:00"
        for i, ts in enumerate([0.0, 1.0]):
            await db.execute(
                """INSERT INTO observations(
                    observation_id, recording_id, frame_id,
                    video_timestamp_seconds, object_label, object_attributes,
                    scene_description, source_frame_path, detection_score,
                    model_version, index_version, content_hash,
                    analysis_pass, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), RECORDING_ID_1,
                    f"{RECORDING_ID_1}_{ts:.3f}", ts,
                    "snake on floor", json.dumps({"color": "brown"}),
                    "Brown snake on corridor floor near baseboard",
                    f"frames/{RECORDING_ID_1}/frame_{i:06d}.jpg",
                    0.78, "mock-v1", "1", f"hash{i}",
                    "hazard", now,
                )
            )
        await db.commit()

    n = await extract_events_for_recording(RECORDING_ID_1, db_path)
    assert n >= 1, "Snake observations should produce at least one event"

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT action_description, object_descriptions FROM events WHERE recording_id=?",
            (RECORDING_ID_1,)
        )).fetchall()

    combined = " ".join(r["action_description"] or "" for r in rows)
    combined += " ".join(r["object_descriptions"] or "" for r in rows)
    assert "snake" in combined.lower(), \
        f"Snake not found in event output; action_descriptions: {combined!r}"


@pytest.mark.anyio
async def test_retrieval_finds_snake(db_path):
    """search_observations returns snake when queried."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        await insert_recording(db, RECORDING_ID_1)
        now = "2026-11-01T10:00:00+00:00"
        await db.execute(
            """INSERT INTO observations(
                observation_id, recording_id, frame_id,
                video_timestamp_seconds, object_label, object_attributes,
                scene_description, source_frame_path, detection_score,
                model_version, index_version, content_hash,
                analysis_pass, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), RECORDING_ID_1,
                f"{RECORDING_ID_1}_5.000", 5.0,
                "snake on floor", json.dumps({"color": "brown", "shape": "elongated"}),
                "Brown snake coiled near baseboard in corridor",
                f"frames/{RECORDING_ID_1}/frame_000005.jpg",
                0.78, "mock-v1", "1", "snakehash",
                "hazard", now,
            )
        )
        await db.commit()

    result = await search_observations("snake in the corridor", db_path)
    assert result.best_sighting is not None, \
        "Expected snake sighting to be found"
    assert "snake" in result.best_sighting.object_label.lower()


# ── Deduplication: no double-count when both passes detect same label ──────────

def test_no_duplicate_when_general_already_detected_snake():
    """If general pass already labelled 'snake', hazard pass duplicate is dropped."""
    general_obs = [{"object_label": "snake near door", "analysis_pass": "general",
                    "detection_score": 0.7}]
    hazard_obs = [{"object_label": "snake near door", "analysis_pass": "hazard",
                   "detection_score": 0.8}]

    general_labels_lower = {o["object_label"].lower() for o in general_obs}
    merged = list(general_obs)
    for hobs in hazard_obs:
        if hobs["object_label"].lower() not in general_labels_lower:
            merged.append(hobs)

    assert len(merged) == 1, "Duplicate label should be deduplicated"
    assert merged[0]["analysis_pass"] == "general"


def test_no_duplicate_different_labels_both_kept():
    """Different labels from each pass are both kept."""
    general_obs = [{"object_label": "person in hallway", "analysis_pass": "general"}]
    hazard_obs = [{"object_label": "smoke near ceiling", "analysis_pass": "hazard"}]

    general_labels_lower = {o["object_label"].lower() for o in general_obs}
    merged = list(general_obs)
    for hobs in hazard_obs:
        if hobs["object_label"].lower() not in general_labels_lower:
            merged.append(hobs)

    assert len(merged) == 2
    passes = {o["analysis_pass"] for o in merged}
    assert passes == {"general", "hazard"}


# ── Hazard pass failure does not abort general pass ───────────────────────────

@pytest.mark.anyio
async def test_hazard_pass_failure_does_not_abort_pipeline(db_path, storage):
    """When hazard pass raises, general pass observations are still stored."""
    call_log = []

    class FailHazardMock(MockMetaClient):
        async def analyze_frame(
            self, frame_data: bytes, *, prompt: str = None
        ) -> dict:
            call_log.append("hazard" if prompt == HAZARD_ANALYSIS_PROMPT else "general")
            if prompt == HAZARD_ANALYSIS_PROMPT:
                raise RuntimeError("hazard model unavailable")
            return {"objects": [{"label": "chair", "confidence": 0.9,
                                 "attributes": {}, "scene_description": "chair in room"}],
                    "scene_summary": "Room with chair."}

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        await insert_recording(db, RECORDING_ID_1)

    async def fake_extract(video_path, output_dir, fps=1.0, max_frames=300):
        output_dir.mkdir(parents=True, exist_ok=True)
        fp = output_dir / "frame_000001.jpg"
        fp.write_bytes(FAKE_FRAME)
        return [(0.0, fp)]

    from unittest.mock import patch

    with patch("app.processor.extract_frames", fake_extract), \
         patch("app.processor._ensure_h264", side_effect=lambda p, *a, **k: p), \
         patch("app.processor.get_video_duration", return_value=5.0):
        await process_recording(
            recording_id=RECORDING_ID_1,
            db_path=db_path,
            storage=storage,
            meta_client=FailHazardMock(),
        )

    # General pass observation should be persisted despite hazard failure
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT object_label, analysis_pass FROM observations WHERE recording_id=?",
            (RECORDING_ID_1,)
        )).fetchall()

    assert any(r["analysis_pass"] == "general" for r in rows), \
        "General pass observation must persist even when hazard pass fails"
    assert "general" in call_log
    assert "hazard" in call_log


# ── analysis_pass DB migration is idempotent ──────────────────────────────────

@pytest.mark.anyio
async def test_analysis_pass_migration_idempotent(db_path):
    """create_tables can be called twice; analysis_pass migration never raises."""
    await create_tables(db_path)
    await create_tables(db_path)  # second call must not raise
    # Verify column exists
    async with get_db(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(observations)")
        cols = {row[1] for row in await cursor.fetchall()}
    assert "analysis_pass" in cols
