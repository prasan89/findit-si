"""Visual object discovery: insect evidence must be retrievable, not invented.

Incident: a 30s video visibly shows a bird with a grub/larva; the indexed
event descriptions contain grub/larva/fly/ants evidence, but
`any insect detected` returned zero events — no insect vocabulary existed
in retrieval. These tests pin evidence-grounded insect retrieval plus
honest reporting when footage was never indexed or analysis failed.
"""
import pytest
import pytest_asyncio

from app.investigate import investigate
from tests.fixtures import RECORDING_ID_1, RECORDING_ID_2, insert_event, insert_recording

INSECT_ACTION = "brown bird perched with white grub in beak"
INSECT_OBJECTS = ["brown bird", "white grub (curved segmented body)"]


@pytest.mark.asyncio
class TestInsectDiscovery:
    async def _seed_insect_scene(self, test_db):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev-grub",
            action_description=INSECT_ACTION,
            object_descriptions=INSECT_OBJECTS,
            observed_facts=["Observed: white grub in beak"],
            event_type="bird_detected",
        )
        await test_db.commit()

    async def test_insect_query_finds_described_grub(self, test_db, db_path):
        await self._seed_insect_scene(test_db)
        result = await investigate("any insect detected", db_path)
        assert [e.event_id for e in result.events] == ["ev-grub"]

    async def test_insect_query_finds_ants_and_larvae(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="ants crawling on feathers",
            object_descriptions=["group of ants"],
            event_type="bird_detected",
        )
        await test_db.commit()
        result = await investigate("any insect detected", db_path)
        assert len(result.events) == 1

    async def test_bird_without_insect_not_returned(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="black bird perched on soil",
            object_descriptions=["black bird"],
            event_type="bird_detected",
        )
        await test_db.commit()
        assert (await investigate("any insect detected", db_path)).events == []

    async def test_grub_query_requires_grub_evidence(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev-ants",
            action_description="ants crawling on feathers",
            object_descriptions=["group of ants"],
            event_type="bird_detected",
        )
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev-grub",
            action_description="bird with white grub in beak",
            object_descriptions=["white grub"],
            event_type="bird_detected",
        )
        await test_db.commit()
        result = await investigate("grub", db_path)
        assert [e.event_id for e in result.events] == ["ev-grub"]
        both = await investigate("any insect detected", db_path)
        assert {e.event_id for e in both.events} == {"ev-ants", "ev-grub"}

    async def test_bird_queries_still_find_bird(self, test_db, db_path):
        await self._seed_insect_scene(test_db)
        result = await investigate("any birds detected", db_path)
        assert [e.event_id for e in result.events] == ["ev-grub"]

    async def test_holding_questions_match_by_bird_evidence(
        self, test_db, db_path
    ):
        await self._seed_insect_scene(test_db)
        for query in ("is a bird carrying anything?",
                      "what is the bird holding?"):
            result = await investigate(query, db_path)
            assert [e.event_id for e in result.events] == ["ev-grub"]

    async def test_ambiguous_object_not_invented_as_grub(
        self, test_db, db_path
    ):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="small pale oval object partially embedded",
            object_descriptions=["small pale oval object"],
            event_type="scene_change",
        )
        await test_db.commit()
        assert (await investigate("grub", db_path)).events == []
        found = await investigate("oval object", db_path)
        assert len(found.events) == 1

    async def test_paired_subjects_unaffected(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1, event_id="ev-person",
            action_description="red person at entrance",
            object_descriptions=["red person"],
            event_type="person_detected",
        )
        await insert_event(
            test_db, RECORDING_ID_1, event_id="ev-dog",
            action_description="brown dog running",
            object_descriptions=["brown dog"],
            event_type="scene_change",
        )
        await insert_event(
            test_db, RECORDING_ID_1, event_id="ev-car",
            action_description="red car parked",
            object_descriptions=["red car"],
            event_type="vehicle_detected",
        )
        await test_db.commit()
        assert (await investigate("any insect detected", db_path)).events == []
        assert [e.event_id for e in
                (await investigate("any human detected", db_path)).events] == ["ev-person"]
        assert [e.event_id for e in
                (await investigate("dog", db_path)).events] == ["ev-dog"]
        assert [e.event_id for e in
                (await investigate("any vehicle detected", db_path)).events] == ["ev-car"]


@pytest.mark.asyncio
class TestIndexingCoverageReporting:
    async def test_never_indexed_recording_reported(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1,
                               processing_status="PENDING")
        await insert_recording(test_db, RECORDING_ID_2)
        await insert_event(
            test_db, RECORDING_ID_2,
            action_description="black bird perched",
            object_descriptions=["black bird"],
            event_type="bird_detected",
        )
        await test_db.commit()
        result = await investigate("any insect detected", db_path)
        assert result.events == []
        assert any("not indexed" in g.lower() for g in result.evidence_gaps)

    async def test_failed_recording_reported(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1,
                               processing_status="FAILED")
        await insert_recording(test_db, RECORDING_ID_2)
        await insert_event(
            test_db, RECORDING_ID_2,
            action_description="black bird perched",
            object_descriptions=["black bird"],
            event_type="bird_detected",
        )
        await test_db.commit()
        result = await investigate("any insect detected", db_path)
        assert result.events == []
        assert any("failed" in g.lower() for g in result.evidence_gaps)
