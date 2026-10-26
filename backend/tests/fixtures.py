"""Shared test fixtures and helpers for FindIt tests."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Fixed UUIDs for predictable test data
RECORDING_ID_1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
RECORDING_ID_2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

# A known frame content → deterministic hash
FRAME_DATA_BLUE_NOTEBOOK = b"\xff\xd8\xff" + b"\x00" * 100 + b"blue_notebook_test_frame"
FRAME_DATA_RED_CAR = b"\xff\xd8\xff" + b"\x00" * 100 + b"red_car_test_frame"
FRAME_DATA_EMPTY = b"\xff\xd8\xff" + b"\x00" * 100 + b"empty_scene_test_frame"

FRAME_HASH_BLUE_NOTEBOOK = hashlib.sha256(FRAME_DATA_BLUE_NOTEBOOK).hexdigest()
FRAME_HASH_RED_CAR = hashlib.sha256(FRAME_DATA_RED_CAR).hexdigest()
FRAME_HASH_EMPTY = hashlib.sha256(FRAME_DATA_EMPTY).hexdigest()


# Minimal valid MP4-like bytes (not a real video; triggers graceful ffmpeg failure)
FAKE_VIDEO_BYTES = (
    b"\x00\x00\x00\x18ftypisom"
    + b"\x00" * 100
)

# Mock model response for blue notebook frame
MOCK_RESPONSE_BLUE_NOTEBOOK = {
    "objects": [
        {
            "label": "blue notebook",
            "attributes": {
                "color": "blue",
                "shape": "rectangular",
                "size": "A4",
                "distinctive_details": "spiral binding on left side",
            },
            "scene_description": "A blue spiral notebook lying on a wooden desk",
            "confidence": 0.92,
        }
    ],
    "scene_summary": "Wooden desk with a blue notebook",
}

# Mock model response for red car frame
MOCK_RESPONSE_RED_CAR = {
    "objects": [
        {
            "label": "red car",
            "attributes": {
                "color": "red",
                "shape": "sedan",
                "size": "medium",
                "distinctive_details": "dented rear bumper",
            },
            "scene_description": "A red sedan parked on the street",
            "confidence": 0.88,
        }
    ],
    "scene_summary": "Street parking with a red car",
}

# Mock model response with no objects
MOCK_RESPONSE_EMPTY = {
    "objects": [],
    "scene_summary": "Empty room with no identifiable objects",
}


def make_scenario_map() -> dict:
    """Return a scenario_map for MockMetaClient with all test hashes mapped."""
    return {
        FRAME_HASH_BLUE_NOTEBOOK: MOCK_RESPONSE_BLUE_NOTEBOOK,
        FRAME_HASH_RED_CAR: MOCK_RESPONSE_RED_CAR,
        FRAME_HASH_EMPTY: MOCK_RESPONSE_EMPTY,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def insert_recording(
    db,
    recording_id: str,
    filename: str = "test_video.mp4",
    recording_datetime: Optional[str] = None,
    recording_timezone: Optional[str] = None,
    processing_status: str = "INDEXED",
    video_path: str = "videos/test.mp4",
):
    """Helper to insert a recording row directly into the test DB."""
    now = now_iso()
    await db.execute(
        """INSERT OR REPLACE INTO recordings(
            recording_id, original_filename, upload_timestamp,
            recording_datetime, recording_timezone, video_path,
            processing_status, frame_count, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            recording_id, filename, now,
            recording_datetime, recording_timezone, video_path,
            processing_status, 0, now, now,
        ),
    )
    await db.commit()


async def insert_observation(
    db,
    recording_id: str,
    object_label: str,
    video_timestamp_seconds: float = 5.0,
    recording_datetime: Optional[str] = None,
    recording_timezone: Optional[str] = "UTC",
    object_attributes: Optional[dict] = None,
    scene_description: Optional[str] = None,
    detection_score: float = 0.9,
    source_frame_path: str = "frames/test/frame.jpg",
):
    """Helper to insert an observation row directly into the test DB."""
    observation_id = str(uuid.uuid4())
    attrs = object_attributes or {}
    await db.execute(
        """INSERT OR REPLACE INTO observations(
            observation_id, recording_id, frame_id,
            video_timestamp_seconds,
            recording_datetime, recording_timezone,
            object_label, object_attributes, scene_description,
            source_frame_path, detection_score, model_version,
            index_version, content_hash, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            observation_id, recording_id, f"{recording_id}_frame",
            video_timestamp_seconds,
            recording_datetime, recording_timezone,
            object_label, json.dumps(attrs), scene_description,
            source_frame_path, detection_score, "mock-v1",
            "1", "deadbeef", now_iso(),
        ),
    )
    await db.commit()
    return observation_id
