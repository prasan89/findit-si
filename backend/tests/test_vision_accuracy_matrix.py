"""Rule 5 matrix: 4 ground-truth event kinds x subject queries + edge rows.

Bird/dog/person/unknown/contradictory cells are pinned in
test_human_query_relevance.py, test_bird_detection.py, and
test_dog_query_relevance.py; this file adds the vehicle column, the empty
scene, and the absent-object row so the full matrix is covered in one place.
"""
import pytest
import pytest_asyncio

from app.investigate import investigate
from tests.fixtures import RECORDING_ID_1, insert_event, insert_recording


@pytest.mark.asyncio
class TestVisionAccuracyMatrix:
    async def _seed_all(self, test_db):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1, event_id="ev-bird",
            action_description="black bird perched on soil",
            object_descriptions=["black bird"],
            event_type="bird_detected",
        )
        await insert_event(
            test_db, RECORDING_ID_1, event_id="ev-dog",
            action_description="brown dog running across lawn",
            object_descriptions=["brown dog"],
            event_type="scene_change",
        )
        await insert_event(
            test_db, RECORDING_ID_1, event_id="ev-person",
            action_description="red person detected at entrance",
            object_descriptions=["red person"],
            event_type="person_detected",
        )
        await insert_event(
            test_db, RECORDING_ID_1, event_id="ev-vehicle",
            action_description="red car parked near entrance",
            object_descriptions=["red car"],
            event_type="vehicle_detected",
        )
        await test_db.commit()

    @pytest.mark.parametrize("query,expected", [
        ("any birds detected?", ["ev-bird"]),
        ("any dog detected?", ["ev-dog"]),
        ("any human detected", ["ev-person"]),
        ("any vehicle detected", ["ev-vehicle"]),
        ("vehicle", ["ev-vehicle"]),
        ("car", ["ev-vehicle"]),
    ])
    async def test_matrix_cell(self, test_db, db_path, query, expected):
        await self._seed_all(test_db)
        result = await investigate(query, db_path)
        assert [e.event_id for e in result.events] == expected

    async def test_vehicle_not_matched_by_other_subjects(
        self, test_db, db_path
    ):
        await self._seed_all(test_db)
        for query in ("any birds detected?", "any dog detected?",
                      "any human detected"):
            result = await investigate(query, db_path)
            assert "ev-vehicle" not in [e.event_id for e in result.events]

    async def test_absent_object_returns_nothing(self, test_db, db_path):
        await self._seed_all(test_db)
        result = await investigate("zebra", db_path)
        assert result.events == []
        assert "no events matching" in result.narrative.lower()

    async def test_empty_scene_returns_nothing(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await test_db.commit()
        for query in ("any birds detected?", "any human detected"):
            result = await investigate(query, db_path)
            assert result.events == []

    async def test_event_identity_preserved(self, test_db, db_path):
        await self._seed_all(test_db)
        result = await investigate("any vehicle detected", db_path)
        ev = result.events[0]
        assert ev.event_id == "ev-vehicle"
        assert ev.recording_id == RECORDING_ID_1
        assert ev.timestamp_seconds == 10.0
        assert ev.event_type == "vehicle_detected"
