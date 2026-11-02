"""Phase 6 regression tests: narrative retrieval in investigations.

Validates that whole-recording narrative steps are:
- Included in investigation results when they match the query
- Ranked below direct segment evidence (source_type ordering)
- Not returned when they don't match (no-match behavior)
- Backward-compatible when recording_narrative is null
- Not duplicated on reprocessing (source_type preserved)
- Timestamp-resolved from narrative step bounds
- Excluded by time-filter when out of range
"""
import json
import uuid
from pathlib import Path

import aiosqlite
import pytest

from app.database import create_tables, get_db
from app.investigate import investigate


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _insert_recording(
    db_path: str,
    rid: str,
    narrative: dict | None = None,
    filename: str = "test.mp4",
) -> None:
    narrative_json = json.dumps(narrative) if narrative is not None else None
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO recordings("
            "recording_id, original_filename, upload_timestamp,"
            "video_path, processing_status, frame_count,"
            "created_at, updated_at, recording_narrative)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (rid, filename, "2026-01-01T00:00:00Z",
             f"videos/{rid}.mp4", "INDEXED", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", narrative_json),
        )
        await db.commit()


async def _insert_event(
    db_path: str,
    rid: str,
    event_id: str | None = None,
    action: str = "Person walks through corridor",
    event_type: str = "person_detected",
    timestamp: float = 5.0,
    confidence: float = 0.8,
) -> str:
    eid = event_id or str(uuid.uuid4())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO events("
            "event_id, recording_id, timestamp_seconds, event_type,"
            "action_description, confidence, created_at) VALUES(?,?,?,?,?,?,?)",
            (eid, rid, timestamp, event_type, action, confidence,
             "2026-01-01T00:00:00Z"),
        )
        await db.commit()
    return eid


_SNATCH_NARRATIVE = {
    "scene": "Outdoor street near market stall, daytime",
    "narrative_steps": [
        {
            "step": 1,
            "time_range": "0:00-0:18",
            "start_seconds": 0.0,
            "end_seconds": 18.0,
            "description": "A woman wearing gold jewellery stands near a shop stall.",
        },
        {
            "step": 2,
            "time_range": "0:18-0:28",
            "start_seconds": 18.0,
            "end_seconds": 28.0,
            "description": "A man on a motorcycle approaches and snatches her necklace.",
        },
        {
            "step": 3,
            "time_range": "0:28-0:45",
            "start_seconds": 28.0,
            "end_seconds": 45.0,
            "description": "The motorcycle flees rapidly; the woman is distressed.",
        },
    ],
    "key_observations": ["Chain snatch occurred at approximately 0:18"],
    "open_questions": ["Vehicle plate not visible"],
    "entities": [],
}


# ── Test: narrative steps appear in results ───────────────────────────────────

class TestNarrativeRetrieval:
    @pytest.mark.asyncio
    async def test_narrative_step_included_when_query_matches(self, tmp_path: Path):
        """A narrative step describing a chain snatch surfaces for that query."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid, narrative=_SNATCH_NARRATIVE)

        result = await investigate("chain snatch", db_path)
        assert result.events, "Expected at least one event from narrative"
        sources = {e.source_type for e in result.events}
        assert "recording_narrative" in sources

    @pytest.mark.asyncio
    async def test_narrative_step_timestamp_matches_step_midpoint(self, tmp_path: Path):
        """Narrative-derived events have timestamp = midpoint of the step's time range."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid, narrative=_SNATCH_NARRATIVE)

        result = await investigate("chain snatch", db_path)
        narr_events = [e for e in result.events if e.source_type == "recording_narrative"]
        assert narr_events
        # Snatch step: start=18, end=28 → mid=23
        timestamps = {e.timestamp_seconds for e in narr_events}
        assert 23.0 in timestamps

    @pytest.mark.asyncio
    async def test_narrative_step_has_correct_event_type(self, tmp_path: Path):
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid, narrative=_SNATCH_NARRATIVE)

        result = await investigate("chain snatch", db_path)
        narr_events = [e for e in result.events if e.source_type == "recording_narrative"]
        assert all(e.event_type == "narrative_step" for e in narr_events)

    @pytest.mark.asyncio
    async def test_narrative_step_has_uncertainty_note(self, tmp_path: Path):
        """Narrative steps carry a note that they're not direct vision detections."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid, narrative=_SNATCH_NARRATIVE)

        result = await investigate("chain snatch", db_path)
        narr_events = [e for e in result.events if e.source_type == "recording_narrative"]
        assert narr_events
        for ev in narr_events:
            assert any(
                "narrative" in n.lower() or "not a direct" in n.lower()
                for n in ev.uncertainty_notes
            ), f"Missing uncertainty note on narrative event: {ev.uncertainty_notes}"


# ── Test: ranking — segment events outrank narrative events ──────────────────

class TestNarrativeRanking:
    @pytest.mark.asyncio
    async def test_segment_event_outranks_narrative_step(self, tmp_path: Path):
        """A direct segment detection for the same query should have higher relevance."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid, narrative=_SNATCH_NARRATIVE)
        # Insert a high-confidence segment event for the same query
        await _insert_event(
            db_path, rid,
            action="Person snatches chain necklace from victim",
            event_type="person_detected",
            timestamp=20.0,
            confidence=0.9,
        )

        result = await investigate("chain snatch", db_path)
        assert len(result.events) >= 2
        seg_events = [e for e in result.events if e.source_type == "segment_event"]
        narr_events = [e for e in result.events if e.source_type == "recording_narrative"]
        assert seg_events and narr_events

        best_seg = max(e.relevance_score or 0.0 for e in seg_events)
        best_narr = max(e.relevance_score or 0.0 for e in narr_events)
        assert best_seg > best_narr, (
            f"Segment ({best_seg:.3f}) should outrank narrative ({best_narr:.3f})"
        )

    @pytest.mark.asyncio
    async def test_segment_events_default_source_type(self, tmp_path: Path):
        """Events from the events table have source_type='segment_event'."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid)
        await _insert_event(db_path, rid, action="Person walks through", timestamp=5.0)

        result = await investigate("person", db_path)
        assert result.events
        for ev in result.events:
            assert ev.source_type == "segment_event"


# ── Test: no-match behavior ───────────────────────────────────────────────────

class TestNarrativeNoMatch:
    @pytest.mark.asyncio
    async def test_unrelated_query_does_not_surface_narrative(self, tmp_path: Path):
        """A query about animals should not surface a chain-snatch narrative."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid, narrative=_SNATCH_NARRATIVE)

        result = await investigate("cat or dog", db_path)
        narr_events = [e for e in result.events if e.source_type == "recording_narrative"]
        assert not narr_events, (
            "Narrative events for chain snatch should not appear for animal query"
        )

    @pytest.mark.asyncio
    async def test_empty_narrative_steps_no_events(self, tmp_path: Path):
        """A narrative with zero steps produces no narrative-sourced events."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid, narrative={
            "scene": "Empty corridor",
            "narrative_steps": [],
            "key_observations": [],
            "open_questions": [],
        })

        result = await investigate("chain snatch", db_path)
        narr_events = [e for e in result.events if e.source_type == "recording_narrative"]
        assert not narr_events


# ── Test: backward compatibility ─────────────────────────────────────────────

class TestNarrativeBackwardCompat:
    @pytest.mark.asyncio
    async def test_no_narrative_recording_still_works(self, tmp_path: Path):
        """Recordings without recording_narrative are searched normally."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid, narrative=None)
        await _insert_event(db_path, rid, action="Person detected at entrance", timestamp=3.0)

        result = await investigate("person", db_path)
        assert result.events
        assert all(e.source_type == "segment_event" for e in result.events)

    @pytest.mark.asyncio
    async def test_mixed_recordings_narrative_only_one(self, tmp_path: Path):
        """When only some recordings have narrative, results still cover all."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)

        rid_a = str(uuid.uuid4())
        rid_b = str(uuid.uuid4())
        await _insert_recording(db_path, rid_a, narrative=_SNATCH_NARRATIVE, filename="cam_a.mp4")
        await _insert_recording(db_path, rid_b, narrative=None, filename="cam_b.mp4")
        await _insert_event(db_path, rid_b, action="Motorcycle seen speeding away")

        result = await investigate("chain snatch motorcycle", db_path)
        assert result.searched_recordings == 2
        sources = {e.source_type for e in result.events}
        # cam_a contributes narrative steps, cam_b contributes segment events
        assert "recording_narrative" in sources
        assert "segment_event" in sources


# ── Test: time filter interaction ─────────────────────────────────────────────

class TestNarrativeTimeFilter:
    @pytest.mark.asyncio
    async def test_time_filter_excludes_narrative_step_outside_range(self, tmp_path: Path):
        """Narrative steps with midpoint outside the time range are excluded."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid, narrative=_SNATCH_NARRATIVE)

        # Request only 0-10s window; snatch step is at 18-28s (mid=23) → excluded
        result = await investigate("chain snatch", db_path, time_end_seconds=10.0)
        narr_events = [e for e in result.events if e.source_type == "recording_narrative"]
        assert all(e.timestamp_seconds <= 10.0 for e in narr_events), (
            "Narrative events outside time range should be excluded"
        )

    @pytest.mark.asyncio
    async def test_time_filter_includes_narrative_step_in_range(self, tmp_path: Path):
        """Narrative steps with midpoint inside the time range are included."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid, narrative=_SNATCH_NARRATIVE)

        # 15-30s window should include step 2 (mid=23) but exclude step 1 (mid=9) and 3 (mid=36.5)
        result = await investigate("chain snatch", db_path, time_start_seconds=15.0, time_end_seconds=30.0)
        narr_events = [e for e in result.events if e.source_type == "recording_narrative"]
        assert narr_events
        assert all(15.0 <= e.timestamp_seconds <= 30.0 for e in narr_events)


# ── Test: deduplication / idempotency ─────────────────────────────────────────

class TestNarrativeDeduplication:
    @pytest.mark.asyncio
    async def test_reprocessing_same_narrative_no_duplicates(self, tmp_path: Path):
        """Calling investigate twice with the same narrative produces no duplicates."""
        db_path = str(tmp_path / "findit.db")
        await create_tables(db_path)
        rid = str(uuid.uuid4())
        await _insert_recording(db_path, rid, narrative=_SNATCH_NARRATIVE)

        result1 = await investigate("chain snatch", db_path)
        result2 = await investigate("chain snatch", db_path)

        narr1 = [e.event_id for e in result1.events if e.source_type == "recording_narrative"]
        narr2 = [e.event_id for e in result2.events if e.source_type == "recording_narrative"]
        assert sorted(narr1) == sorted(narr2), "Second call produced different narrative events"
        assert len(narr1) == len(set(narr1)), "Duplicate narrative event_ids in single result"
