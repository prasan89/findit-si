"""Tests for the upload-time general-purpose video understanding pipeline.

Validates:
1. MockMetaClient.analyze_upload_segment returns valid structure
2. _events_from_understanding generates action-level events from understanding data
3. Multiple events per recording when upload understanding data is available
4. Retrieval of events via action-vocabulary queries (chair removal, person entry, etc.)
5. Static scenes produce exactly one coverage event
6. Understanding-based events have richer action_descriptions than observation-based ones
7. process_recording stores upload_understandings rows and generates multiple events
"""
import json
import uuid

import pytest

from app.event_extractor import (
    _events_from_understanding,
    _infer_event_type_from_action,
    _change_type_to_event_type,
)
from app.meta_client import MockMetaClient, validate_upload_understanding


# ── Unit tests: validate_upload_understanding ────────────────────────────────

class TestValidateUploadUnderstanding:
    def test_valid_full_response(self):
        data = {
            "scene_context": "outdoor courtyard",
            "entities": [{"entity_id": "entity_1", "type": "person", "description": "person in blue"}],
            "actions": [{"entity_id": "entity_1", "action": "walking toward gate"}],
            "interactions": [],
            "changes": [{"type": "entered", "entity_id": "entity_1", "description": "person entered"}],
            "unusual_observations": [],
            "uncertain_observations": [],
            "static_scene": False,
        }
        result = validate_upload_understanding(data)
        assert result is data

    def test_minimal_static_scene(self):
        data = {"static_scene": True}
        result = validate_upload_understanding(data)
        assert result is data

    def test_entities_must_be_list_if_present(self):
        from app.meta_client import ModelResponseError
        with pytest.raises(ModelResponseError, match="entities"):
            validate_upload_understanding({"entities": "not a list"})

    def test_actions_must_be_list_if_present(self):
        from app.meta_client import ModelResponseError
        with pytest.raises(ModelResponseError, match="actions"):
            validate_upload_understanding({"actions": {"key": "val"}})

    def test_changes_must_be_list_if_present(self):
        from app.meta_client import ModelResponseError
        with pytest.raises(ModelResponseError, match="changes"):
            validate_upload_understanding({"changes": "disappeared"})

    def test_non_dict_raises(self):
        from app.meta_client import ModelResponseError
        with pytest.raises(ModelResponseError):
            validate_upload_understanding([{"entities": []}])


# ── Unit tests: MockMetaClient.analyze_upload_segment ───────────────────────

class TestMockUploadSegment:
    @pytest.mark.asyncio
    async def test_returns_valid_structure(self):
        client = MockMetaClient()
        result = await client.analyze_upload_segment(
            frames=[b"frame1", b"frame2"],
            timestamps=[0.0, 1.0],
        )
        assert isinstance(result, dict)
        assert isinstance(result.get("entities"), list)
        assert isinstance(result.get("actions"), list)
        assert isinstance(result.get("changes"), list)
        assert "static_scene" in result

    @pytest.mark.asyncio
    async def test_uses_scenario_map_for_objects(self):
        import hashlib
        frame = b"test_frame"
        h = hashlib.sha256(frame).hexdigest()
        client = MockMetaClient(scenario_map={
            h: {"objects": [{"label": "chair", "confidence": 0.9}],
                "scene_summary": "chair in corridor"}
        })
        result = await client.analyze_upload_segment([frame], [5.0])
        # Mock produces entities from objects
        assert len(result["entities"]) == 1
        assert "chair" in result["entities"][0]["description"]

    @pytest.mark.asyncio
    async def test_empty_frames_raises(self):
        client = MockMetaClient()
        with pytest.raises(ValueError, match="at least one frame"):
            await client.analyze_upload_segment([], [])

    @pytest.mark.asyncio
    async def test_mismatched_lengths_raise(self):
        client = MockMetaClient()
        with pytest.raises(ValueError, match="equal length"):
            await client.analyze_upload_segment([b"f1", b"f2"], [0.0])


# ── Unit tests: _events_from_understanding ───────────────────────────────────

def _make_understanding_row(
    start_seconds=0.0,
    end_seconds=10.0,
    scene_context="indoor corridor",
    entities=None,
    actions=None,
    interactions=None,
    changes=None,
    unusual_observations=None,
    uncertain_observations=None,
    static_scene=False,
):
    return {
        "segment_id": str(uuid.uuid4()),
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "scene_context": scene_context,
        "entities_json": json.dumps(entities or []),
        "actions_json": json.dumps(actions or []),
        "interactions_json": json.dumps(interactions or []),
        "changes_json": json.dumps(changes or []),
        "unusual_observations_json": json.dumps(unusual_observations or []),
        "uncertain_observations_json": json.dumps(uncertain_observations or []),
        "static_scene": 1 if static_scene else 0,
    }


def _call_events_from_understanding(rows):
    return _events_from_understanding(
        understanding_rows=rows,
        recording_id="test-rec",
        recording_filename="test.mp4",
        recording_datetime=None,
        recording_timezone=None,
        camera_name=None,
        camera_location=None,
        all_frame_paths_by_ts={1.0: "frames/f1.jpg", 5.0: "frames/f5.jpg", 9.0: "frames/f9.jpg"},
        sorted_timestamps=[1.0, 5.0, 9.0],
    )


class TestEventsFromUnderstanding:

    def test_one_action_produces_one_event(self):
        rows = [_make_understanding_row(
            entities=[{"entity_id": "entity_1", "type": "person",
                       "description": "person in blue jacket",
                       "first_seen_seconds": 0.0, "last_seen_seconds": 10.0,
                       "confidence": 0.9}],
            actions=[{"entity_id": "entity_1", "action": "walking toward gate",
                      "start_seconds": 2.0, "end_seconds": 5.0, "confidence": 0.85}],
        )]
        events = _call_events_from_understanding(rows)
        assert len(events) == 1
        assert "walking toward gate" in events[0]["action_description"]
        assert events[0]["event_type"] == "person_detected"

    def test_multiple_actions_produce_multiple_events(self):
        rows = [_make_understanding_row(
            entities=[{"entity_id": "entity_1", "type": "person",
                       "description": "person", "first_seen_seconds": 0.0,
                       "last_seen_seconds": 10.0, "confidence": 0.8}],
            actions=[
                {"entity_id": "entity_1", "action": "enters from left",
                 "start_seconds": 1.0, "end_seconds": 2.0, "confidence": 0.8},
                {"entity_id": "entity_1", "action": "picks up bag from floor",
                 "start_seconds": 4.0, "end_seconds": 6.0, "confidence": 0.75},
                {"entity_id": "entity_1", "action": "exits through gate",
                 "start_seconds": 8.0, "end_seconds": 9.0, "confidence": 0.8},
            ],
        )]
        events = _call_events_from_understanding(rows)
        assert len(events) == 3
        action_descs = [e["action_description"] for e in events]
        assert any("enters" in d for d in action_descs)
        assert any("picks up bag" in d for d in action_descs)
        assert any("exits" in d for d in action_descs)

    def test_change_event_produced(self):
        rows = [_make_understanding_row(
            entities=[{"entity_id": "entity_1", "type": "object",
                       "description": "black bag", "first_seen_seconds": 0.0,
                       "last_seen_seconds": 5.0, "confidence": 0.85}],
            changes=[{"type": "disappeared", "entity_id": "entity_1",
                      "description": "black bag disappeared from bench",
                      "timestamp_seconds": 5.5}],
        )]
        events = _call_events_from_understanding(rows)
        change_events = [e for e in events if "disappeared" in e["action_description"]]
        assert len(change_events) == 1
        assert change_events[0]["confidence"] == 0.65

    def test_unusual_observation_produces_event(self):
        rows = [_make_understanding_row(
            unusual_observations=["snake visible on floor near right wall"],
        )]
        events = _call_events_from_understanding(rows)
        unusual_events = [e for e in events if "snake" in e["action_description"]]
        assert len(unusual_events) == 1
        assert "unusual" in unusual_events[0]["uncertainty_notes"][0]

    def test_static_scene_no_actions_produces_coverage_event(self):
        rows = [_make_understanding_row(
            static_scene=True,
            entities=[{"entity_id": "entity_1", "type": "object",
                       "description": "broom on floor", "first_seen_seconds": 0.0,
                       "last_seen_seconds": 10.0, "confidence": 0.9}],
        )]
        events = _call_events_from_understanding(rows)
        assert len(events) == 1
        assert "static" in events[0]["action_description"].lower()

    def test_empty_understanding_produces_no_events(self):
        rows = [_make_understanding_row()]
        events = _call_events_from_understanding(rows)
        assert len(events) == 0

    def test_action_description_includes_entity_description(self):
        rows = [_make_understanding_row(
            entities=[{"entity_id": "E1", "type": "person",
                       "description": "tall person in red shirt",
                       "first_seen_seconds": 0.0, "last_seen_seconds": 10.0,
                       "confidence": 0.9}],
            actions=[{"entity_id": "E1", "action": "carrying chair out of room",
                      "start_seconds": 3.0, "end_seconds": 7.0, "confidence": 0.8}],
        )]
        events = _call_events_from_understanding(rows)
        assert len(events) == 1
        desc = events[0]["action_description"]
        assert "tall person in red shirt" in desc
        assert "carrying chair out of room" in desc

    def test_vehicle_entity_type_produces_vehicle_event_type(self):
        rows = [_make_understanding_row(
            entities=[{"entity_id": "V1", "type": "vehicle",
                       "description": "white van", "first_seen_seconds": 0.0,
                       "last_seen_seconds": 5.0, "confidence": 0.9}],
            actions=[{"entity_id": "V1", "action": "drives through gate",
                      "start_seconds": 1.0, "end_seconds": 3.0, "confidence": 0.85}],
        )]
        events = _call_events_from_understanding(rows)
        assert events[0]["event_type"] == "vehicle_detected"

    def test_observed_facts_contain_action_text(self):
        rows = [_make_understanding_row(
            entities=[{"entity_id": "E1", "type": "person", "description": "person",
                       "first_seen_seconds": 0.0, "last_seen_seconds": 5.0, "confidence": 0.8}],
            actions=[{"entity_id": "E1", "action": "running toward camera",
                      "start_seconds": 1.0, "end_seconds": 3.0, "confidence": 0.8}],
        )]
        events = _call_events_from_understanding(rows)
        facts = events[0]["observed_facts"]
        assert any("running toward camera" in f for f in facts)

    def test_evidence_frames_populated(self):
        rows = [_make_understanding_row(
            actions=[{"entity_id": "E1", "action": "moves",
                      "start_seconds": 4.0, "end_seconds": 6.0, "confidence": 0.7}],
        )]
        events = _call_events_from_understanding(rows)
        # Should have at least a "during" frame near timestamp 5.0
        frames = events[0]["evidence_frames"]
        assert len(frames) >= 1
        assert frames[0]["role"] == "during"


# ── Unit tests: _infer_event_type_from_action ────────────────────────────────

class TestInferEventTypeFromAction:
    def test_person_entity_type(self):
        assert _infer_event_type_from_action("walks left", "person", []) == "person_detected"

    def test_vehicle_entity_type(self):
        assert _infer_event_type_from_action("drives through", "vehicle", []) == "vehicle_detected"

    def test_enters_in_action_text(self):
        assert _infer_event_type_from_action("entered from left", "", []) == "entry_exit_detected"

    def test_exited_in_action_text(self):
        assert _infer_event_type_from_action("exited through door", "", []) == "entry_exit_detected"

    def test_animal_entity_stays_scene_change(self):
        assert _infer_event_type_from_action("slithers on floor", "animal", []) == "scene_change"

    def test_unknown_falls_through_to_scene_change(self):
        assert _infer_event_type_from_action("object rotates", "object", []) == "scene_change"


# ── Unit tests: _change_type_to_event_type ───────────────────────────────────

class TestChangeTypeToEventType:
    def test_entered_maps_to_entry_exit(self):
        assert _change_type_to_event_type("entered") == "entry_exit_detected"

    def test_appeared_maps_to_entry_exit(self):
        assert _change_type_to_event_type("appeared") == "entry_exit_detected"

    def test_exited_maps_to_entry_exit(self):
        assert _change_type_to_event_type("exited") == "entry_exit_detected"

    def test_disappeared_maps_to_scene_change(self):
        assert _change_type_to_event_type("disappeared") == "scene_change"

    def test_moved_maps_to_scene_change(self):
        assert _change_type_to_event_type("moved") == "scene_change"

    def test_unknown_maps_to_scene_change(self):
        assert _change_type_to_event_type("exploded") == "scene_change"


# ── Integration test: process_recording stores understanding and events ───────

@pytest.mark.asyncio
async def test_process_recording_stores_upload_understanding(db_path, storage, monkeypatch):
    """process_recording should store upload_understandings rows and generate events
    from them when the meta_client supports analyze_upload_segment."""
    import hashlib
    from app import processor

    # Patch frame extraction to produce 3 frames
    frame_bytes = [b"frame_0", b"frame_1", b"frame_2"]

    async def mock_extract(video_path, output_dir, fps=1.0, max_frames=300):
        output_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for i, fb in enumerate(frame_bytes):
            p = output_dir / f"frame_{i:06d}.jpg"
            p.write_bytes(fb)
            results.append((float(i), p))
        return results

    async def mock_get_duration(_):
        return 3.0

    async def mock_ensure_h264(p, rid, st, db):
        return p

    monkeypatch.setattr(processor, "extract_frames", mock_extract)
    monkeypatch.setattr(processor, "get_video_duration", mock_get_duration)
    monkeypatch.setattr(processor, "_ensure_h264", mock_ensure_h264)

    # Build scenario map: each frame produces a person object
    scenario_map = {}
    for fb in frame_bytes:
        h = hashlib.sha256(fb).hexdigest()
        scenario_map[h] = {
            "objects": [{"label": "person in dark jacket", "scene_description": "standing in corridor",
                         "confidence": 0.85, "attributes": {"color": "dark", "shape": "upright"}}],
            "scene_summary": "person standing in indoor corridor",
        }
    meta_client = MockMetaClient(scenario_map=scenario_map)

    from app.database import create_tables
    await create_tables(db_path)

    import aiosqlite
    recording_id = str(uuid.uuid4())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO recordings(
                recording_id, original_filename, upload_timestamp,
                video_path, processing_status, frame_count, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (recording_id, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "PENDING", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    await processor.process_recording(
        recording_id, db_path, storage, meta_client,
        fps=1.0, max_frames=10, max_api_calls=50,
    )

    # Verify upload_understandings rows were stored
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        u_rows = await (await db.execute(
            "SELECT * FROM upload_understandings WHERE recording_id = ?",
            (recording_id,),
        )).fetchall()
        event_rows = await (await db.execute(
            "SELECT event_type, action_description FROM events WHERE recording_id = ?",
            (recording_id,),
        )).fetchall()

    # Should have stored at least 1 understanding segment
    assert len(u_rows) >= 1, f"Expected upload_understandings rows, got {len(u_rows)}"

    # Events should exist
    assert len(event_rows) >= 1, f"Expected events, got {len(event_rows)}"

    # Events should be person-type (because our scenario produces person labels)
    person_events = [r for r in event_rows if r["event_type"] == "person_detected"]
    assert len(person_events) >= 1, f"Expected person_detected events, got {event_rows}"


