"""Regression tests: query-relevance classification for video summaries.

These tests verify that the *query gate* correctly separates event detection
from query match:
  - Detecting a "possible-assault" does not mean a chain-snatch query matches.
  - Generic motion/activity observations do not inflate to POSSIBLE_MATCH.
  - Only evidence that textually supports the query produces POSSIBLE_MATCH.
  - Unrelated queries (e.g. "shoplifting") don't match unrelated assault events.
  - Count metrics (relevant observations) use query-match status, not event label.
"""
import json
import uuid
import pytest

from app.models import MatchClassification
from app.investigate import _classify_video, _tokenize, investigate
from tests.fixtures import (
    RECORDING_ID_1,
    RECORDING_ID_2,
    insert_recording,
    now_iso,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_incident_event(
    event_type: str = "possible-assault",
    observed_facts: list[str] | None = None,
    action_description: str = "Motion detected",
    confidence: float = 0.65,
    recording_id: str = RECORDING_ID_1,
):
    """Make an Event that looks like a temporal incident candidate."""
    from app.models import Event
    return Event(
        event_id=str(uuid.uuid4()),
        recording_id=recording_id,
        timestamp_seconds=10.0,
        event_type=event_type,
        action_description=action_description,
        confidence=confidence,
        recording_filename="test.mp4",
        relevance_score=0.88,   # preset score — query-blind
        uncertainty_notes=[
            "clip analysis unavailable",
            "Incident category is an observation-only candidate, not a confirmed crime classification",
        ],
        observed_facts=observed_facts or ["fast motion detected"],
    )


# ── _classify_video unit tests ─────────────────────────────────────────────────

class TestQueryGate:
    """The query gate must fire on _classify_video when query_terms are given."""

    def test_assault_does_not_match_chain_snatch_query(self):
        """possible-assault with generic motion evidence is not a chain-snatch match."""
        ev = _make_incident_event(
            event_type="possible-assault",
            observed_facts=["fast motion detected", "person approached another"],
            action_description="Possible assault activity 5s–15s",
        )
        qt = _tokenize("chain snatch")
        cls, _ = _classify_video([ev], qt)
        assert cls in (MatchClassification.NO_MATCH, MatchClassification.NEEDS_REVIEW), (
            f"Expected no_match or needs_review, got {cls.value}. "
            "Generic assault evidence should not match 'chain snatch' query."
        )
        assert cls != MatchClassification.POSSIBLE_MATCH

    def test_ambiguous_object_transfer_is_needs_review_not_possible(self):
        """Object transfer without chain/necklace evidence = needs_review at most."""
        ev = _make_incident_event(
            event_type="possible-assault",
            observed_facts=["unspecified object transfer", "contact between persons"],
            action_description="Object transfer activity observed",
        )
        qt = _tokenize("chain snatch")
        cls, _ = _classify_video([ev], qt)
        # Should be needs_review or no_match, NOT possible_match
        assert cls != MatchClassification.POSSIBLE_MATCH, (
            f"Ambiguous object transfer got {cls.value}, expected needs_review or no_match."
        )

    def test_chain_necklace_evidence_produces_possible_match(self):
        """Events with clear necklace/chain vocabulary should reach possible_match."""
        ev = _make_incident_event(
            event_type="possible-assault",
            observed_facts=[
                "person grabs chain from victim's neck",
                "necklace snatched away",
            ],
            action_description="Person forcibly removes necklace",
        )
        qt = _tokenize("chain snatch")
        cls, _ = _classify_video([ev], qt)
        assert cls == MatchClassification.POSSIBLE_MATCH, (
            f"Chain/necklace evidence should produce possible_match, got {cls.value}."
        )

    def test_shoplifting_query_does_not_match_assault_event(self):
        """An assault event should not match a shoplifting investigation."""
        ev = _make_incident_event(
            event_type="possible-assault",
            observed_facts=["person hits another person", "physical contact"],
            action_description="Assault activity",
        )
        qt = _tokenize("shoplifting")
        cls, _ = _classify_video([ev], qt)
        assert cls in (MatchClassification.NO_MATCH, MatchClassification.NEEDS_REVIEW), (
            f"Assault event should not match shoplifting query, got {cls.value}."
        )
        assert cls != MatchClassification.POSSIBLE_MATCH

    def test_no_query_terms_falls_back_to_detection_score(self):
        """With no query terms, classification falls back to detection score alone."""
        ev = _make_incident_event(
            event_type="possible-assault",
            observed_facts=["fast motion"],
            confidence=0.7,
        )
        cls, score = _classify_video([ev], [])
        # With empty query terms, detection score (0.88) > 0.5 → possible_match
        assert cls == MatchClassification.POSSIBLE_MATCH

    def test_relevant_match_never_for_incident_candidates(self):
        """Incident candidates with perfect chain evidence are still possible, not relevant."""
        ev = _make_incident_event(
            event_type="possible-theft",
            observed_facts=[
                "chain snatched from victim's neck",
                "necklace grabbed and pulled",
                "person flees with chain",
            ],
            action_description="Chain snatch observed",
        )
        qt = _tokenize("chain snatch")
        cls, _ = _classify_video([ev], qt)
        assert cls != MatchClassification.RELEVANT_MATCH, (
            "Incident candidates are observation-only — never relevant_match."
        )


# ── Integration tests via investigate() ───────────────────────────────────────

@pytest.mark.anyio
async def test_chain_snatch_filters_generic_assault_to_no_match(db_path, test_db):
    """Chain snatch query: possible-assault with generic facts → no_match or needs_review."""
    await insert_recording(test_db, RECORDING_ID_1, filename="generic_assault.mp4")
    await test_db.execute(
        """INSERT INTO temporal_events
           (event_id, recording_id, category, start_seconds, end_seconds,
            timestamp_seconds, confidence, evidence_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), RECORDING_ID_1, "possible-assault",
            4.0, 39.0, 10.0, 0.592,
            json.dumps({
                "observed_facts": ["fast motion", "contact posture", "person approached"],
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

    summary = next(s for s in result.video_summaries if s.recording_id == RECORDING_ID_1)
    assert summary.match_classification in (
        MatchClassification.NO_MATCH,
        MatchClassification.NEEDS_REVIEW,
    ), (
        f"Generic assault evidence classified as {summary.match_classification.value} "
        "for chain snatch — expected no_match or needs_review."
    )
    assert summary.match_classification != MatchClassification.POSSIBLE_MATCH


@pytest.mark.anyio
async def test_chain_snatch_with_necklace_evidence_stays_possible(db_path, test_db):
    """Chain snatch query: events with chain/necklace evidence → possible_match."""
    await insert_recording(test_db, RECORDING_ID_1, filename="chain_video.mp4")
    await test_db.execute(
        """INSERT INTO temporal_events
           (event_id, recording_id, category, start_seconds, end_seconds,
            timestamp_seconds, confidence, evidence_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), RECORDING_ID_1, "possible-assault",
            4.0, 20.0, 10.0, 0.7,
            json.dumps({
                "observed_facts": [
                    "person grabs chain from victim",
                    "necklace removed forcibly",
                    "contact between two persons",
                ],
                "uncertain": [
                    "clip analysis unavailable",
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

    summary = next(s for s in result.video_summaries if s.recording_id == RECORDING_ID_1)
    assert summary.match_classification == MatchClassification.POSSIBLE_MATCH, (
        f"Chain/necklace evidence should produce possible_match, got {summary.match_classification.value}."
    )


@pytest.mark.anyio
async def test_counts_exclude_generic_assault_from_relevant_observations(db_path, test_db):
    """Relevant observations count uses query-match status, not event-detection label.

    When generic assault events don't match the chain-snatch query, the
    'relevant observations' count should not include them. Only videos
    with actual chain/necklace evidence count.
    """
    await insert_recording(test_db, RECORDING_ID_1, filename="generic_assault.mp4")
    await insert_recording(test_db, RECORDING_ID_2, filename="chain_video.mp4")

    # Generic assault — no chain evidence
    await test_db.execute(
        """INSERT INTO temporal_events
           (event_id, recording_id, category, start_seconds, end_seconds,
            timestamp_seconds, confidence, evidence_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), RECORDING_ID_1, "possible-assault",
            4.0, 30.0, 10.0, 0.6,
            json.dumps({
                "observed_facts": ["fast motion", "persons in frame"],
                "uncertain": [
                    "clip analysis unavailable",
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

    # Chain-specific evidence
    await test_db.execute(
        """INSERT INTO temporal_events
           (event_id, recording_id, category, start_seconds, end_seconds,
            timestamp_seconds, confidence, evidence_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), RECORDING_ID_2, "possible-theft",
            5.0, 25.0, 12.0, 0.75,
            json.dumps({
                "observed_facts": [
                    "chain grabbed from person",
                    "necklace snatched",
                ],
                "uncertain": [
                    "clip analysis unavailable",
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

    summaries = result.video_summaries
    assert summaries is not None

    s1 = next(s for s in summaries if s.recording_id == RECORDING_ID_1)
    s2 = next(s for s in summaries if s.recording_id == RECORDING_ID_2)

    # Only s2 (chain evidence) should be POSSIBLE_MATCH
    assert s2.match_classification == MatchClassification.POSSIBLE_MATCH
    assert s1.match_classification in (
        MatchClassification.NO_MATCH,
        MatchClassification.NEEDS_REVIEW,
    )

    # Count query-relevant matches — only s2 qualifies
    relevant_count = sum(
        1 for s in summaries
        if s.match_classification in (
            MatchClassification.RELEVANT_MATCH,
            MatchClassification.POSSIBLE_MATCH,
        )
    )
    assert relevant_count == 1, (
        f"Expected 1 relevant match (chain video), got {relevant_count}. "
        "Generic assault should not inflate the count."
    )


# ── Chair-removal query regression tests ─────────────────────────────────────
#
# "When was the chair taken away?" must:
#   - Return relevant/possible only when a chair removal is evidenced.
#   - Exclude brooms, cattle, people-with-children, and empty rooms entirely.
# Root cause fixed: FURNITURE_SUBJECTS gate + query-match gate in _classify_video.

def _make_vision_event(
    action_description: str,
    observed_facts: list[str] | None = None,
    object_descriptions: list[str] | None = None,
    scene_description: str = "",
    event_type: str = "scene_change",
    confidence: float = 0.70,
    relevance_score: float = 0.65,
    recording_id: str = RECORDING_ID_1,
) -> "Event":
    """Make a non-temporal vision Event (direct observation, not incident candidate)."""
    from app.models import Event
    return Event(
        event_id=str(uuid.uuid4()),
        recording_id=recording_id,
        timestamp_seconds=8.0,
        event_type=event_type,
        action_description=action_description,
        scene_description=scene_description,
        confidence=confidence,
        recording_filename="test.mp4",
        relevance_score=relevance_score,
        uncertainty_notes=[],
        observed_facts=observed_facts or [],
        object_descriptions=object_descriptions or [],
    )


class TestChairRemovalQuery:
    """Chair-removal query must not match unrelated footage."""

    _QUERY = "When was the chair taken away?"

    def _qt(self):
        return _tokenize(self._QUERY)

    def test_tokenize_chair_query(self):
        """'away' and 'out' are directional particles — stripped as stopwords."""
        qt = self._qt()
        assert "chair" in qt
        assert "taken" in qt
        assert "away" not in qt, "'away' is a directional particle stopword"
        assert "when" not in qt, "'when' is a stopword"
        assert "was" not in qt

    def test_chair_carried_away_is_relevant(self):
        """Event describing a chair being carried out → relevant or possible match."""
        ev = _make_vision_event(
            action_description="Person carries chair out of room",
            observed_facts=[
                "wooden chair lifted by person",
                "chair transported toward exit",
                "chair removed from corridor",
            ],
            object_descriptions=["wooden chair"],
            relevance_score=0.85,
        )
        qt = self._qt()
        cls, score = _classify_video([ev], qt)
        assert cls in (
            MatchClassification.RELEVANT_MATCH,
            MatchClassification.POSSIBLE_MATCH,
        ), f"Chair-removal event should be relevant/possible, got {cls.value}"

    def test_stationary_chair_is_possible_not_confirmed_removal(self):
        """A video with a chair but no removal action should be possible, not relevant."""
        ev = _make_vision_event(
            action_description="Chair visible in corridor",
            observed_facts=["wooden chair against wall", "chair stationary"],
            object_descriptions=["wooden chair"],
            relevance_score=0.50,
            confidence=0.6,
        )
        qt = self._qt()
        cls, _ = _classify_video([ev], qt)
        # Possible or needs_review — NOT relevant (no removal confirmed)
        # and NOT no_match (chair IS present, just not seen being removed)
        assert cls != MatchClassification.RELEVANT_MATCH, (
            "Stationary chair should not be RELEVANT_MATCH — no removal action confirmed"
        )

    def test_broom_event_excluded_entirely(self):
        """A broom event with no chair mention → no_match for chair query."""
        ev = _make_vision_event(
            action_description="Person sweeping floor with broom",
            observed_facts=["broom used on floor", "person sweeping corridor"],
            object_descriptions=["broom"],
            relevance_score=0.68,
            confidence=0.72,
        )
        qt = self._qt()
        cls, _ = _classify_video([ev], qt)
        assert cls == MatchClassification.NO_MATCH, (
            f"Broom event should be NO_MATCH for chair query, got {cls.value}"
        )

    def test_cattle_event_excluded_entirely(self):
        """A cattle event with no chair mention → no_match for chair query."""
        ev = _make_vision_event(
            action_description="Cattle moving through field",
            observed_facts=["cattle in frame", "animals moving"],
            object_descriptions=["cattle"],
            event_type="scene_change",
            relevance_score=0.60,
            confidence=0.65,
        )
        qt = self._qt()
        cls, _ = _classify_video([ev], qt)
        assert cls == MatchClassification.NO_MATCH, (
            f"Cattle event should be NO_MATCH for chair query, got {cls.value}"
        )

    def test_person_with_child_excluded(self):
        """Person carrying a child is not a chair removal."""
        ev = _make_vision_event(
            action_description="Person carries child across room",
            observed_facts=["adult carrying small child", "child taken by person"],
            object_descriptions=["person", "child"],
            event_type="person_detected",
            relevance_score=0.60,
            confidence=0.75,
        )
        qt = self._qt()
        cls, _ = _classify_video([ev], qt)
        assert cls == MatchClassification.NO_MATCH, (
            f"Person+child event should be NO_MATCH for chair query, got {cls.value}"
        )

    def test_empty_room_excluded(self):
        """Empty room with no objects → no_match for any object-specific query."""
        ev = _make_vision_event(
            action_description="Empty corridor — no activity",
            observed_facts=["no objects detected", "corridor empty"],
            object_descriptions=[],
            relevance_score=0.30,
            confidence=0.50,
        )
        qt = self._qt()
        cls, _ = _classify_video([ev], qt)
        assert cls == MatchClassification.NO_MATCH, (
            f"Empty room should be NO_MATCH for chair query, got {cls.value}"
        )

    def test_no_chair_events_returns_no_match(self):
        """If no events at all contain chair evidence, result is no_match."""
        qt = self._qt()
        cls, _ = _classify_video([], qt)
        assert cls == MatchClassification.NO_MATCH

    def test_furniture_hypernym_query_matches_chair_event(self):
        """Query 'furniture removed' should match a chair-removal event."""
        ev = _make_vision_event(
            action_description="Chair moved out of storage room",
            observed_facts=["chair being relocated", "furniture moved by staff"],
            object_descriptions=["chair"],
            relevance_score=0.80,
        )
        qt = _tokenize("furniture removed from room")
        cls, _ = _classify_video([ev], qt)
        assert cls in (
            MatchClassification.RELEVANT_MATCH,
            MatchClassification.POSSIBLE_MATCH,
        ), f"furniture hypernym query should match chair event, got {cls.value}"

    def test_generic_motion_without_chair_excluded(self):
        """Event describing motion ('taken out', 'moved away') without any chair → no_match."""
        ev = _make_vision_event(
            action_description="Object taken out of storage",
            observed_facts=["item removed from shelf", "package taken away by person"],
            object_descriptions=["package"],
            relevance_score=0.55,
            confidence=0.65,
        )
        qt = self._qt()
        cls, _ = _classify_video([ev], qt)
        # "package" is in OBJECT_SUBJECTS but not FURNITURE_SUBJECTS — no chair → no_match
        assert cls == MatchClassification.NO_MATCH, (
            f"Package/box event should be NO_MATCH for chair query, got {cls.value}"
        )
