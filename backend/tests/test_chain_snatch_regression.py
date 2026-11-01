"""Regression tests for false-positive chain-snatch classification.

These tests document the exact real-world failures:
- Arrest019_x264.mp4: 14 noisy candidates, no chain evidence → NO_MATCH
- videoplayback(1).mp4: clean 3-stage sequence, no chain text → NEEDS_REVIEW
- Chain/necklace text evidence → POSSIBLE_MATCH
- Traffic/crowd footage → NO_MATCH

Also covers: zero-coverage guard (confidence alone must not pass the gate),
and the clean-sequence threshold (_THEFT_SEQ_MAX_CANDIDATES).
"""
import uuid
import pytest

from app.models import Event, MatchClassification
from app.investigate import (
    _classify_video,
    _has_clean_theft_sequence,
    _query_match_score_for_event,
    _tokenize,
    _THEFT_SEQ_MAX_CANDIDATES,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ev(
    facts: list[str],
    event_type: str = "possible-assault",
    conf: float = 0.65,
    action: str = "Observed activity in possible-assault window",
) -> Event:
    return Event(
        event_id=str(uuid.uuid4()),
        recording_id="rec-1",
        timestamp_seconds=10.0,
        event_type=event_type,
        action_description=action,
        confidence=conf,
        recording_filename="test.mp4",
        relevance_score=0.88,
        uncertainty_notes=[
            "clip analysis unavailable",
            "Incident category is an observation-only candidate, not a confirmed crime classification",
        ],
        observed_facts=facts,
    )


_CHAIN_SNATCH_QT = _tokenize("chain snatch")

# ── _has_clean_theft_sequence ──────────────────────────────────────────────────

class TestHasCleanTheftSequence:
    def test_clean_3stage_sequential(self):
        ev = _ev([
            "Observed sequence span 1.0s-18.0s",
            "Candidate sudden_approach at 1.0s-1.0s (confidence 0.55)",
            "Candidate contact_confrontation at 9.0s-10.0s (confidence 0.72)",
            "Candidate rapid_departure at 16.0s-18.0s (confidence 0.72)",
        ])
        assert _has_clean_theft_sequence(ev) is True

    def test_too_many_candidates_fails(self):
        """More than _THEFT_SEQ_MAX_CANDIDATES candidates → not a clean sequence."""
        facts = [
            "Candidate sudden_approach at 1.0s-1.0s (confidence 0.55)",
            "Candidate contact_confrontation at 9.0s-9.0s (confidence 0.55)",
            "Candidate rapid_departure at 16.0s-16.0s (confidence 0.55)",
        ] + [
            f"Candidate rapid_departure at {20+i}.0s-{20+i}.0s (confidence 0.55)"
            for i in range(_THEFT_SEQ_MAX_CANDIDATES)
        ]
        ev = _ev(facts)
        assert _has_clean_theft_sequence(ev) is False

    def test_wrong_order_fails(self):
        """Departure before approach is not a theft sequence."""
        ev = _ev([
            "Candidate rapid_departure at 1.0s-1.0s (confidence 0.82)",  # departure first
            "Candidate sudden_approach at 5.0s-5.0s (confidence 0.55)",
            "Candidate contact_confrontation at 9.0s-9.0s (confidence 0.55)",
        ])
        assert _has_clean_theft_sequence(ev) is False

    def test_missing_contact_fails(self):
        ev = _ev([
            "Candidate sudden_approach at 1.0s-1.0s (confidence 0.55)",
            "Candidate rapid_departure at 16.0s-16.0s (confidence 0.72)",
        ])
        assert _has_clean_theft_sequence(ev) is False

    def test_no_candidates_fails(self):
        ev = _ev(["some observed fact"])
        assert _has_clean_theft_sequence(ev) is False

    def test_arrest019_pattern_fails(self):
        """Arrest019 has 14 candidates — fails the count ceiling."""
        facts = [
            "Observed sequence span 0.0s-42.0s",
            "Candidate rapid_departure at 0.0s-3.0s (confidence 0.82)",
            "Candidate sudden_approach at 1.0s-1.0s (confidence 0.55)",
            "Candidate sudden_approach at 7.0s-21.0s (confidence 0.88)",
            "Candidate contact_confrontation at 11.0s-11.0s (confidence 0.55)",
            "Candidate stumble_fall at 18.0s-18.0s (confidence 0.55)",
            "Candidate rapid_departure at 21.0s-21.0s (confidence 0.55)",
            "Candidate stumble_fall at 21.0s-21.0s (confidence 0.55)",
            "Candidate contact_confrontation at 22.0s-22.0s (confidence 0.55)",
            "Candidate rapid_departure at 27.0s-27.0s (confidence 0.82)",
            "Candidate sudden_approach at 27.0s-27.0s (confidence 0.82)",
            "Candidate rapid_departure at 32.0s-32.0s (confidence 0.55)",
            "Candidate sudden_approach at 32.0s-32.0s (confidence 0.55)",
            "Candidate sudden_approach at 36.0s-36.0s (confidence 0.82)",
            "Candidate stumble_fall at 42.0s-42.0s (confidence 0.55)",
        ]
        ev = _ev(facts, conf=0.639)
        assert _has_clean_theft_sequence(ev) is False


# ── _query_match_score_for_event: zero-coverage guard ─────────────────────────

class TestZeroCoverageGuard:
    def test_candidate_only_facts_score_zero(self):
        """Events with only Candidate/span lines get zero score (no term evidence)."""
        ev = _ev([
            "Observed sequence span 0.0s-42.0s",
            "Candidate rapid_departure at 0.0s-3.0s (confidence 0.82)",
            "Candidate sudden_approach at 7.0s-21.0s (confidence 0.88)",
            "Candidate contact_confrontation at 11.0s-11.0s (confidence 0.55)",
        ], conf=0.639)
        score = _query_match_score_for_event(_CHAIN_SNATCH_QT, ev)
        assert score == 0.0, (
            f"Expected 0.0 (no term coverage), got {score}. "
            "Confidence must not leak through when coverage is zero."
        )

    def test_high_confidence_alone_does_not_pass_gate(self):
        """A high-confidence event with zero term coverage must score 0.0."""
        ev = _ev([
            "Candidate rapid_departure at 0.0s-3.0s (confidence 0.99)",
            "Candidate sudden_approach at 5.0s-5.0s (confidence 0.99)",
        ], conf=0.99)
        score = _query_match_score_for_event(_CHAIN_SNATCH_QT, ev)
        assert score == 0.0

    def test_chain_text_produces_nonzero_score(self):
        """Observed facts containing 'chain' yield a positive score."""
        ev = _ev(["person grabs chain from victim"], conf=0.7)
        score = _query_match_score_for_event(_CHAIN_SNATCH_QT, ev)
        assert score > 0.0

    def test_candidate_lines_stripped_before_scoring(self):
        """Candidate bookkeeping lines must not contribute to coverage score."""
        # Even with high sub-confidence numbers that happen to contain digits,
        # the query terms chain/snatch don't appear → zero coverage → zero score.
        ev = _ev([
            "Candidate contact_confrontation at 9.0s-9.0s (confidence 0.99)",
            "Candidate sudden_approach at 1.0s-1.0s (confidence 0.99)",
        ], conf=0.9)
        score = _query_match_score_for_event(_CHAIN_SNATCH_QT, ev)
        assert score == 0.0


# ── _classify_video: the four key cases from the spec ─────────────────────────

class TestClassifyVideoRegressions:

    def test_arrest019_noisy_candidates_is_no_match(self):
        """Chain snatch + 14 noisy candidates (Arrest019 pattern) → no_match."""
        ev = _ev([
            "Observed sequence span 0.0s-42.0s",
            "Candidate rapid_departure at 0.0s-3.0s (confidence 0.82)",
            "Candidate sudden_approach at 1.0s-1.0s (confidence 0.55)",
            "Candidate sudden_approach at 7.0s-21.0s (confidence 0.88)",
            "Candidate contact_confrontation at 11.0s-11.0s (confidence 0.55)",
            "Candidate stumble_fall at 18.0s-18.0s (confidence 0.55)",
            "Candidate rapid_departure at 21.0s-21.0s (confidence 0.55)",
            "Candidate stumble_fall at 21.0s-21.0s (confidence 0.55)",
            "Candidate contact_confrontation at 22.0s-22.0s (confidence 0.55)",
            "Candidate rapid_departure at 27.0s-27.0s (confidence 0.82)",
            "Candidate sudden_approach at 27.0s-27.0s (confidence 0.82)",
            "Candidate rapid_departure at 32.0s-32.0s (confidence 0.55)",
            "Candidate sudden_approach at 32.0s-32.0s (confidence 0.55)",
            "Candidate sudden_approach at 36.0s-36.0s (confidence 0.82)",
            "Candidate stumble_fall at 42.0s-42.0s (confidence 0.55)",
        ], conf=0.639)
        cls, _ = _classify_video([ev], _CHAIN_SNATCH_QT)
        assert cls == MatchClassification.NO_MATCH, (
            f"Arrest019-pattern (14 noisy candidates) classified as {cls.value}, "
            "expected no_match for chain snatch query."
        )

    def test_vp1_clean_sequence_is_needs_review(self):
        """Chain snatch + clean A→C→D sequence (vp1 pattern) → needs_review."""
        ev = _ev([
            "Observed sequence span 1.0s-18.0s",
            "Candidate sudden_approach at 1.0s-1.0s (confidence 0.55)",
            "Candidate contact_confrontation at 9.0s-10.0s (confidence 0.72)",
            "Candidate rapid_departure at 16.0s-18.0s (confidence 0.72)",
        ], conf=0.667)
        cls, _ = _classify_video([ev], _CHAIN_SNATCH_QT)
        assert cls == MatchClassification.NEEDS_REVIEW, (
            f"Clean 3-stage sequence (vp1 pattern) classified as {cls.value}, "
            "expected needs_review for chain snatch query."
        )

    def test_chain_necklace_text_is_possible_match(self):
        """Chain snatch + clear necklace/chain evidence → possible_match."""
        ev = _ev(
            ["person grabs chain from victim neck", "necklace snatched away"],
            conf=0.7,
        )
        cls, _ = _classify_video([ev], _CHAIN_SNATCH_QT)
        assert cls == MatchClassification.POSSIBLE_MATCH

    def test_traffic_crowd_only_approach_is_no_match(self):
        """Chain snatch + single approach in traffic context → no_match."""
        ev = _ev([
            "Observed sequence span 5.0s-30.0s",
            "Candidate sudden_approach at 5.0s-5.0s (confidence 0.55)",
        ], conf=0.55)
        cls, _ = _classify_video([ev], _CHAIN_SNATCH_QT)
        assert cls == MatchClassification.NO_MATCH

    def test_generic_assault_label_no_chain_text_is_no_match(self):
        """Generic assault detection with no chain/necklace text → no_match."""
        ev = _ev(
            ["fast motion", "person approached another"],
            event_type="possible-assault",
            conf=0.6,
        )
        cls, _ = _classify_video([ev], _CHAIN_SNATCH_QT)
        assert cls in (MatchClassification.NO_MATCH, MatchClassification.NEEDS_REVIEW)
        assert cls != MatchClassification.POSSIBLE_MATCH

    def test_shoplifting_query_traffic_is_no_match(self):
        """Shoplifting query + traffic/approach candidates → no_match."""
        qt = _tokenize("shoplifting")
        ev = _ev([
            "Candidate sudden_approach at 1.0s-1.0s (confidence 0.55)",
            "Candidate rapid_departure at 5.0s-5.0s (confidence 0.55)",
        ], conf=0.55)
        cls, _ = _classify_video([ev], qt)
        assert cls == MatchClassification.NO_MATCH

    def test_incident_candidate_never_relevant_match(self):
        """Incident candidates are observation-only — never relevant_match."""
        ev = _ev(
            ["chain grabbed", "necklace snatched", "person flees with chain"],
            event_type="possible-theft",
            conf=0.95,
        )
        cls, _ = _classify_video([ev], _CHAIN_SNATCH_QT)
        assert cls != MatchClassification.RELEVANT_MATCH
