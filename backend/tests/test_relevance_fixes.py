"""Regression: relevance filtering, entity tracking, incident prioritization.

Covers the UI-fix batch: word-boundary matching (no substring false
hits), animal/object subject gates, label normalization, entity tracks
across adjacent frames, timestamped action sequences, incident-term
temporal merge with primary-finding flagging, and explicit agent skip
reasons. All fixtures are synthetic; no video files are referenced.
"""
import json

import pytest
import pytest_asyncio

from app.agents.base import AgentStatus, InvestigationContext
from app.event_extractor import (
    _build_event,
    build_action_sequence,
    extract_events_for_recording,
    normalize_description,
    track_entities,
)
from app.investigate import _score_event, investigate
from tests.fixtures import (
    RECORDING_ID_1,
    insert_event,
    insert_observation,
    insert_recording,
)


def _obs(ts, label, scene="scene", score=0.8, attrs=None):
    return {
        "observation_id": f"o-{ts}-{label[:8]}",
        "object_label": label,
        "object_attributes": json.dumps(attrs) if attrs else None,
        "scene_description": scene,
        "source_frame_path": f"frames/test/{ts}.jpg",
        "detection_score": score,
        "video_timestamp_seconds": float(ts),
    }


def _paths(group):
    return {o["video_timestamp_seconds"]: o["source_frame_path"]
            for o in group}


class TestLabelNormalization:
    def test_color_in_label_not_duplicated(self):
        assert normalize_description(
            "black motorcycle", {"color": "black"}) == "black motorcycle"

    def test_new_color_prepended(self):
        assert normalize_description(
            "motorcycle", {"color": "black"}) == "black motorcycle"

    def test_details_appended_once(self):
        assert normalize_description(
            "box", {"distinctive_details": "held at chest"}) == \
            "box (held at chest)"
        assert normalize_description(
            "box (held at chest)",
            {"distinctive_details": "held at chest"}) == \
            "box (held at chest)"

    def test_model_stutter_collapsed(self):
        assert normalize_description(
            "black black motorcycle", {}) == "black motorcycle"

    def test_build_event_has_no_doubled_words(self):
        group = [
            _obs(0.0, "black motorcycle",
                 scene="street", attrs={"color": "black"}),
            _obs(1.0, "black motorcycle",
                 scene="street", attrs={"color": "black"}),
        ]
        paths = _paths(group)
        ev = _build_event(
            group=group, recording_id=RECORDING_ID_1,
            recording_filename="cam.mp4", recording_datetime=None,
            recording_timezone=None, camera_name=None,
            camera_location=None, all_frame_paths_by_ts=paths,
            sorted_timestamps=sorted(paths),
        )
        assert ev["object_descriptions"] == ["black motorcycle"]
        assert "black black" not in " ".join(ev["observed_facts"])


class TestEntityTracking:
    def test_rephrased_person_is_one_entity(self):
        group = [
            _obs(0.0, "person in dark long garment"),
            _obs(1.0, "person in long dark garment"),
            _obs(2.0, "person in dark long dress"),
        ]
        entities = track_entities(group)
        assert len(entities) == 1
        ent = entities[0]
        assert ent["entity_id"] == "E1"
        assert ent["head"] == "person"
        assert ent["first_seen_seconds"] == 0.0
        assert ent["last_seen_seconds"] == 2.0
        assert ent["frame_count"] == 3
        assert ent["observation_count"] == 3
        assert ent["representative_frame"] == "frames/test/0.0.jpg"

    def test_different_people_stay_separate(self):
        group = [
            _obs(0.0, "person in pink sari"),
            _obs(1.0, "person in dark clothing"),
        ]
        entities = track_entities(group)
        assert [e["entity_id"] for e in entities] == ["E1", "E2"]

    def test_different_heads_stay_separate(self):
        group = [
            _obs(0.0, "brown dog lying down"),
            _obs(0.0, "black cat sitting"),
        ]
        entities = track_entities(group)
        assert sorted(e["head"] for e in entities) == ["cat", "dog"]

    def test_frame_gap_breaks_track(self):
        group = [
            _obs(0.0, "person in pink sari"),
            _obs(1.0, "black motorcycle parked"),
            _obs(2.0, "black motorcycle parked"),
            _obs(3.0, "black motorcycle parked"),
            _obs(9.0, "person in pink sari"),
        ]
        people = [e for e in track_entities(group) if e["head"] == "person"]
        assert len(people) == 2

    def test_man_person_synonym_links(self):
        group = [
            _obs(0.0, "man in green shirt"),
            _obs(1.0, "person in green shirt"),
        ]
        entities = track_entities(group)
        assert len(entities) == 1
        assert entities[0]["head"] == "person"


class TestActionSequence:
    def test_runs_compress_into_steps(self):
        group = [
            _obs(0.0, "person", scene="standing by gate"),
            _obs(1.0, "person", scene="standing by gate"),
            _obs(2.0, "person", scene="walking away quickly"),
        ]
        steps, truncated = build_action_sequence(group)
        assert truncated is False
        assert [(s["start_seconds"], s["end_seconds"]) for s in steps] == [
            (0.0, 1.0), (2.0, 2.0)]
        assert steps[0]["description"] == "standing by gate"
        assert steps[0]["frame_path"] == "frames/test/0.0.jpg"

    def test_empty_scenes_skipped(self):
        group = [_obs(0.0, "person", scene=""),
                 _obs(1.0, "person", scene="visible")]
        steps, _ = build_action_sequence(group)
        assert [s["description"] for s in steps] == ["visible"]

    def test_long_sequences_truncate(self):
        group = [_obs(float(i), "person", scene=f"scene {i}")
                 for i in range(25)]
        steps, truncated = build_action_sequence(group)
        assert truncated is True
        assert len(steps) == 20


def _ev_dict(action, objects, event_type="scene_change", conf=0.8,
             facts=None, scene=None):
    return {
        "action_description": action,
        "scene_description": scene,
        "event_type": event_type,
        "object_descriptions": objects,
        "observed_facts": facts if facts is not None else
        [f"Observed: {o}" for o in objects],
        "confidence": conf,
    }


class TestWordBoundaryRelevance:
    def test_car_does_not_match_cardboard(self):
        ev = _ev_dict("cardboard cart in storage", ["cardboard cart"])
        assert _score_event(["car"], ev) == 0.0

    def test_dog_does_not_match_hotdog(self):
        ev = _ev_dict("hotdog stand on sidewalk", ["hotdog stand"])
        assert _score_event(["dog"], ev) == 0.0

    def test_dog_matches_dog_word(self):
        ev = _ev_dict("brown dog running", ["brown dog"])
        assert _score_event(["dog"], ev) > 0.5

    def test_cat_query_excludes_dog_only_event(self):
        ev = _ev_dict("brown dog running", ["brown dog"])
        assert _score_event(["cat"], ev) == 0.0

    def test_animal_hypernym_matches_dog(self):
        ev = _ev_dict("brown dog running", ["brown dog"])
        assert _score_event(["animal"], ev) > 0.5

    def test_box_query_matches_box_event(self):
        ev = _ev_dict("person carrying box", ["cardboard box"])
        assert _score_event(["box"], ev) > 0.5

    def test_box_query_excludes_bird_event(self):
        ev = _ev_dict("black bird perched", ["black bird"],
                      event_type="bird_detected")
        assert _score_event(["box"], ev) == 0.0

    def test_bag_matches_backpack_synonym(self):
        ev = _ev_dict("backpack left behind", ["red backpack"])
        assert _score_event(["bag"], ev) > 0.5


@pytest.mark.asyncio
class TestDogPrimaryFinding:
    async def _seed_mixed(self, test_db):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev-blob",
            action_description="person carrying box walking past shops",
            object_descriptions=["person carrying box",
                                 "brown dog lying nearby"],
            observed_facts=["Observed: person carrying box",
                            "Observed: brown dog lying nearby"],
            event_type="person_detected",
            confidence=0.8,
        )
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev-dog",
            timestamp_seconds=20.0,
            action_description="brown dog running across lawn",
            object_descriptions=["brown dog"],
            observed_facts=["Observed: brown dog running"],
            event_type="scene_change",
            confidence=0.8,
        )
        await test_db.commit()

    async def test_dog_headline_beats_buried_mention(
        self, test_db, db_path
    ):
        await self._seed_mixed(test_db)
        result = await investigate("any dog detected?", db_path)
        by_id = {e.event_id: e for e in result.events}
        assert set(by_id) == {"ev-blob", "ev-dog"}
        assert by_id["ev-dog"].relevance_score > \
            by_id["ev-blob"].relevance_score
        assert by_id["ev-dog"].is_primary_finding is True
        assert by_id["ev-blob"].is_primary_finding is False

    async def test_buried_mention_headlines_dog_evidence(
        self, test_db, db_path
    ):
        await self._seed_mixed(test_db)
        result = await investigate("any dog detected?", db_path)
        blob = next(e for e in result.events
                    if e.event_id == "ev-blob")
        assert blob.primary_match is not None
        assert "dog" in blob.primary_match.lower()
        assert "person carrying box" not in blob.primary_match.lower()
        assert any("dog" in x.lower() for x in blob.matched_excerpts)

    async def test_summary_leads_with_primary(self, test_db, db_path):
        await self._seed_mixed(test_db)
        result = await investigate("any dog detected?", db_path)
        assert result.summary.startswith("Best match:")
        assert "dog" in result.summary.lower()


async def _insert_temporal(db, recording_id, event_id, category,
                           start, end, confidence=0.7,
                           action_sequence=None, facts=None):
    import uuid
    from datetime import datetime, timezone
    evidence = {
        "candidate_types": ["rapid_movement"],
        "stages": ["approach", "interaction", "departure"],
        "action_sequence": action_sequence or [],
        "confirmed": [],
        "uncertain": ["low light"],
        "observed_facts": facts or [],
        "evidence_frames": [{
            "frame_path": f"frames/{recording_id}/mid.jpg",
            "timestamp_seconds": (start + end) / 2.0,
            "role": "during",
        }],
        "tracks": [],
    }
    await db.execute(
        """INSERT OR REPLACE INTO temporal_events(
            event_id, recording_id, category, start_seconds, end_seconds,
            timestamp_seconds, timestamp_source, confidence,
            single_frame_candidate, evidence_json, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (event_id or str(uuid.uuid4()), recording_id, category,
         start, end, (start + end) / 2.0, "coarse_grid", confidence, 0,
         json.dumps(evidence),
         datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()


@pytest.mark.asyncio
class TestIncidentPrioritization:
    async def _seed_routine_plus_theft(self, test_db):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev-routine",
            action_description="quiet street with parked cars",
            object_descriptions=["parked cars"],
            event_type="scene_change",
            confidence=0.9,
        )
        await _insert_temporal(
            test_db, RECORDING_ID_1, "te-theft", "possible-theft",
            4.0, 7.0, confidence=0.7,
            action_sequence=[
                "motorcyclist approaches pedestrian",
                "rider grabs toward pedestrian",
                "motorcycle departs at speed",
            ],
            facts=["Observed sequence span 4.0s–7.0s",
                   "Candidate sudden_approach at 4.0s–4.0s "
                   "(confidence 0.80)"],
        )

    async def test_snatch_query_surfaces_theft_candidate(
        self, test_db, db_path
    ):
        await self._seed_routine_plus_theft(test_db)
        result = await investigate("any chain snatch?", db_path)
        assert [e.event_id for e in result.events] == ["te-theft"]
        theft = result.events[0]
        assert theft.event_type == "possible-theft"
        assert theft.is_primary_finding is True
        assert theft.primary_match == \
            "motorcyclist approaches pedestrian"
        assert [s["description"] for s in theft.action_sequence] == [
            "motorcyclist approaches pedestrian",
            "rider grabs toward pedestrian",
            "motorcycle departs at speed",
        ]
        assert theft.start_seconds == 4.0
        assert theft.end_seconds == 7.0
        assert any("observation-only candidate" in n
                   for n in theft.uncertainty_notes)
        assert theft.primary_match is not None

    async def test_non_incident_query_ignores_theft(self, test_db, db_path):
        await self._seed_routine_plus_theft(test_db)
        result = await investigate("quiet street", db_path)
        assert [e.event_id for e in result.events] == ["ev-routine"]

    async def test_snatch_query_surfaces_assault_candidate(
        self, test_db, db_path
    ):
        # A violent grab reads as contact stages (possible-assault), not an
        # observed object transfer — theft vocabulary surfaces both.
        await insert_recording(test_db, RECORDING_ID_1)
        await _insert_temporal(
            test_db, RECORDING_ID_1, "te-assault", "possible-assault",
            8.0, 10.0, confidence=0.6,
            action_sequence=["rider reaches toward pedestrian"],
            facts=["Observed sequence span 8.0s–10.0s"],
        )
        result = await investigate("any chain snatch?", db_path)
        assert [e.event_id for e in result.events] == ["te-assault"]
        assert result.events[0].event_type == "possible-assault"

    async def test_fall_query_does_not_match_theft(
        self, test_db, db_path
    ):
        await self._seed_routine_plus_theft(test_db)
        result = await investigate("person fell down", db_path)
        assert result.events == []

    async def test_incident_respects_time_filter(
        self, test_db, db_path
    ):
        await self._seed_routine_plus_theft(test_db)
        result = await investigate(
            "any chain snatch?", db_path, time_start_seconds=100.0)
        assert result.events == []


@pytest.mark.asyncio
class TestCacheInvalidationBump:
    async def _updated_at(self, test_db):
        row = await (await test_db.execute(
            "SELECT updated_at FROM recordings WHERE recording_id = ?",
            (RECORDING_ID_1,))).fetchone()
        return row[0]

    async def test_extraction_bumps_recording(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_observation(
            test_db, RECORDING_ID_1, "person",
            video_timestamp_seconds=1.0, scene_description="standing")
        await insert_observation(
            test_db, RECORDING_ID_1, "person",
            video_timestamp_seconds=2.0, scene_description="standing")
        before = await self._updated_at(test_db)
        assert await extract_events_for_recording(
            RECORDING_ID_1, db_path) == 1
        assert await self._updated_at(test_db) != before

    async def test_temporal_run_bumps_recording(
        self, test_db, db_path, tmp_path
    ):
        import types
        from app.storage import LocalStorageBackend
        from app.temporal_pipeline import run_temporal_detection
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_observation(
            test_db, RECORDING_ID_1, "person",
            video_timestamp_seconds=1.0, scene_description="standing")
        before = await self._updated_at(test_db)
        storage = LocalStorageBackend(tmp_path)
        client = types.SimpleNamespace(model_version="mock-test")
        await run_temporal_detection(
            recording_id=RECORDING_ID_1, db_path=db_path,
            storage=storage, meta_client=client, reason="test")
        assert await self._updated_at(test_db) != before


@pytest.mark.asyncio
class TestAgentSkipReasons:
    def _ctx(self, db_path, recording_ids=None, budget=2.0):
        return InvestigationContext(
            query="q", db_path=db_path,
            recording_ids=list(recording_ids or []),
            budget_usd=budget, max_llm_calls=8,
        )

    async def test_cross_camera_skip_has_reason(self, db_path):
        from app.agents import cross_camera_correlation
        result = await cross_camera_correlation.run(
            self._ctx(db_path, [RECORDING_ID_1]))
        assert result.status == AgentStatus.SKIPPED
        assert result.error
        assert "2 recordings" in result.error

    async def test_empty_events_skips_have_reasons(self, db_path):
        from app.agents import (
            contradiction_hunter, evidence_auditor,
            object_action_analyst, temporal_reasoning,
            video_intelligence,
        )
        for module in (video_intelligence, object_action_analyst,
                       temporal_reasoning, contradiction_hunter,
                       evidence_auditor):
            result = await module.run(self._ctx(db_path))
            assert result.status == AgentStatus.SKIPPED, module.__name__
            assert result.error, module.__name__

    async def test_orchestrator_marks_every_row(self, test_db, db_path):
        from app.agents.orchestrator import InvestigationOrchestrator
        await insert_recording(test_db, RECORDING_ID_1)
        await test_db.commit()
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=0.0)
        result = await orch.run(query="nothing matches this",
                                recording_ids=[RECORDING_ID_1])
        for agent in result["agents"]:
            assert agent["status"] in ("completed", "skipped", "failed",
                                       "timed_out", "mocked"), agent
            if agent["status"] != "completed":
                assert agent["error"], agent["agent_name"]
        by_name = {a["agent_name"]: a for a in result["agents"]}
        assert by_name["ReportSynthesisAgent"]["status"] == "completed"
        assert result["final_report"] is not None
