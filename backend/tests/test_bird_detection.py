"""Paired regression: bird queries must find birds, human queries must not.

Companion to test_human_query_relevance.py. After the false-person fix,
`any birds detected?` scored 0.0 against the bird event: plural "birds"
never substring-matches singular "bird", and the (correct) scene_change
type no longer donates a "detected" hit. These tests pin the paired matrix:
bird queries find birds, human queries find people, never crossed.
"""
import pytest
import pytest_asyncio

from app.event_extractor import _infer_event_type
from app.investigate import investigate
from tests.fixtures import RECORDING_ID_1, insert_event, insert_recording

BIRD_ACTION = "standing on dark soil in center of frame facing right, stationary"
BIRD_OBJECTS = ["black small black bird (entirely dark plumage)", "dark brown group of ants"]
BIRD_FRAMES = [
    {"frame_path": "frames/r1/before.jpg", "timestamp_seconds": 3.0, "role": "before"},
    {"frame_path": "frames/r1/during.jpg", "timestamp_seconds": 8.0, "role": "during"},
    {"frame_path": "frames/r1/after.jpg", "timestamp_seconds": 13.0, "role": "after"},
]
PERSON_ACTION = "red person detected at entrance"
PERSON_OBJECTS = ["red person"]


class TestBirdTypeInference:
    def test_bird_labels_classified_as_bird(self):
        assert _infer_event_type(BIRD_OBJECTS, [BIRD_ACTION]) == "bird_detected"

    def test_plural_bird_label_classified_as_bird(self):
        assert _infer_event_type(["a flock of birds"], []) == "bird_detected"

    def test_person_takes_priority_over_bird(self):
        assert _infer_event_type(["person holding a bird"], []) == "person_detected"

    def test_unrelated_labels_stay_unclaimed(self):
        assert _infer_event_type(["blurry shape"], []) == "scene_change"


@pytest.mark.asyncio
class TestPairedBirdPersonMatrix:
    async def _seed_both(self, test_db):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev-bird",
            timestamp_seconds=8.0,
            action_description=BIRD_ACTION,
            object_descriptions=BIRD_OBJECTS,
            observed_facts=["Observed: black small black bird"],
            evidence_frames=BIRD_FRAMES,
            event_type="bird_detected",
            confidence=0.94,
        )
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev-person",
            timestamp_seconds=20.0,
            action_description=PERSON_ACTION,
            object_descriptions=PERSON_OBJECTS,
            observed_facts=["Red figure at entrance"],
            event_type="person_detected",
            confidence=0.8,
        )
        await test_db.commit()

    async def test_bird_query_returns_bird_with_evidence(self, test_db, db_path):
        await self._seed_both(test_db)
        result = await investigate("any birds detected?", db_path)
        assert [e.event_id for e in result.events] == ["ev-bird"]
        ev = result.events[0]
        assert ev.event_type == "bird_detected"
        assert ev.recording_id == RECORDING_ID_1
        assert ev.timestamp_seconds == 8.0
        assert {f.role for f in ev.evidence_frames} == {"before", "during", "after"}

    async def test_human_query_returns_person_not_bird(self, test_db, db_path):
        await self._seed_both(test_db)
        result = await investigate("any human detected", db_path)
        assert [e.event_id for e in result.events] == ["ev-person"]

    @pytest.mark.parametrize("query", [
        "bird", "birds", "any birds detected?", "is there a bird?",
    ])
    async def test_bird_query_language(self, test_db, db_path, query):
        await self._seed_both(test_db)
        result = await investigate(query, db_path)
        assert [e.event_id for e in result.events] == ["ev-bird"]

    @pytest.mark.parametrize("query", [
        "any human detected", "people", "person",
    ])
    async def test_human_query_language(self, test_db, db_path, query):
        await self._seed_both(test_db)
        result = await investigate(query, db_path)
        assert [e.event_id for e in result.events] == ["ev-person"]

    async def test_animal_does_not_match_people(self, test_db, db_path):
        # Broad words are not auto-synonyms: "animal" matches neither class.
        await self._seed_both(test_db)
        result = await investigate("animal", db_path)
        assert result.events == []


@pytest.mark.asyncio
class TestUnknownAndContradictoryBird:
    async def test_unknown_excluded_from_bird_query(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="blurry shape in low light",
            object_descriptions=["blurry shape"],
            event_type="scene_change",
        )
        await test_db.commit()
        assert (await investigate("any birds detected?", db_path)).events == []

    async def test_contradictory_bird_row_flagged(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description=PERSON_ACTION,
            object_descriptions=PERSON_OBJECTS,
            event_type="bird_detected",
            confidence=0.8,
        )
        await test_db.commit()
        result = await investigate("person", db_path)
        assert len(result.events) == 1
        ev = result.events[0]
        assert any("not supported by" in n for n in ev.uncertainty_notes)
        assert ev.confidence == 0.8
