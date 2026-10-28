"""Regression: a bird must never be presented as a Person for a human query.

Root causes covered:
1. `_infer_event_type` used substring matching, so the ants' "multiple
   individuals" labeled a bird event `person_detected`.
2. `_score_event` matched the generic token "detected" against the stored
   `person_detected` type text, so "any human detected" returned the bird
   with no human evidence. Conversely a lone "human" query could not find
   a genuine person event (no human<->person synonym coverage).
"""
import pytest
import pytest_asyncio

from app.event_extractor import _build_event, _infer_event_type
from app.investigate import investigate
from tests.fixtures import RECORDING_ID_1, insert_event, insert_recording

BIRD_ACTION = "standing on dark soil in center of frame facing right, stationary"
BIRD_OBJECTS = [
    "black small black bird (entirely dark plumage, brown eye, thin dark legs)",
    "dark brown group of ants (multiple individuals visible on head and back)",
]
BIRD_FACTS = [
    "Observed: black small black bird",
    "Observed: group of ants (multiple individuals visible on feathers)",
]


class TestPersonTypeInference:
    def test_bird_with_individuals_is_not_person(self):
        assert _infer_event_type(BIRD_OBJECTS, [BIRD_ACTION]) == "scene_change"

    def test_person_labels_still_detected(self):
        assert _infer_event_type(["red person"], []) == "person_detected"
        assert _infer_event_type(["woman walking"], []) == "person_detected"
        assert _infer_event_type(["human standing"], []) == "person_detected"

    def test_vehicle_still_detected(self):
        assert _infer_event_type(["red car"], []) == "vehicle_detected"

    def test_substring_traps_do_not_trigger_person(self):
        assert _infer_event_type(["mannequin in store window"], []) != "person_detected"
        assert _infer_event_type(["outdoor performance stage"], []) != "person_detected"

    def test_empty_labels_do_not_crash(self):
        assert _infer_event_type([], []) == "scene_change"


@pytest.mark.asyncio
class TestHumanQueryRelevance:
    async def _seed_bird(self, test_db, event_type="scene_change"):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description=BIRD_ACTION,
            object_descriptions=BIRD_OBJECTS,
            observed_facts=BIRD_FACTS,
            event_type=event_type,
            confidence=0.94,
        )
        await test_db.commit()

    async def _seed_person(self, test_db):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="red person detected at entrance",
            object_descriptions=["red person"],
            observed_facts=["Red figure at entrance"],
            event_type="person_detected",
            confidence=0.8,
        )
        await test_db.commit()

    async def test_bird_excluded_from_human_query(self, test_db, db_path):
        await self._seed_bird(test_db)
        result = await investigate("any human detected", db_path)
        assert result.events == []
        assert "no events matching" in result.narrative.lower()

    async def test_legacy_mislabeled_bird_excluded_from_human_query(
        self, test_db, db_path
    ):
        # Stored type says person, but no human evidence in the description.
        await self._seed_bird(test_db, event_type="person_detected")
        result = await investigate("any human detected", db_path)
        assert result.events == []

    async def test_person_returned_for_human_query(self, test_db, db_path):
        await self._seed_person(test_db)
        result = await investigate("any human detected", db_path)
        assert len(result.events) == 1
        assert "person" in result.events[0].action_description

    async def test_human_single_term_finds_person(self, test_db, db_path):
        await self._seed_person(test_db)
        result = await investigate("human", db_path)
        assert len(result.events) == 1

    async def test_vehicle_excluded_from_human_query(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="red car parked near entrance",
            object_descriptions=["red car"],
            event_type="vehicle_detected",
        )
        await test_db.commit()
        assert (await investigate("any human detected", db_path)).events == []
        found = await investigate("car", db_path)
        assert len(found.events) == 1

    async def test_unknown_object_excluded_from_human_query(
        self, test_db, db_path
    ):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="blurry shape in low light",
            object_descriptions=["blurry shape"],
            event_type="scene_change",
        )
        await test_db.commit()
        assert (await investigate("any human detected", db_path)).events == []

    async def test_contradictory_row_flagged_not_silenced(
        self, test_db, db_path
    ):
        await self._seed_bird(test_db, event_type="person_detected")
        result = await investigate("bird", db_path)
        assert len(result.events) == 1
        ev = result.events[0]
        # The contradiction is surfaced; the score itself is untouched.
        assert any("not supported by" in n for n in ev.uncertainty_notes)
        assert ev.confidence == 0.94


@pytest.mark.asyncio
class TestMultiAgentHumanQuery:
    async def test_mislabeled_bird_excluded(self, db_path):
        from app.agents.orchestrator import InvestigationOrchestrator
        from app.database import get_db
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1)
            await insert_event(
                db, RECORDING_ID_1,
                action_description=BIRD_ACTION,
                object_descriptions=BIRD_OBJECTS,
                observed_facts=BIRD_FACTS,
                event_type="person_detected",
            )
            await db.commit()
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=0.0)
        result = await orch.run(
            query="any human detected", recording_ids=[RECORDING_ID_1])
        assert result["final_report"]["key_findings"] == []
        assert "No evidence found" in result["final_report"]["narrative"]

    async def test_person_found(self, db_path):
        from app.agents.orchestrator import InvestigationOrchestrator
        from app.database import get_db
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1)
            await insert_event(
                db, RECORDING_ID_1,
                action_description="red person detected at entrance",
                object_descriptions=["red person"],
                event_type="person_detected",
            )
            await db.commit()
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=0.0)
        result = await orch.run(
            query="any human detected", recording_ids=[RECORDING_ID_1])
        assert len(result["final_report"]["key_findings"]) > 0


class TestConfidenceSemantics:
    def test_event_confidence_is_mean_detection_score(self):
        # Documents the actual meaning: average of the model's per-object
        # detection scores — NOT a calibrated person probability.
        def obs(ts, score):
            return {
                "observation_id": f"o{ts}", "object_label": "black bird",
                "object_attributes": None, "scene_description": "perched",
                "source_frame_path": f"frames/test/{ts}.jpg",
                "detection_score": score,
            }
        group = [obs(1.0, 0.96), obs(2.0, 0.92)]
        paths = {o["video_timestamp_seconds"] if "video_timestamp_seconds" in o
                 else float(o["observation_id"][1:]): o["source_frame_path"]
                 for o in group}
        ev = _build_event(
            group=[{**o, "video_timestamp_seconds": float(o["observation_id"][1:])}
                   for o in group],
            recording_id=RECORDING_ID_1,
            recording_filename="cam.mp4",
            recording_datetime=None,
            recording_timezone=None,
            camera_name=None,
            camera_location=None,
            all_frame_paths_by_ts=paths,
            sorted_timestamps=sorted(paths.keys()),
        )
        assert ev["event_type"] == "scene_change"
        assert ev["confidence"] == pytest.approx(0.94)
