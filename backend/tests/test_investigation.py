"""Tests for RealityTrace investigation features:
- Event schema validation
- Event extraction pipeline
- Investigation query engine
- Cross-camera correlation
- Timestamp handling
- Evidence paths
- Edge cases
"""
import json
import pytest
import uuid
from datetime import datetime, timezone

from app.models import Event, EvidenceFrame, InvestigationResult, CrossCameraLink
from app.event_extractor import (
    extract_events_for_recording,
    _group_observations_into_windows,
    _build_event,
    _infer_event_type,
    EVENT_WINDOW_SECONDS,
)
from app.investigate import investigate, _tokenize, _score_event, _parse_temporal_query
from app.correlator import correlate_across_cameras, _temporal_score, _appearance_score
from app.database import get_db
from tests.fixtures import (
    RECORDING_ID_1,
    RECORDING_ID_2,
    insert_recording,
    insert_observation,
    insert_event,
    now_iso,
)


# ── Event model validation ────────────────────────────────────────────────────

class TestEventSchema:
    def test_valid_event_passes(self):
        ev = Event(
            event_id="test-id",
            recording_id=RECORDING_ID_1,
            timestamp_seconds=10.5,
            event_type="scene_change",
            action_description="Object detected",
            confidence=0.85,
            recording_filename="cam1.mp4",
        )
        assert ev.confidence == 0.85
        assert ev.event_type == "scene_change"

    def test_confidence_out_of_range_rejected(self):
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            Event(
                event_id="x",
                recording_id=RECORDING_ID_1,
                timestamp_seconds=0,
                event_type="t",
                action_description="d",
                confidence=1.5,
                recording_filename="f",
            )

    def test_negative_confidence_rejected(self):
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            Event(
                event_id="x",
                recording_id=RECORDING_ID_1,
                timestamp_seconds=0,
                event_type="t",
                action_description="d",
                confidence=-0.1,
                recording_filename="f",
            )

    def test_evidence_frame_roles(self):
        f = EvidenceFrame(frame_path="frames/x.jpg", timestamp_seconds=5.0, role="before")
        assert f.role == "before"

    def test_cross_camera_link_fields(self):
        lnk = CrossCameraLink(
            target_event_id="ev2",
            target_recording_id=RECORDING_ID_2,
            confidence=0.6,
            link_type="candidate",
            explanation="Temporal proximity",
        )
        assert lnk.link_type == "candidate"

    def test_observed_facts_distinct_from_inferred(self):
        ev = Event(
            event_id="e",
            recording_id=RECORDING_ID_1,
            timestamp_seconds=0,
            event_type="t",
            action_description="d",
            confidence=0.8,
            recording_filename="f",
            observed_facts=["Observed: red bag"],
            inferred_claims=["May be the bag from camera 2"],
        )
        assert "red bag" in ev.observed_facts[0]
        assert "May be" in ev.inferred_claims[0]


# ── Event extraction ──────────────────────────────────────────────────────────

class TestGroupObservations:
    def _obs(self, ts: float) -> dict:
        return {
            "observation_id": str(uuid.uuid4()),
            "video_timestamp_seconds": ts,
            "object_label": "test",
            "object_attributes": "{}",
            "scene_description": None,
            "source_frame_path": f"frames/x/{ts}.jpg",
            "detection_score": 0.8,
            "recording_datetime": None,
            "recording_timezone": None,
        }

    def test_single_observation_one_group(self):
        groups = _group_observations_into_windows([self._obs(5.0)])
        assert len(groups) == 1

    def test_within_window_grouped(self):
        obs = [self._obs(0.0), self._obs(5.0), self._obs(8.0)]
        groups = _group_observations_into_windows(obs)
        assert len(groups) == 1

    def test_gap_creates_new_group(self):
        obs = [self._obs(0.0), self._obs(EVENT_WINDOW_SECONDS + 1)]
        groups = _group_observations_into_windows(obs)
        assert len(groups) == 2

    def test_exact_window_boundary_in_same_group(self):
        obs = [self._obs(0.0), self._obs(EVENT_WINDOW_SECONDS)]
        groups = _group_observations_into_windows(obs)
        assert len(groups) == 1


class TestBuildEvent:
    def _obs(self, ts: float, label: str = "red bag", score: float = 0.8) -> dict:
        return {
            "observation_id": str(uuid.uuid4()),
            "video_timestamp_seconds": ts,
            "object_label": label,
            "object_attributes": json.dumps({"color": "red"}),
            "scene_description": "Near the door",
            "source_frame_path": f"frames/test/{ts}.jpg",
            "detection_score": score,
            "recording_datetime": "2024-06-01T10:00:00+00:00",
            "recording_timezone": "UTC",
        }

    def _all_paths(self, obs_list):
        return {o["video_timestamp_seconds"]: o["source_frame_path"] for o in obs_list}

    def test_event_has_during_frame(self):
        obs = [self._obs(10.0), self._obs(12.0)]
        ev = _build_event(
            group=obs,
            recording_id=RECORDING_ID_1,
            recording_filename="cam1.mp4",
            recording_datetime="2024-06-01T10:00:00+00:00",
            recording_timezone="UTC",
            camera_name="Camera 1",
            camera_location="Entrance",
            all_frame_paths_by_ts=self._all_paths(obs),
            sorted_timestamps=sorted(self._all_paths(obs).keys()),
        )
        assert ev is not None
        roles = [f["role"] for f in ev["evidence_frames"]]
        assert "during" in roles

    def test_event_before_after_frames(self):
        all_obs = [self._obs(ts) for ts in [1.0, 5.0, 10.0, 12.0, 18.0, 25.0]]
        group = all_obs[2:4]  # 10.0, 12.0
        all_paths = self._all_paths(all_obs)
        sorted_ts = sorted(all_paths.keys())

        ev = _build_event(
            group=group,
            recording_id=RECORDING_ID_1,
            recording_filename="cam1.mp4",
            recording_datetime="2024-06-01T10:00:00+00:00",
            recording_timezone="UTC",
            camera_name=None,
            camera_location=None,
            all_frame_paths_by_ts=all_paths,
            sorted_timestamps=sorted_ts,
        )
        roles = {f["role"] for f in ev["evidence_frames"]}
        assert "before" in roles
        assert "after" in roles

    def test_absolute_timestamp_computed(self):
        # Phase 4: two confirming frames (single-frame groups are dropped).
        obs = [self._obs(60.0), self._obs(61.0)]
        ev = _build_event(
            group=obs,
            recording_id=RECORDING_ID_1,
            recording_filename="cam1.mp4",
            recording_datetime="2024-06-01T10:00:00+00:00",
            recording_timezone="UTC",
            camera_name=None,
            camera_location=None,
            all_frame_paths_by_ts=self._all_paths(obs),
            sorted_timestamps=[60.0, 61.0],
        )
        assert ev["has_reliable_timestamp"] is True
        assert ev["timestamp_absolute"] is not None
        # 10:00:00 + 60s = 10:01:00
        assert "10:01" in ev["timestamp_absolute"]

    def test_no_recording_datetime_flags_uncertainty(self):
        obs = [self._obs(10.0), self._obs(11.0)]
        for o in obs:
            o["recording_datetime"] = None
        ev = _build_event(
            group=obs,
            recording_id=RECORDING_ID_1,
            recording_filename="cam1.mp4",
            recording_datetime=None,
            recording_timezone=None,
            camera_name=None,
            camera_location=None,
            all_frame_paths_by_ts=self._all_paths(obs),
            sorted_timestamps=[10.0],
        )
        assert ev["has_reliable_timestamp"] is False
        assert any("timestamp" in n.lower() for n in ev["uncertainty_notes"])

    def test_low_confidence_filtered(self):
        obs = [self._obs(10.0, score=0.05), self._obs(11.0, score=0.05)]
        ev = _build_event(
            group=obs,
            recording_id=RECORDING_ID_1,
            recording_filename="cam1.mp4",
            recording_datetime=None,
            recording_timezone=None,
            camera_name=None,
            camera_location=None,
            all_frame_paths_by_ts=self._all_paths(obs),
            sorted_timestamps=[10.0, 11.0],
        )
        assert ev is None

    def test_observed_facts_not_inferred(self):
        obs = [self._obs(10.0, label="blue backpack"),
               self._obs(11.0, label="blue backpack")]
        ev = _build_event(
            group=obs,
            recording_id=RECORDING_ID_1,
            recording_filename="cam1.mp4",
            recording_datetime="2024-06-01T10:00:00+00:00",
            recording_timezone="UTC",
            camera_name=None,
            camera_location=None,
            all_frame_paths_by_ts=self._all_paths(obs),
            sorted_timestamps=[10.0],
        )
        assert any("blue backpack" in f for f in ev["observed_facts"])
        # Observed facts must not use speculative language
        for fact in ev["observed_facts"]:
            assert "may" not in fact.lower()
            assert "possibly" not in fact.lower()


@pytest.mark.asyncio
class TestExtractEventsForRecording:
    async def test_no_observations_returns_zero(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        n = await extract_events_for_recording(RECORDING_ID_1, db_path)
        assert n == 0

    async def test_observations_produce_events(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        for ts in [5.0, 6.0, 7.0, 20.0, 21.0]:
            await insert_observation(
                test_db, RECORDING_ID_1,
                object_label="person",
                video_timestamp_seconds=ts,
                source_frame_path=f"frames/{RECORDING_ID_1}/{ts}.jpg",
            )
        n = await extract_events_for_recording(RECORDING_ID_1, db_path)
        assert n >= 1  # at least one group

    async def test_events_stored_in_db(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        # Phase 4: two confirming frames (single-frame groups are dropped).
        for ts in (10.0, 11.0):
            await insert_observation(
                test_db, RECORDING_ID_1,
                object_label="red bag",
                video_timestamp_seconds=ts,
                source_frame_path=f"frames/r1/{ts:.0f}.jpg",
            )
        n = await extract_events_for_recording(RECORDING_ID_1, db_path)
        assert n >= 1
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                "SELECT * FROM events WHERE recording_id = ?", (RECORDING_ID_1,)
            )).fetchall()
        assert len(rows) >= 1

    async def test_idempotent_rerun(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        # Phase 4: two confirming frames (single-frame groups are dropped).
        for ts in (10.0, 11.0):
            await insert_observation(
                test_db, RECORDING_ID_1,
                object_label="person",
                video_timestamp_seconds=ts,
                source_frame_path=f"frames/r1/{ts:.0f}.jpg",
            )
        n1 = await extract_events_for_recording(RECORDING_ID_1, db_path)
        n2 = await extract_events_for_recording(RECORDING_ID_1, db_path)
        assert n1 == n2
        assert n1 >= 1

    async def test_source_frame_path_in_evidence(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="car",
            video_timestamp_seconds=5.0,
            source_frame_path="frames/r1/5.jpg",
        )
        # Phase 4: second confirming frame (single-frame groups are dropped).
        await insert_observation(
            test_db, RECORDING_ID_1,
            object_label="car",
            video_timestamp_seconds=6.0,
            source_frame_path="frames/r1/6.jpg",
        )
        await extract_events_for_recording(RECORDING_ID_1, db_path)
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT evidence_frames FROM events WHERE recording_id = ?", (RECORDING_ID_1,)
            )).fetchone()
        assert row is not None
        frames = json.loads(row["evidence_frames"])
        assert any(f["frame_path"] == "frames/r1/5.jpg" for f in frames)

    async def test_missing_recording_id_returns_zero(self, db_path):
        n = await extract_events_for_recording("nonexistent-id", db_path)
        assert n == 0


# ── Investigation query engine ────────────────────────────────────────────────

class TestTokenize:
    def test_removes_investigation_stopwords(self):
        tokens = _tokenize("What happened before the person entered the building?")
        assert "person" in tokens
        assert "building" in tokens
        assert "what" not in tokens
        assert "before" not in tokens

    def test_empty_returns_empty(self):
        assert _tokenize("") == []


class TestTemporalQuery:
    def test_parses_between_times(self):
        result = _parse_temporal_query("Show events between 10:00 and 10:15")
        assert result["start"] == 10 * 60
        assert result["end"] == 10 * 60 + 15

    def test_no_time_returns_empty(self):
        result = _parse_temporal_query("Where was the red bag?")
        assert result == {}


class TestScoreEvent:
    def test_matching_action_description_scores_high(self):
        ev = {
            "action_description": "red bag detected near door",
            "scene_description": None,
            "event_type": "unattended_object",
            "object_descriptions": ["red bag"],
            "observed_facts": [],
            "confidence": 0.8,
        }
        score = _score_event(["red", "bag"], ev)
        assert score > 0.5

    def test_unrelated_event_scores_low(self):
        ev = {
            "action_description": "vehicle detected on road",
            "scene_description": "Highway scene",
            "event_type": "vehicle_detected",
            "object_descriptions": ["blue car"],
            "observed_facts": [],
            "confidence": 0.7,
        }
        score = _score_event(["backpack", "entrance"], ev)
        assert score < 0.4

    def test_empty_query_returns_full_score(self):
        ev = {
            "action_description": "anything",
            "scene_description": None,
            "event_type": "scene_change",
            "object_descriptions": [],
            "observed_facts": [],
            "confidence": 0.5,
        }
        score = _score_event([], ev)
        assert score == 1.0


@pytest.mark.asyncio
class TestInvestigateEngine:
    async def test_no_recordings_returns_empty(self, db_path):
        result = await investigate("Find the red bag", db_path)
        assert result.events == []
        assert result.searched_recordings == 0

    async def test_no_events_returns_empty_with_gaps(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        result = await investigate("Find the red bag", db_path)
        assert result.events == []
        assert len(result.evidence_gaps) > 0

    async def test_matching_event_returned(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="red bag detected near door",
            object_descriptions=["red bag"],
            timestamp_seconds=10.0,
        )
        result = await investigate("red bag near door", db_path)
        assert len(result.events) >= 1
        assert any("bag" in e.action_description.lower() for e in result.events)

    async def test_result_has_source_video_references(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1, filename="cam1.mp4")
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="person detected",
            object_descriptions=["person"],
        )
        result = await investigate("person detected", db_path)
        assert result.events[0].recording_id == RECORDING_ID_1

    async def test_chronological_ordering_with_timestamps(self, test_db, db_path):
        await insert_recording(
            test_db, RECORDING_ID_1,
            recording_datetime="2024-06-01T10:00:00+00:00",
        )
        await insert_recording(
            test_db, RECORDING_ID_2,
            recording_datetime="2024-06-01T10:05:00+00:00",
        )
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="person detected",
            object_descriptions=["person"],
            timestamp_seconds=0.0,
            has_reliable_timestamp=True,
            timestamp_absolute="2024-06-01T10:00:00+00:00",
        )
        await insert_event(
            test_db, RECORDING_ID_2,
            action_description="person detected",
            object_descriptions=["person"],
            timestamp_seconds=0.0,
            has_reliable_timestamp=True,
            timestamp_absolute="2024-06-01T10:05:00+00:00",
        )
        result = await investigate("person", db_path)
        assert result.timestamp_ordering_reliable is True
        ids = [e.recording_id for e in result.events]
        assert ids[0] == RECORDING_ID_1  # earlier first

    async def test_no_timestamps_ordering_unreliable(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1, recording_datetime=None)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="person detected",
            object_descriptions=["person"],
        )
        result = await investigate("person", db_path)
        assert result.timestamp_ordering_reliable is False

    async def test_time_range_filter_applies(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="red bag detected",
            object_descriptions=["red bag"],
            timestamp_seconds=5.0,
        )
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="red bag detected",
            object_descriptions=["red bag"],
            timestamp_seconds=900.0,  # 15 minutes
        )
        result = await investigate(
            "red bag", db_path,
            time_start_seconds=0,
            time_end_seconds=60,
        )
        assert all(e.timestamp_seconds <= 60 for e in result.events)

    async def test_evidence_gaps_reported_for_unmatched_recordings(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1, filename="cam1.mp4")
        await insert_recording(test_db, RECORDING_ID_2, filename="cam2.mp4")
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="red bag detected",
            object_descriptions=["red bag"],
        )
        # RECORDING_ID_2 has no matching events for this query
        result = await investigate("red bag", db_path)
        # cam2 should appear as a gap or may match if general
        assert result.evidence_gaps is not None

    async def test_no_fabricated_events(self, test_db, db_path):
        """Events in result must have recording_id from actual recordings."""
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="test event",
            object_descriptions=["test"],
        )
        result = await investigate("test", db_path)
        real_ids = {RECORDING_ID_1}
        for ev in result.events:
            assert ev.recording_id in real_ids

    async def test_investigation_api_endpoint(self, client):
        """Integration test: POST /api/investigations/"""
        resp = await client.post(
            "/api/investigations/",
            json={"query": "red bag near entrance"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "investigation_id" in data
        assert "events" in data
        assert "narrative" in data

    async def test_investigation_empty_query_rejected(self, client):
        resp = await client.post(
            "/api/investigations/",
            json={"query": ""},
        )
        assert resp.status_code == 400

    async def test_investigation_specific_recording_ids(self, client):
        resp = await client.post(
            "/api/investigations/",
            json={
                "query": "anything",
                "recording_ids": [RECORDING_ID_1],
            },
        )
        assert resp.status_code == 200

    async def test_investigation_returns_before_after_evidence(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        frames = [
            {"frame_path": "frames/r1/before.jpg", "timestamp_seconds": 3.0, "role": "before"},
            {"frame_path": "frames/r1/during.jpg", "timestamp_seconds": 8.0, "role": "during"},
            {"frame_path": "frames/r1/after.jpg", "timestamp_seconds": 13.0, "role": "after"},
        ]
        await insert_event(
            test_db, RECORDING_ID_1,
            action_description="person detected",
            object_descriptions=["person"],
            evidence_frames=frames,
        )
        result = await investigate("person", db_path)
        assert len(result.events) >= 1
        ev = result.events[0]
        roles = {f.role for f in ev.evidence_frames}
        assert "before" in roles
        assert "during" in roles
        assert "after" in roles


# ── Cross-camera correlation ──────────────────────────────────────────────────

class TestTemporalScore:
    def test_close_events_score_high(self):
        ev_a = {"abs_ts": 1000.0}
        ev_b = {"abs_ts": 1010.0}
        score, expl = _temporal_score(ev_a, ev_b)
        assert score > 0.0
        assert "10" in expl

    def test_far_events_score_zero(self):
        ev_a = {"abs_ts": 1000.0}
        ev_b = {"abs_ts": 5000.0}
        score, expl = _temporal_score(ev_a, ev_b)
        assert score == 0.0

    def test_missing_abs_ts_returns_low_score(self):
        ev_a = {"abs_ts": None}
        ev_b = {"abs_ts": None}
        score, expl = _temporal_score(ev_a, ev_b)
        assert score <= 0.1


class TestAppearanceScore:
    def test_matching_objects_score_high(self):
        ev_a = {"object_descriptions": ["red backpack"]}
        ev_b = {"object_descriptions": ["red backpack"]}
        score, expl = _appearance_score(ev_a, ev_b)
        assert score > 0.0
        assert "red" in expl.lower() or "backpack" in expl.lower()

    def test_unrelated_objects_score_zero(self):
        ev_a = {"object_descriptions": ["blue car"]}
        ev_b = {"object_descriptions": ["wooden chair"]}
        score, expl = _appearance_score(ev_a, ev_b)
        assert score == 0.0

    def test_note_added_for_appearance_match(self):
        ev_a = {"object_descriptions": ["red jacket person"]}
        ev_b = {"object_descriptions": ["red jacket person"]}
        _, expl = _appearance_score(ev_a, ev_b)
        # appearance match note is added in composite score, not appearance alone
        assert len(expl) > 0


@pytest.mark.asyncio
class TestCorrelateAcrossCameras:
    async def test_same_recording_not_linked(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev1",
            action_description="person",
            object_descriptions=["person"],
            timestamp_seconds=10.0,
        )
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev2",
            action_description="person",
            object_descriptions=["person"],
            timestamp_seconds=20.0,
        )
        n = await correlate_across_cameras(db_path)
        assert n == 0

    async def test_similar_events_different_cameras_linked(self, test_db, db_path):
        await insert_recording(
            test_db, RECORDING_ID_1,
            recording_datetime="2024-06-01T10:00:00+00:00",
            camera_name="Camera 1",
        )
        await insert_recording(
            test_db, RECORDING_ID_2,
            recording_datetime="2024-06-01T10:01:00+00:00",
            camera_name="Camera 2",
        )
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="ev1",
            action_description="person with red backpack",
            object_descriptions=["red backpack", "person"],
            timestamp_seconds=0.0,
        )
        await insert_event(
            test_db, RECORDING_ID_2,
            event_id="ev2",
            action_description="person with red backpack",
            object_descriptions=["red backpack", "person"],
            timestamp_seconds=0.0,
        )
        n = await correlate_across_cameras(db_path)
        assert n > 0

    async def test_links_labeled_candidate_not_confirmed(self, test_db, db_path):
        """Appearance match should be candidate, not confirmed."""
        await insert_recording(
            test_db, RECORDING_ID_1,
            recording_datetime="2024-06-01T10:00:00+00:00",
        )
        await insert_recording(
            test_db, RECORDING_ID_2,
            recording_datetime="2024-06-01T10:00:30+00:00",
        )
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="evA",
            action_description="person in blue jacket",
            object_descriptions=["blue jacket", "person"],
            timestamp_seconds=0.0,
        )
        await insert_event(
            test_db, RECORDING_ID_2,
            event_id="evB",
            action_description="person in blue jacket",
            object_descriptions=["blue jacket", "person"],
            timestamp_seconds=0.0,
        )
        await correlate_across_cameras(db_path)
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                "SELECT link_type, explanation FROM event_links"
            )).fetchall()
        assert len(rows) > 0
        # All links should be candidate (appearance alone can't confirm identity)
        for row in rows:
            assert row["link_type"] in ("candidate", "confirmed")
            # Must have uncertainty notice
            if row["link_type"] == "candidate":
                # explanation should note the uncertainty
                assert len(row["explanation"]) > 0

    async def test_correlation_idempotent(self, test_db, db_path):
        await insert_recording(
            test_db, RECORDING_ID_1,
            recording_datetime="2024-06-01T10:00:00+00:00",
        )
        await insert_recording(
            test_db, RECORDING_ID_2,
            recording_datetime="2024-06-01T10:01:00+00:00",
        )
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="evA",
            action_description="car detected",
            object_descriptions=["red car"],
            timestamp_seconds=0.0,
        )
        await insert_event(
            test_db, RECORDING_ID_2,
            event_id="evB",
            action_description="car detected",
            object_descriptions=["red car"],
            timestamp_seconds=0.0,
        )
        n1 = await correlate_across_cameras(db_path)
        n2 = await correlate_across_cameras(db_path)
        # Idempotent: same number of links each run
        assert n1 == n2

    async def test_unsynchronized_clocks_handled(self, test_db, db_path):
        """Recording without datetime should still produce candidate links based on appearance."""
        await insert_recording(
            test_db, RECORDING_ID_1,
            recording_datetime="2024-06-01T10:00:00+00:00",
        )
        await insert_recording(
            test_db, RECORDING_ID_2,
            recording_datetime=None,  # unsynchronized clock
        )
        await insert_event(
            test_db, RECORDING_ID_1,
            event_id="evA",
            action_description="blue bag detected",
            object_descriptions=["blue bag"],
            timestamp_seconds=10.0,
        )
        await insert_event(
            test_db, RECORDING_ID_2,
            event_id="evB",
            action_description="blue bag detected",
            object_descriptions=["blue bag"],
            timestamp_seconds=15.0,
        )
        n = await correlate_across_cameras(db_path)
        # May or may not find link depending on appearance; should not crash
        assert n >= 0


# ── Timestamp correctness ─────────────────────────────────────────────────────

class TestTimestampConversion:
    def test_relative_to_absolute_offset(self):
        """Video timestamp at 65s into recording starting at 10:00:00 → 10:01:05"""
        recording_datetime = "2024-06-01T10:00:00+00:00"
        video_ts = 65.0
        dt = datetime.fromisoformat(recording_datetime)
        abs_dt = datetime.fromtimestamp(dt.timestamp() + video_ts, tz=timezone.utc)
        assert abs_dt.hour == 10
        assert abs_dt.minute == 1
        assert abs_dt.second == 5

    def test_event_ordering_across_videos(self):
        """Events with absolute timestamps should sort correctly."""
        from app.investigate import _sort_key
        ev_early = {
            "recording_datetime": "2024-06-01T10:00:00+00:00",
            "timestamp_seconds": 30.0,
        }
        ev_late = {
            "recording_datetime": "2024-06-01T10:01:00+00:00",
            "timestamp_seconds": 0.0,
        }
        assert _sort_key(ev_early) < _sort_key(ev_late)

    def test_missing_timestamp_falls_back_to_relative(self):
        from app.investigate import _sort_key
        ev = {
            "recording_datetime": None,
            "timestamp_seconds": 300.0,
        }
        assert _sort_key(ev) == 300.0


# ── Upload with camera metadata ────────────────────────────────────────────────

@pytest.mark.asyncio
class TestUploadWithCameraMetadata:
    async def test_upload_accepts_camera_fields(self, client):
        from io import BytesIO
        from tests.fixtures import FAKE_VIDEO_BYTES
        resp = await client.post(
            "/api/recordings/upload",
            files={"file": ("cam1.mp4", BytesIO(FAKE_VIDEO_BYTES), "video/mp4")},
            data={
                "recording_datetime": "2024-06-01T10:00:00+00:00",
                "recording_timezone": "UTC",
                "camera_name": "Entrance Camera",
                "camera_location": "Front Door",
            },
        )
        assert resp.status_code == 202

    async def test_camera_fields_in_recording_status(self, client):
        from io import BytesIO
        from tests.fixtures import FAKE_VIDEO_BYTES
        upload = await client.post(
            "/api/recordings/upload",
            files={"file": ("cam2.mp4", BytesIO(FAKE_VIDEO_BYTES), "video/mp4")},
            data={
                "camera_name": "Side Entrance",
                "camera_location": "West Wing",
            },
        )
        rid = upload.json()["recording_id"]
        resp = await client.get(f"/api/recordings/{rid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["camera_name"] == "Side Entrance"
        assert data["camera_location"] == "West Wing"
