"""Regression: generic token 'detected' must not match every *_detected event.

Incident: `any dog detected?` returned both bird events (score 0.63) because
"detected" substring-matched the stored "bird detected" type text, with the
"dog" token matching nothing. Same defect class previously fired through
"person detected". Fix: generic detection words carry no subject signal and
are stopwords; subject evidence must come from the description.
"""
import pytest
import pytest_asyncio

from app.event_extractor import _infer_event_type
from app.investigate import _tokenize, investigate
from tests.fixtures import RECORDING_ID_1, insert_event, insert_recording


@pytest.mark.asyncio
class TestDogQueryRelevance:
    async def _seed_bird_and_person(self, test_db):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev-bird",
            action_description="black bird perched on soil",
            object_descriptions=["black bird"],
            event_type="bird_detected",
        )
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev-person",
            action_description="red person detected at entrance",
            object_descriptions=["red person"],
            event_type="person_detected",
        )
        await test_db.commit()

    async def test_dog_query_matches_neither_bird_nor_person(
        self, test_db, db_path
    ):
        await self._seed_bird_and_person(test_db)
        result = await investigate("any dog detected?", db_path)
        assert result.events == []
        assert "no events matching" in result.narrative.lower()

    async def test_genuine_dog_event_found_by_dog_query(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev-dog",
            action_description="brown dog running across lawn",
            object_descriptions=["brown dog"],
            event_type="scene_change",
        )
        await test_db.commit()
        result = await investigate("any dog detected?", db_path)
        assert [e.event_id for e in result.events] == ["ev-dog"]

    async def test_bird_query_unaffected_by_stopwords(self, test_db, db_path):
        await self._seed_bird_and_person(test_db)
        result = await investigate("any birds detected?", db_path)
        assert [e.event_id for e in result.events] == ["ev-bird"]

    async def test_human_query_unaffected_by_stopwords(self, test_db, db_path):
        await self._seed_bird_and_person(test_db)
        result = await investigate("any human detected", db_path)
        assert [e.event_id for e in result.events] == ["ev-person"]

    async def test_multi_agent_dog_query_clean(self, db_path):
        from app.agents.orchestrator import InvestigationOrchestrator
        from app.database import get_db
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1)
            await insert_event(
                db, RECORDING_ID_1,
                action_description="black bird perched on soil",
                object_descriptions=["black bird"],
                event_type="bird_detected",
            )
            await db.commit()
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=0.0)
        result = await orch.run(
            query="any dog detected?", recording_ids=[RECORDING_ID_1])
        assert result["final_report"]["key_findings"] == []


class TestDetectionStopwords:
    def test_detected_forms_are_stopwords(self):
        assert _tokenize("any dog detected?") == ["dog"]
        assert _tokenize("detection") == []
        assert _tokenize("detections") == []

    def test_dog_labels_stay_unforced(self):
        # No dog category exists; unknown subjects stay scene_change and
        # match by description text rather than a forced label.
        assert _infer_event_type(["brown dog"], []) == "scene_change"
