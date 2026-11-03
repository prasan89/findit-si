"""Regression tests for action-query synonym expansion.

Covers the bug where "How did the person approach?" and "How did the person escape?"
returned irrelevant results because:
  1. _ACTION_SYNONYMS had no entries for approach/escape/flee/snatch/grab.
  2. _score_event() used exact word-boundary matching (term_matches) which
     required "approach" to match literally, missing "approaches"/"approaching".
  3. _ACTION_QUERY_TERMS was missing these verbs.

Each test asserts that _score_event gives a positive score when the event
description uses inflected/synonymous forms of the query verb.
"""
import uuid
import pytest

from app.investigate import (
    _score_event,
    _action_synonyms_for_terms,
    _action_confirmed_in_headline,
    _tokenize,
    _ACTION_QUERY_TERMS,
    INCIDENT_CATEGORY_MAP,
    _incident_categories_for_terms,
)


def _ev(action: str, facts: list[str] | None = None, scene: str = "") -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "recording_id": "rec-1",
        "timestamp_seconds": 5.0,
        "event_type": "possible-assault",
        "action_description": action,
        "scene_description": scene,
        "object_descriptions": [],
        "observed_facts": facts or [],
        "inferred_claims": [],
        "uncertainty_notes": [],
        "confidence": 0.75,
        "entities": [],
        "action_sequence": [],
    }


class TestApproachSynonyms:
    def test_approaches_matches_approach_query(self):
        ev = _ev("person approaches the victim from behind")
        score = _score_event(["person", "approach"], ev)
        assert score > 0.0, "approach synonym 'approaches' should match"

    def test_approaching_matches_approach_query(self):
        ev = _ev("a man is approaching on a motorcycle")
        score = _score_event(["approach"], ev)
        assert score > 0.0

    def test_approached_matches_approach_query(self):
        ev = _ev("suspect approached the pedestrian from the left")
        score = _score_event(["approach"], ev)
        assert score > 0.0

    def test_moves_toward_matches_approach_query(self):
        ev = _ev("the motorcyclist moves toward the woman")
        score = _score_event(["approach"], ev)
        assert score > 0.0

    def test_unrelated_event_scores_zero_for_approach(self):
        ev = _ev("a dog sits quietly in the corner")
        score = _score_event(["person", "approach"], ev)
        assert score == 0.0, "unrelated event must not match person+approach query"


class TestEscapeSynonyms:
    def test_flees_matches_escape_query(self):
        ev = _ev("the suspect flees the scene on a motorcycle")
        score = _score_event(["escape"], ev)
        assert score > 0.0

    def test_fleeing_matches_flee_query(self):
        ev = _ev("motorcyclist fleeing after the incident")
        score = _score_event(["flee"], ev)
        assert score > 0.0

    def test_fled_matches_escape_query(self):
        ev = _ev("person fled down the alley")
        score = _score_event(["escape"], ev)
        assert score > 0.0

    def test_drives_away_matches_escape_query(self):
        ev = _ev("motorcycle drives away quickly")
        score = _score_event(["escape"], ev)
        assert score > 0.0

    def test_speeds_off_matches_escape_query(self):
        ev = _ev("the vehicle speeds off after the grab")
        score = _score_event(["escape"], ev)
        assert score > 0.0

    def test_unrelated_stays_still_scores_low(self):
        # "person" hits but "escape" doesn't — score is partial (1/2 terms)
        # This is NEEDS_REVIEW territory, not a confident match.
        ev = _ev("a person stands and waits at the bus stop")
        score = _score_event(["person", "escape"], ev)
        assert score < 0.65, "no escape evidence should keep score below RELEVANT_MATCH threshold"


class TestSnatchGrabSynonyms:
    def test_snatches_matches_snatch_query(self):
        ev = _ev("suspect snatches the gold chain from the woman's neck")
        score = _score_event(["snatch"], ev)
        assert score > 0.0

    def test_grabbed_matches_grab_query(self):
        ev = _ev("attacker grabbed the bag and ran")
        score = _score_event(["grab"], ev)
        assert score > 0.0

    def test_snatching_matches_snatch_query(self):
        ev = _ev("snatching motion captured at frame 12")
        score = _score_event(["snatch"], ev)
        assert score > 0.0

    def test_rips_matches_snatch_query(self):
        ev = _ev("one person rips the necklace away")
        score = _score_event(["snatch"], ev)
        assert score > 0.0


class TestFollowPursueSynonyms:
    def test_following_matches_follow_query(self):
        ev = _ev("the motorcycle is following the pedestrian for 30 seconds")
        score = _score_event(["follow"], ev)
        assert score > 0.0

    def test_chasing_matches_follow_query(self):
        ev = _ev("vehicle chasing a person on foot")
        score = _score_event(["follow"], ev)
        assert score > 0.0


class TestActionQueryTermsRegistration:
    def test_approach_in_action_query_terms(self):
        assert "approach" in _ACTION_QUERY_TERMS

    def test_escape_in_action_query_terms(self):
        assert "escape" in _ACTION_QUERY_TERMS

    def test_flee_in_action_query_terms(self):
        assert "flee" in _ACTION_QUERY_TERMS

    def test_snatch_in_action_query_terms(self):
        assert "snatch" in _ACTION_QUERY_TERMS

    def test_grab_in_action_query_terms(self):
        assert "grab" in _ACTION_QUERY_TERMS


class TestActionConfirmedInHeadline:
    def test_approaches_confirms_approach_action(self):
        synonyms = _action_synonyms_for_terms(["approach"])
        assert _action_confirmed_in_headline("the motorcyclist approaches the victim", synonyms)

    def test_fleeing_confirms_escape_action(self):
        synonyms = _action_synonyms_for_terms(["escape"])
        assert _action_confirmed_in_headline("suspect fleeing on motorcycle", synonyms)

    def test_snatches_confirms_snatch_action(self):
        synonyms = _action_synonyms_for_terms(["snatch"])
        assert _action_confirmed_in_headline("suspect snatches the chain", synonyms)


class TestPersonSubjectExpansion:
    """Narrative steps from direct-video analysis use role words not in legacy PERSON_SUBJECTS."""

    def test_rider_satisfies_person_query(self):
        ev = _ev("rider approaches on a motorcycle")
        score = _score_event(["person", "approach"], ev)
        assert score > 0.0, "'rider' should satisfy the person subject gate"

    def test_victim_satisfies_person_query(self):
        ev = _ev("the victim is snatched from behind")
        score = _score_event(["person", "snatch"], ev)
        assert score > 0.0, "'victim' should satisfy the person subject gate"

    def test_suspect_satisfies_person_query(self):
        ev = _ev("suspect flees after the incident")
        score = _score_event(["person", "escape"], ev)
        assert score > 0.0, "'suspect' should satisfy the person subject gate"

    def test_passenger_satisfies_person_query(self):
        ev = _ev("passenger reaches out and grabs the chain")
        score = _score_event(["person", "grab"], ev)
        assert score > 0.0, "'passenger' should satisfy the person subject gate"

    def test_pedestrian_satisfies_person_query(self):
        ev = _ev("a pedestrian approaches the intersection")
        score = _score_event(["person", "approach"], ev)
        assert score > 0.0, "'pedestrian' should satisfy the person subject gate"
    def test_approach_surfaces_suspicious_approach_departure(self):
        cats = _incident_categories_for_terms(["person", "approach"])
        assert "suspicious-approach-departure" in cats

    def test_escape_surfaces_suspicious_approach_departure(self):
        cats = _incident_categories_for_terms(["escape", "motorcycle"])
        assert "suspicious-approach-departure" in cats

    def test_flee_surfaces_suspicious_approach_departure(self):
        cats = _incident_categories_for_terms(["flee"])
        assert "suspicious-approach-departure" in cats
