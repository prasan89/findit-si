"""Tests for per-video investigation summaries (VideoAnalysisSummary).

Covers:
- Every searched recording appears in video_summaries
- Events are correctly associated with their source recording
- Match classification logic: relevant_match, possible_match, no_match,
  needs_review, analysis_failed
- chain snatch query does not label unrelated recordings as confirmed matches
- Ineligible recordings appear as analysis_failed or needs_review
- Observation text is grounded (not fabricated) per video
"""
import json
import pytest
import uuid

from app.models import (
    MatchClassification,
    VideoAnalysisSummary,
)
from app.investigate import (
    investigate,
    _classify_video,
    _observation_for_video,
    _build_video_summaries,
)
from app.models import Event
from app.database import get_db
from tests.fixtures import (
    RECORDING_ID_1,
    RECORDING_ID_2,
    insert_recording,
    insert_event,
    now_iso,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_event(
    recording_id: str = RECORDING_ID_1,
    event_type: str = "possible-assault",
    confidence: float = 0.65,
    relevance_score: float | None = 0.85,
    uncertainty_notes: list[str] | None = None,
    action_description: str = "Two people approach each other",
    observed_facts: list[str] | None = None,
) -> Event:
    return Event(
        event_id=str(uuid.uuid4()),
        recording_id=recording_id,
        timestamp_seconds=10.0,
        event_type=event_type,
        action_description=action_description,
        confidence=confidence,
        recording_filename="test.mp4",
        relevance_score=relevance_score,
        uncertainty_notes=uncertainty_notes or [],
        observed_facts=observed_facts or [],
    )


# ── Classification unit tests ──────────────────────────────────────────────────

class TestClassifyVideo:
    def test_no_events_is_no_match(self):
        cls, conf = _classify_video([])
        assert cls == MatchClassification.NO_MATCH
        assert conf is None

    def test_incident_candidate_high_relevance_is_possible(self):
        ev = _make_event(
            event_type="possible-assault",
            relevance_score=0.9,
            uncertainty_notes=[
                "clip analysis unavailable",
                "Object transfer (e.g. chain or bag removal) not visually confirmed",
                "Incident category is an observation-only candidate, not a confirmed crime classification",
            ],
        )
        cls, conf = _classify_video([ev])
        assert cls == MatchClassification.POSSIBLE_MATCH
        assert conf is not None and conf >= 0.5

    def test_incident_candidate_low_relevance_is_needs_review(self):
        ev = _make_event(
            event_type="possible-assault",
            relevance_score=0.3,
            confidence=0.35,
            uncertainty_notes=[
                "clip analysis unavailable",
                "Incident category is an observation-only candidate, not a confirmed crime classification",
            ],
        )
        cls, conf = _classify_video([ev])
        assert cls == MatchClassification.NEEDS_REVIEW

    def test_non_incident_high_relevance_is_relevant_match(self):
        ev = _make_event(
            event_type="person_detected",
            relevance_score=0.8,
            uncertainty_notes=[],
        )
        cls, conf = _classify_video([ev])
        assert cls == MatchClassification.RELEVANT_MATCH

    def test_non_incident_medium_relevance_is_possible(self):
        ev = _make_event(
            event_type="person_detected",
            relevance_score=0.55,
            uncertainty_notes=[],
        )
        cls, conf = _classify_video([ev])
        assert cls == MatchClassification.POSSIBLE_MATCH

    def test_non_incident_low_relevance_is_needs_review(self):
        ev = _make_event(
            event_type="person_detected",
            relevance_score=0.3,
            confidence=0.3,
            uncertainty_notes=[],
        )
        cls, conf = _classify_video([ev])
        assert cls == MatchClassification.NEEDS_REVIEW

    def test_classification_never_relevant_for_observation_only_candidates(self):
        """Temporal candidates (possible-*) must never be 'relevant_match'."""
        ev = _make_event(
            event_type="possible-theft",
            relevance_score=0.99,
            confidence=0.99,
            uncertainty_notes=[
                "Incident category is an observation-only candidate, not a confirmed crime classification",
            ],
        )
        cls, _ = _classify_video([ev])
        assert cls != MatchClassification.RELEVANT_MATCH


# ── Observation text tests ──────────────────────────────────────────────────────

class TestObservationForVideo:
    def test_no_events_returns_no_activity(self):
        obs = _observation_for_video([], "test.mp4")
        assert "no relevant activity" in obs.lower()

    def test_uses_action_description_when_meaningful(self):
        ev = _make_event(
            action_description="Person approaches another and reaches for neck",
            observed_facts=["fast motion", "two people"],
        )
        obs = _observation_for_video([ev], "test.mp4")
        assert "approaches" in obs.lower() or "person" in obs.lower()

    def test_avoids_generic_template_action(self):
        """Generic 'Observed activity in possible-assault window' should not be the headline."""
        ev = _make_event(
            action_description="Observed activity in possible-assault window 4.0s–39.0s",
            observed_facts=["fast motion detected", "contact between persons"],
        )
        obs = _observation_for_video([ev], "test.mp4")
        # Should use the observed fact instead of the template action
        assert "contact" in obs.lower() or "motion" in obs.lower()

    def test_adds_unconfirmed_note_when_clip_unavailable(self):
        ev = _make_event(
            action_description="Person approach detected",
            uncertainty_notes=["Object transfer (e.g. chain or bag removal) not visually confirmed"],
        )
        obs = _observation_for_video([ev], "test.mp4")
        assert "not visually confirmed" in obs.lower() or "review" in obs.lower()

    def test_no_fabrication_for_empty_facts(self):
        """Should describe timestamp-based fallback, not invented content."""
        ev = _make_event(
            action_description="Observed activity in possible-assault window 5.0s–10.0s",
            observed_facts=[],
        )
        obs = _observation_for_video([ev], "test.mp4")
        # Should still return a non-empty string
        assert len(obs) > 10


# ── Integration tests: build_video_summaries ──────────────────────────────────

class TestBuildVideoSummaries:
    def test_all_eligible_recordings_present(self):
        rid1 = str(uuid.uuid4())
        rid2 = str(uuid.uuid4())
        rid3 = str(uuid.uuid4())  # no events

        events = [
            _make_event(recording_id=rid1),
            _make_event(recording_id=rid2),
        ]
        rec_meta = {
            rid1: {"original_filename": "a.mp4", "processing_status": "INDEXED"},
            rid2: {"original_filename": "b.mp4", "processing_status": "INDEXED"},
            rid3: {"original_filename": "c.mp4", "processing_status": "INDEXED"},
        }
        summaries = _build_video_summaries(events, [rid1, rid2, rid3], rec_meta, [])
        recording_ids = {s.recording_id for s in summaries}
        assert rid1 in recording_ids
        assert rid2 in recording_ids
        assert rid3 in recording_ids

    def test_no_match_recording_has_no_match_classification(self):
        rid_no_match = str(uuid.uuid4())
        rec_meta = {rid_no_match: {"original_filename": "empty.mp4", "processing_status": "INDEXED"}}
        summaries = _build_video_summaries([], [rid_no_match], rec_meta, [])
        assert len(summaries) == 1
        assert summaries[0].match_classification == MatchClassification.NO_MATCH

    def test_events_associated_with_correct_recording(self):
        rid1 = str(uuid.uuid4())
        rid2 = str(uuid.uuid4())
        ev1 = _make_event(recording_id=rid1)
        ev2 = _make_event(recording_id=rid2)
        rec_meta = {
            rid1: {"original_filename": "a.mp4", "processing_status": "INDEXED"},
            rid2: {"original_filename": "b.mp4", "processing_status": "INDEXED"},
        }
        summaries = _build_video_summaries([ev1, ev2], [rid1, rid2], rec_meta, [])
        s1 = next(s for s in summaries if s.recording_id == rid1)
        s2 = next(s for s in summaries if s.recording_id == rid2)
        assert ev1.event_id in s1.event_ids
        assert ev2.event_id not in s1.event_ids
        assert ev2.event_id in s2.event_ids

    def test_ineligible_failed_appears_as_analysis_failed(self):
        rid = str(uuid.uuid4())
        ineligible = [{"recording_id": rid, "original_filename": "broken.mp4",
                       "processing_status": "FAILED",
                       "video_duration_seconds": None, "thumbnail_path": None,
                       "camera_name": None, "camera_location": None}]
        summaries = _build_video_summaries([], [], {}, ineligible)
        assert len(summaries) == 1
        assert summaries[0].match_classification == MatchClassification.ANALYSIS_FAILED

    def test_ineligible_pending_appears_as_needs_review(self):
        rid = str(uuid.uuid4())
        ineligible = [{"recording_id": rid, "original_filename": "pending.mp4",
                       "processing_status": "PENDING",
                       "video_duration_seconds": None, "thumbnail_path": None,
                       "camera_name": None, "camera_location": None}]
        summaries = _build_video_summaries([], [], {}, ineligible)
        assert len(summaries) == 1
        assert summaries[0].match_classification == MatchClassification.NEEDS_REVIEW

    def test_relevant_timestamps_match_event_timestamps(self):
        rid = str(uuid.uuid4())
        ev = _make_event(recording_id=rid)
        rec_meta = {rid: {"original_filename": "a.mp4", "processing_status": "INDEXED"}}
        summaries = _build_video_summaries([ev], [rid], rec_meta, [])
        s = summaries[0]
        assert ev.timestamp_seconds in s.relevant_timestamps

    def test_no_match_has_empty_timestamps(self):
        rid = str(uuid.uuid4())
        rec_meta = {rid: {"original_filename": "a.mp4", "processing_status": "INDEXED"}}
        summaries = _build_video_summaries([], [rid], rec_meta, [])
        assert summaries[0].relevant_timestamps == []


# ── End-to-end integration test via investigate() ─────────────────────────────

@pytest.mark.anyio
async def test_investigation_returns_video_summaries_for_all_recordings(db_path, test_db):
    """investigate() must include video_summaries covering every searched recording."""
    await insert_recording(test_db, RECORDING_ID_1, filename="cam01.mp4")
    await insert_recording(test_db, RECORDING_ID_2, filename="cam02.mp4")
    # Insert a temporal event only for RECORDING_ID_1
    await test_db.execute(
        """INSERT INTO temporal_events
           (event_id, recording_id, category, start_seconds, end_seconds,
            timestamp_seconds, confidence, evidence_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), RECORDING_ID_1, "possible-assault",
            5.0, 15.0, 10.0, 0.7,
            json.dumps({
                "observed_facts": ["fast motion detected"],
                "uncertain": ["clip analysis unavailable"],
                "evidence_frames": [],
                "action_sequence": [],
                "observable_actions": [],
                "confirmed": [],
                "tracks": [],
            }),
            now_iso(),
        ),
    )
    await test_db.commit()

    result = await investigate("chain snatch", db_path)

    assert result.video_summaries is not None
    assert len(result.video_summaries) == 2

    recording_ids = {s.recording_id for s in result.video_summaries}
    assert RECORDING_ID_1 in recording_ids
    assert RECORDING_ID_2 in recording_ids

    s1 = next(s for s in result.video_summaries if s.recording_id == RECORDING_ID_1)
    s2 = next(s for s in result.video_summaries if s.recording_id == RECORDING_ID_2)

    # Recording with temporal event but no chain/snatch evidence →
    # needs_review (observation-only candidate, query gate not met)
    assert s1.match_classification in (
        MatchClassification.NEEDS_REVIEW,
        MatchClassification.NO_MATCH,
    )

    # Recording with no matching events → no_match
    assert s2.match_classification == MatchClassification.NO_MATCH
    assert s2.relevant_timestamps == []


@pytest.mark.anyio
async def test_chain_snatch_does_not_produce_relevant_match(db_path, test_db):
    """Temporal incident candidates for chain snatch must never be 'relevant_match'.

    Temporal detection is observation-only — the vision model sees neutral movement,
    not confirmed criminal behavior. A 'possible-assault' candidate from approach/
    contact/departure postures is never a confirmed chain snatch.
    """
    await insert_recording(test_db, RECORDING_ID_1, filename="12_video.mp4")
    await test_db.execute(
        """INSERT INTO temporal_events
           (event_id, recording_id, category, start_seconds, end_seconds,
            timestamp_seconds, confidence, evidence_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), RECORDING_ID_1, "possible-assault",
            4.0, 39.0, 10.0, 0.592,
            json.dumps({
                "observed_facts": ["fast motion", "contact posture"],
                "uncertain": [
                    "clip analysis unavailable",
                    "Object transfer (e.g. chain or bag removal) not visually confirmed",
                    "Incident category is an observation-only candidate, not a confirmed crime classification",
                ],
                "evidence_frames": [],
                "action_sequence": [],
                "observable_actions": [],
                "confirmed": [],
                "tracks": [],
            }),
            now_iso(),
        ),
    )
    await test_db.commit()

    result = await investigate("chain snatch", db_path)

    for summary in result.video_summaries:
        assert summary.match_classification != MatchClassification.RELEVANT_MATCH, (
            f"Recording {summary.filename} classified as relevant_match for "
            f"chain snatch — temporal candidates are observation-only"
        )


@pytest.mark.anyio
async def test_video_summaries_present_even_with_no_events(db_path, test_db):
    """video_summaries should list all recordings even when no events match."""
    await insert_recording(test_db, RECORDING_ID_1, filename="empty1.mp4")
    await insert_recording(test_db, RECORDING_ID_2, filename="empty2.mp4")

    result = await investigate("chain snatch", db_path)

    assert result.video_summaries is not None
    # Both recordings must appear even with no events
    assert len(result.video_summaries) == 2
    for s in result.video_summaries:
        assert s.match_classification in (
            MatchClassification.NO_MATCH,
            MatchClassification.NEEDS_REVIEW,
        )


@pytest.mark.anyio
async def test_ineligible_recording_in_video_summaries(db_path, test_db):
    """Failed recordings must appear in video_summaries as analysis_failed."""
    failed_rid = str(uuid.uuid4())
    await insert_recording(test_db, RECORDING_ID_1, filename="good.mp4")
    await insert_recording(test_db, failed_rid, filename="broken.mp4",
                           processing_status="FAILED")

    result = await investigate("chain snatch", db_path)

    failed_summary = next(
        (s for s in result.video_summaries if s.recording_id == failed_rid), None
    )
    assert failed_summary is not None
    assert failed_summary.match_classification == MatchClassification.ANALYSIS_FAILED
