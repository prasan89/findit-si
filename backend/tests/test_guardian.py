"""Tests for Guardian hazard detection pipeline."""
import hashlib
import json
import uuid

import aiosqlite
import pytest

from app.meta_client import (
    MockMetaClient,
    validate_guardian_hazard_response,
    ModelResponseError,
)
from app.guardian import (
    compute_dedup_key,
    merge_overlapping_hazards,
    upsert_guardian_alert,
    get_alerts_for_recording,
    update_alert_review_status,
    run_guardian_detection,
    VALID_HAZARD_CATEGORIES,
    _classify_hazards_from_understanding,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

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


# ── Validate hazard response ─────────────────────────────────────────────────

class TestValidateGuardianHazardResponse:
    def test_valid_full_response(self):
        data = {
            "hazards": [{
                "hazard_category": "fire_smoke",
                "severity": "high",
                "confidence": 0.85,
                "timestamp_seconds": 3.0,
                "description": "Visible flames on right wall",
            }],
            "scene_clear": False,
        }
        result = validate_guardian_hazard_response(data)
        assert result is data

    def test_empty_hazards_valid(self):
        data = {"hazards": [], "scene_clear": True}
        result = validate_guardian_hazard_response(data)
        assert result is data

    def test_non_dict_raises(self):
        with pytest.raises(ModelResponseError, match="not a JSON object"):
            validate_guardian_hazard_response([{"hazard_category": "fire_smoke"}])

    def test_hazards_not_list_raises(self):
        with pytest.raises(ModelResponseError, match="must be a list"):
            validate_guardian_hazard_response({"hazards": "fire"})

    def test_missing_category_raises(self):
        with pytest.raises(ModelResponseError, match="hazard_category"):
            validate_guardian_hazard_response({
                "hazards": [{"severity": "high", "confidence": 0.8, "description": "fire"}]
            })

    def test_missing_description_raises(self):
        with pytest.raises(ModelResponseError, match="description"):
            validate_guardian_hazard_response({
                "hazards": [{"hazard_category": "fire_smoke", "severity": "high", "confidence": 0.8}]
            })

    def test_invalid_confidence_raises(self):
        with pytest.raises(ModelResponseError, match="confidence"):
            validate_guardian_hazard_response({
                "hazards": [{
                    "hazard_category": "fire_smoke",
                    "severity": "high",
                    "confidence": 1.5,
                    "description": "fire",
                }]
            })

    def test_needs_review_field_preserved(self):
        data = {
            "hazards": [{
                "hazard_category": "dangerous_animal",
                "severity": "medium",
                "confidence": 0.45,
                "description": "Snake on floor",
                "needs_review": True,
            }],
        }
        result = validate_guardian_hazard_response(data)
        assert result["hazards"][0]["needs_review"] is True


# ── MockMetaClient.detect_hazards_for_segment ────────────────────────────────

class TestMockDetectHazards:
    @pytest.mark.asyncio
    async def test_default_returns_no_hazards(self):
        client = MockMetaClient()
        result = await client.detect_hazards_for_segment([b"frame"], [0.0])
        assert result["hazards"] == []
        assert result["scene_clear"] is True

    @pytest.mark.asyncio
    async def test_uses_guardian_sentinel_key(self):
        frame = b"fire_frame"
        h = hashlib.sha256(frame).hexdigest()
        fire_response = {
            "hazards": [{
                "hazard_category": "fire_smoke",
                "severity": "critical",
                "confidence": 0.92,
                "timestamp_seconds": 5.0,
                "description": "Flames visible",
            }],
            "scene_clear": False,
        }
        client = MockMetaClient(scenario_map={f"guardian:{h}": fire_response})
        result = await client.detect_hazards_for_segment([frame], [5.0])
        assert len(result["hazards"]) == 1
        assert result["hazards"][0]["hazard_category"] == "fire_smoke"

    @pytest.mark.asyncio
    async def test_empty_frames_raises(self):
        client = MockMetaClient()
        with pytest.raises(ValueError, match="at least one frame"):
            await client.detect_hazards_for_segment([], [])

    @pytest.mark.asyncio
    async def test_mismatched_lengths_raise(self):
        client = MockMetaClient()
        with pytest.raises(ValueError, match="equal length"):
            await client.detect_hazards_for_segment([b"f1", b"f2"], [0.0])

    @pytest.mark.asyncio
    async def test_no_sentinel_returns_safe(self):
        frame = b"normal_frame"
        client = MockMetaClient()
        result = await client.detect_hazards_for_segment([frame], [1.0])
        assert result["scene_clear"] is True
        assert result["hazards"] == []


# ── Deduplication ─────────────────────────────────────────────────────────────

class TestDedupKey:
    def test_same_category_same_bucket(self):
        k1 = compute_dedup_key("fire_smoke", 0.0)
        k2 = compute_dedup_key("fire_smoke", 5.0)
        assert k1 == k2

    def test_different_bucket(self):
        k1 = compute_dedup_key("fire_smoke", 0.0)
        k2 = compute_dedup_key("fire_smoke", 31.0)
        assert k1 != k2

    def test_different_category_different_key(self):
        k1 = compute_dedup_key("fire_smoke", 5.0)
        k2 = compute_dedup_key("dangerous_animal", 5.0)
        assert k1 != k2


class TestMergeOverlappingHazards:
    def _h(self, cat, start, end, conf=0.7, desc="test"):
        return {
            "hazard_category": cat,
            "severity": "high",
            "confidence": conf,
            "timestamp_seconds": start,
            "time_range_start": start,
            "time_range_end": end,
            "description": desc,
            "evidence_frames": [],
            "segment_id": str(uuid.uuid4()),
        }

    def test_non_overlapping_same_category_not_merged(self):
        raw = [self._h("fire_smoke", 0.0, 8.0), self._h("fire_smoke", 40.0, 48.0)]
        merged = merge_overlapping_hazards(raw)
        assert len(merged) == 2

    def test_overlapping_same_category_merged(self):
        raw = [self._h("fire_smoke", 0.0, 10.0), self._h("fire_smoke", 8.0, 18.0)]
        merged = merge_overlapping_hazards(raw)
        assert len(merged) == 1
        assert merged[0]["time_range_start"] == 0.0
        assert merged[0]["time_range_end"] == 18.0

    def test_adjacent_segments_merged(self):
        raw = [self._h("fire_smoke", 0.0, 10.0), self._h("fire_smoke", 10.0, 20.0)]
        merged = merge_overlapping_hazards(raw)
        assert len(merged) == 1

    def test_different_categories_not_merged(self):
        raw = [self._h("fire_smoke", 0.0, 10.0), self._h("dangerous_animal", 0.0, 10.0)]
        merged = merge_overlapping_hazards(raw)
        assert len(merged) == 2

    def test_merged_takes_max_confidence(self):
        raw = [self._h("fire_smoke", 0.0, 10.0, conf=0.6), self._h("fire_smoke", 8.0, 18.0, conf=0.9)]
        merged = merge_overlapping_hazards(raw)
        assert merged[0]["confidence"] == 0.9

    def test_empty_input_returns_empty(self):
        assert merge_overlapping_hazards([]) == []


# ── Upsert idempotency ────────────────────────────────────────────────────────

class TestUpsertGuardianAlert:
    def _make_alert(self, cat="fire_smoke", start=0.0, desc="Flames visible"):
        return {
            "hazard_category": cat,
            "severity": "high",
            "confidence": 0.85,
            "timestamp_seconds": start,
            "time_range_start": start,
            "time_range_end": start + 10.0,
            "description": desc,
            "evidence_frames": [],
            "supporting_segment_ids": [],
        }

    @pytest.mark.asyncio
    async def test_first_insert_returns_true(self, db_path):
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)
        result = await upsert_guardian_alert(db_path, rid, self._make_alert())
        assert result is True

    @pytest.mark.asyncio
    async def test_second_insert_same_key_returns_false(self, db_path):
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)
        alert = self._make_alert()
        await upsert_guardian_alert(db_path, rid, alert)
        result = await upsert_guardian_alert(db_path, rid, alert)
        assert result is False

    @pytest.mark.asyncio
    async def test_new_alert_has_new_review_status(self, db_path):
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)
        await upsert_guardian_alert(db_path, rid, self._make_alert())
        alerts = await get_alerts_for_recording(db_path, rid)
        assert alerts[0]["review_status"] == "new"

    @pytest.mark.asyncio
    async def test_different_bucket_creates_two_alerts(self, db_path):
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)
        await upsert_guardian_alert(db_path, rid, self._make_alert(start=0.0))
        await upsert_guardian_alert(db_path, rid, self._make_alert(start=40.0))
        alerts = await get_alerts_for_recording(db_path, rid)
        assert len(alerts) == 2


# ── run_guardian_detection integration ───────────────────────────────────────

@pytest.mark.asyncio
async def test_guardian_no_unusual_observations_no_alerts(db_path, storage):
    recording_id = str(uuid.uuid4())
    await _insert_recording(db_path, recording_id)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO upload_understandings(segment_id,recording_id,segment_hash,"
            "start_seconds,end_seconds,static_scene,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), recording_id, "hash1", 0.0, 10.0, 1, "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    errors: list = []
    count = await run_guardian_detection(recording_id, db_path, storage, MockMetaClient(), errors)
    assert count == 0
    alerts = await get_alerts_for_recording(db_path, recording_id)
    assert len(alerts) == 0


@pytest.mark.asyncio
async def test_guardian_fire_keyword_triggers_detection(db_path, storage):
    recording_id = str(uuid.uuid4())
    frame_bytes = b"fire_test_frame"
    frame_hash = hashlib.sha256(frame_bytes).hexdigest()

    # Write a real frame file using storage.data_dir
    frames_dir = storage.data_dir / "frames" / recording_id
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_path = frames_dir / "f001.jpg"
    frame_path.write_bytes(frame_bytes)
    rel_path = f"frames/{recording_id}/f001.jpg"

    await _insert_recording(db_path, recording_id)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO observations(observation_id,recording_id,frame_id,"
            "video_timestamp_seconds,object_label,source_frame_path,detection_score,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), recording_id, "f1", 5.0,
             "test object", rel_path, 0.8, "2026-01-01T00:00:00Z"),
        )
        await db.execute(
            "INSERT INTO upload_understandings(segment_id,recording_id,segment_hash,"
            "start_seconds,end_seconds,unusual_observations_json,static_scene,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), recording_id, "hash1", 0.0, 10.0,
             json.dumps(["visible fire on left wall"]), 0, "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    fire_response = {
        "hazards": [{
            "hazard_category": "fire_smoke",
            "severity": "critical",
            "confidence": 0.90,
            "timestamp_seconds": 5.0,
            "description": "Visible flames on left wall at 5s",
        }],
        "scene_clear": False,
    }
    client = MockMetaClient(scenario_map={f"guardian:{frame_hash}": fire_response})

    errors: list = []
    count = await run_guardian_detection(recording_id, db_path, storage, client, errors)
    assert count >= 1
    alerts = await get_alerts_for_recording(db_path, recording_id)
    fire_alerts = [a for a in alerts if a["hazard_category"] == "fire_smoke"]
    assert len(fire_alerts) >= 1
    assert fire_alerts[0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_guardian_deduplication_adjacent_segments(db_path, storage):
    """Two adjacent segments with fire → merged into one alert (same 30s bucket)."""
    recording_id = str(uuid.uuid4())
    frame_bytes = b"fire_frame_dedup"
    frame_hash = hashlib.sha256(frame_bytes).hexdigest()

    frames_dir = storage.data_dir / "frames" / recording_id
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        p = frames_dir / f"f{i:03d}.jpg"
        p.write_bytes(frame_bytes)

    await _insert_recording(db_path, recording_id)
    async with aiosqlite.connect(db_path) as db:
        for i in range(3):
            rel = f"frames/{recording_id}/f{i:03d}.jpg"
            await db.execute(
                "INSERT INTO observations(observation_id,recording_id,frame_id,"
                "video_timestamp_seconds,object_label,source_frame_path,detection_score,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), recording_id, f"f{i}", float(i * 5),
                 "fire", rel, 0.9, "2026-01-01T00:00:00Z"),
            )
        # Two overlapping segments both with fire keyword, both in first 30s bucket
        for seg_start, seg_end in [(0.0, 8.0), (5.0, 13.0)]:
            await db.execute(
                "INSERT INTO upload_understandings(segment_id,recording_id,segment_hash,"
                "start_seconds,end_seconds,unusual_observations_json,static_scene,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), recording_id, f"hash_{seg_start}",
                 seg_start, seg_end,
                 json.dumps(["fire visible on ceiling"]), 0, "2026-01-01T00:00:00Z"),
            )
        await db.commit()

    fire_response = {
        "hazards": [{
            "hazard_category": "fire_smoke",
            "severity": "high",
            "confidence": 0.85,
            "timestamp_seconds": 2.5,
            "description": "Fire on ceiling",
        }],
        "scene_clear": False,
    }
    client = MockMetaClient(scenario_map={f"guardian:{frame_hash}": fire_response})

    errors: list = []
    await run_guardian_detection(recording_id, db_path, storage, client, errors)
    alerts = await get_alerts_for_recording(db_path, recording_id)
    fire_alerts = [a for a in alerts if a["hazard_category"] == "fire_smoke"]
    # Adjacent/overlapping segments in same 30s bucket → exactly 1 alert after upsert dedup
    assert len(fire_alerts) == 1


@pytest.mark.asyncio
async def test_guardian_is_mocked_flag(db_path, storage):
    recording_id = str(uuid.uuid4())
    frame_bytes = b"mocked_frame"
    frame_hash = hashlib.sha256(frame_bytes).hexdigest()

    frames_dir = storage.data_dir / "frames" / recording_id
    frames_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / "f001.jpg").write_bytes(frame_bytes)

    await _insert_recording(db_path, recording_id)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO observations(observation_id,recording_id,frame_id,"
            "video_timestamp_seconds,object_label,source_frame_path,detection_score,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), recording_id, "f1", 1.0,
             "object", f"frames/{recording_id}/f001.jpg", 0.8, "2026-01-01T00:00:00Z"),
        )
        await db.execute(
            "INSERT INTO upload_understandings(segment_id,recording_id,segment_hash,"
            "start_seconds,end_seconds,unusual_observations_json,static_scene,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), recording_id, "h1", 0.0, 5.0,
             json.dumps(["weapon visible"]), 0, "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    weapon_response = {
        "hazards": [{
            "hazard_category": "weapon_visible",
            "severity": "critical",
            "confidence": 0.88,
            "timestamp_seconds": 1.0,
            "description": "Firearm visible in person's hand",
        }],
        "scene_clear": False,
    }
    client = MockMetaClient(scenario_map={f"guardian:{frame_hash}": weapon_response})
    errors: list = []
    await run_guardian_detection(recording_id, db_path, storage, client, errors)
    alerts = await get_alerts_for_recording(db_path, recording_id)
    assert len(alerts) >= 1
    # MockMetaClient → is_mocked should be True
    assert alerts[0]["is_mocked"] is True


# ── Review status updates ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_review_status(db_path):
    rid = str(uuid.uuid4())
    await _insert_recording(db_path, rid)
    await upsert_guardian_alert(db_path, rid, {
        "hazard_category": "fire_smoke",
        "severity": "high",
        "confidence": 0.8,
        "timestamp_seconds": 0.0,
        "time_range_start": 0.0,
        "time_range_end": 10.0,
        "description": "Fire",
        "evidence_frames": [],
        "supporting_segment_ids": [],
    })
    alerts = await get_alerts_for_recording(db_path, rid)
    alert_id = alerts[0]["alert_id"]

    result = await update_alert_review_status(db_path, rid, alert_id, "acknowledged")
    assert result is True

    updated = await get_alerts_for_recording(db_path, rid)
    assert updated[0]["review_status"] == "acknowledged"


@pytest.mark.asyncio
async def test_update_nonexistent_alert_returns_false(db_path):
    result = await update_alert_review_status(
        db_path, str(uuid.uuid4()), str(uuid.uuid4()), "dismissed"
    )
    assert result is False


# ── Guardian API ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_get_alerts_empty(client, tmp_data_dir):
    from app.database import create_tables
    from app.database import get_db
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    rid = str(uuid.uuid4())
    async with get_db(app_db) as db:
        await db.execute(
            "INSERT INTO recordings(recording_id,original_filename,upload_timestamp,"
            "video_path,processing_status,frame_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (rid, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "INDEXED", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    res = await client.get(f"/api/recordings/{rid}/guardian-alerts")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 0
    assert data["alerts"] == []


@pytest.mark.asyncio
async def test_api_get_recording_not_found(client, tmp_data_dir):
    from app.database import create_tables
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    fake_id = str(uuid.uuid4())
    res = await client.get(f"/api/recordings/{fake_id}/guardian-alerts")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_api_patch_acknowledge(client, tmp_data_dir):
    from app.database import create_tables, get_db
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    rid = str(uuid.uuid4())
    async with get_db(app_db) as db:
        await db.execute(
            "INSERT INTO recordings(recording_id,original_filename,upload_timestamp,"
            "video_path,processing_status,frame_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (rid, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "INDEXED", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    alert_dict = {
        "hazard_category": "fire_smoke",
        "severity": "high",
        "confidence": 0.8,
        "timestamp_seconds": 1.0,
        "time_range_start": 0.0,
        "time_range_end": 10.0,
        "description": "Test fire",
        "evidence_frames": [],
        "supporting_segment_ids": [],
    }
    await upsert_guardian_alert(app_db, rid, alert_dict)

    alerts_res = await client.get(f"/api/recordings/{rid}/guardian-alerts")
    alert_id = alerts_res.json()["alerts"][0]["alert_id"]

    patch_res = await client.patch(
        f"/api/recordings/{rid}/guardian-alerts/{alert_id}",
        json={"review_status": "acknowledged"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["review_status"] == "acknowledged"


@pytest.mark.asyncio
async def test_api_patch_invalid_status(client, tmp_data_dir):
    from app.database import create_tables
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    rid = str(uuid.uuid4())
    aid = str(uuid.uuid4())
    res = await client.patch(
        f"/api/recordings/{rid}/guardian-alerts/{aid}",
        json={"review_status": "purple"},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_api_global_alerts_endpoint(client, tmp_data_dir):
    from app.database import create_tables
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    res = await client.get("/api/guardian-alerts")
    assert res.status_code == 200
    data = res.json()
    assert "alerts" in data
    assert "total" in data


# ── Animal classification — ordinary dogs must NOT trigger hazard alerts ──────
#
# Requirements:
#   - Normal dog presence (walking, sitting, playing, barking) → no alert
#   - Dangerous behavior (attacking, biting, chasing) → dangerous_animal alert
#   - Inherently dangerous wild animals → always produce alerts
#   - Cats, birds, ordinary domestic animals → no alert without behavior context

def _make_entity(type_: str, description: str, first_seen: float = 0.0) -> dict:
    return {"type": type_, "description": description, "first_seen_seconds": first_seen}


def _run_classification(entities=None, observations=None):
    return _classify_hazards_from_understanding(
        entities=entities or [],
        actions=[],
        unusual_observations=observations or [],
        scene_context="",
        seg_start=0.0,
        seg_end=10.0,
        segment_id="test-seg",
    )


class TestDogClassification:
    """Ordinary dogs must never produce a dangerous_animal alert."""

    def test_dog_walking_no_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Brown dog walking across the courtyard")]
        )
        assert result == [], "Walking dog should produce no alert"

    def test_dog_sitting_no_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Small dog sitting near the entrance")]
        )
        assert result == [], "Sitting dog should produce no alert"

    def test_dog_playing_no_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Dog playing fetch in the yard")]
        )
        assert result == [], "Playing dog should produce no alert"

    def test_dog_barking_no_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Dog barking at the gate")]
        )
        assert result == [], "Barking dog (no person targeted) should produce no alert"

    def test_dog_passing_through_no_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Stray dog passing through the parking lot")]
        )
        assert result == [], "Dog passing through should produce no alert"

    def test_dog_entity_type_no_alert(self):
        result = _run_classification(
            entities=[_make_entity("dog", "Labrador retriever, friendly, on leash")]
        )
        assert result == [], "Dog entity type alone should produce no alert"

    def test_dog_observation_no_alert(self):
        result = _run_classification(
            observations=["A dog is visible near the building entrance"]
        )
        assert result == [], "Ordinary dog observation should produce no alert"

    def test_multiple_dogs_no_alert(self):
        entities = [
            _make_entity("animal", "Large dog running through the courtyard"),
            _make_entity("animal", "Small dog near the benches"),
        ]
        result = _run_classification(entities=entities)
        assert result == [], "Multiple ordinary dogs should produce no alert"


class TestDogDangerousAlert:
    """Dogs displaying confirmed dangerous behavior MUST produce an alert."""

    def test_dog_attacking_person_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Dog attacking person near exit")]
        )
        assert len(result) == 1
        assert result[0]["hazard_category"] == "dangerous_animal"

    def test_dog_biting_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Dog biting security guard's arm")]
        )
        assert len(result) == 1
        assert result[0]["hazard_category"] == "dangerous_animal"

    def test_dog_aggressively_chasing_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Dog aggressively chasing a child")]
        )
        assert len(result) == 1
        assert result[0]["hazard_category"] == "dangerous_animal"

    def test_dog_lunging_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Large dog lunging at pedestrian")]
        )
        assert len(result) == 1
        assert result[0]["hazard_category"] == "dangerous_animal"

    def test_dog_attack_observation_alert(self):
        result = _run_classification(
            observations=["Dog attack on pedestrian at front gate"]
        )
        categories = {r["hazard_category"] for r in result}
        assert "dangerous_animal" in categories, "Dog attack should produce dangerous_animal alert"

    def test_attacking_dog_observation_alert(self):
        result = _run_classification(
            observations=["Attacking dog near the entrance, person backing away"]
        )
        categories = {r["hazard_category"] for r in result}
        assert "dangerous_animal" in categories, "Attacking dog should produce dangerous_animal alert"

    def test_dog_biting_observation_alert(self):
        result = _run_classification(
            observations=["Dog biting a delivery worker's leg"]
        )
        categories = {r["hazard_category"] for r in result}
        assert "dangerous_animal" in categories, "Dog biting should produce dangerous_animal alert"


class TestOtherDomesticAnimals:
    """Cats, birds, and other common animals must not produce alerts without dangerous behavior."""

    def test_cat_walking_no_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Orange cat walking along the corridor")]
        )
        assert result == [], "Ordinary cat should produce no alert"

    def test_bird_flying_no_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Pigeon flying across the courtyard")]
        )
        assert result == [], "Ordinary bird should produce no alert"

    def test_cat_observation_no_alert(self):
        result = _run_classification(
            observations=["A cat is sitting on the window ledge"]
        )
        assert result == [], "Ordinary cat observation should produce no alert"


class TestWildDangerousAnimals:
    """Inherently dangerous wild animals always produce alerts, no behavior check needed."""

    def test_snake_always_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Snake on the floor near the door")]
        )
        assert len(result) == 1
        assert result[0]["hazard_category"] == "dangerous_animal"
        assert result[0]["severity"] == "critical"

    def test_cobra_always_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Cobra detected near the stairwell")]
        )
        assert len(result) == 1
        assert result[0]["hazard_category"] == "dangerous_animal"

    def test_tiger_always_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Tiger visible in the corridor")]
        )
        assert len(result) == 1
        assert result[0]["hazard_category"] == "dangerous_animal"
        assert result[0]["severity"] == "critical"

    def test_bear_always_alert(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Bear near the entrance")]
        )
        assert len(result) == 1
        assert result[0]["hazard_category"] == "dangerous_animal"


class TestAnimalDoesNotBlockOtherHazards:
    """Dog presence alongside a real hazard should not suppress that hazard."""

    def test_dog_plus_fire_only_fire_alert(self):
        result = _run_classification(
            entities=[
                _make_entity("animal", "Dog walking near the entrance"),
                _make_entity("object", "Fire visible on the left wall"),
            ]
        )
        categories = {r["hazard_category"] for r in result}
        assert "fire_smoke" in categories, "Fire must still be detected"
        assert "dangerous_animal" not in categories, "Ordinary dog must not add animal alert"

    def test_dog_plus_smoke_observation(self):
        result = _run_classification(
            entities=[_make_entity("animal", "Dog near lobby")],
            observations=["Smoke detected near the ceiling vents"],
        )
        categories = {r["hazard_category"] for r in result}
        assert "fire_smoke" in categories
        assert "dangerous_animal" not in categories
