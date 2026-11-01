"""Regression tests for the investigation audit findings.

Covers 7 named scenarios derived from the chain-snatch screenshot audit:
1. approach → contact/grab → rapid_departure sequence produces possible-assault
2. stationary person using phone must NOT become the primary incident finding
3. evidence frames must correspond to the event window, not unrelated frames
4. event timestamp must seek to the correct video moment (start_seconds, not mid)
5. uncertainty must be explicit when chain removal is not visually confirmed
6. internal "Candidate …" lines must not surface in observed_facts display
7. normal greeting/passing scene must not produce theft/assault sequences
"""
import json
import uuid
import pytest
import pytest_asyncio

from app.candidate_detector import Candidate, detect_candidates
from app.temporal_correlator import correlate_sequences
from app.temporal_pipeline import _select_evidence_frames
from app.investigate import (
    _incident_categories_for_terms,
    _is_incident_query,
    _temporal_row_to_event_dict,
    _tokenize,
    investigate,
)
from app.database import get_db
from tests.fixtures import (
    RECORDING_ID_1,
    RECORDING_ID_2,
    insert_event,
    insert_observation,
    insert_recording,
    now_iso,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _cand(ctype, start, end, conf=0.7):
    return Candidate(candidate_type=ctype, start_seconds=start,
                     end_seconds=end, confidence=conf)


def _obs(ts, label, scene, score=0.8, frame=None):
    return {
        "observation_id": str(uuid.uuid4()),
        "video_timestamp_seconds": ts,
        "object_label": label,
        "scene_description": scene,
        "detection_score": score,
        "source_frame_path": frame or f"frames/test/frame_{ts:.0f}.jpg",
    }


async def _insert_temporal_event(db, recording_id, category, start, end,
                                  confidence, evidence: dict | None = None):
    eid = str(uuid.uuid4())
    ev = evidence or {
        "event_id": eid,
        "category": category,
        "start_seconds": start,
        "end_seconds": end,
        "timestamp_seconds": (start + end) / 2.0,
        "observed_facts": [f"Observed sequence span {start:.1f}s–{end:.1f}s"],
        "action_sequence": [],
        "observable_actions": [],
        "confirmed": [],
        "uncertain": ["clip analysis unavailable"],
        "evidence_frames": [],
        "tracks": [],
    }
    await db.execute(
        """INSERT OR REPLACE INTO temporal_events(
            event_id, recording_id, category, start_seconds, end_seconds,
            timestamp_seconds, timestamp_source, confidence,
            single_frame_candidate, evidence_json, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (eid, recording_id, category, start, end, (start + end) / 2.0,
         "coarse_grid", confidence, 0, json.dumps(ev), now_iso()))
    await db.commit()
    return eid


# ── 1. Approach → contact → departure sequence ────────────────────────────

class TestApproachContactDepartureSequence:
    """Candidate correlator must produce possible-assault from the three stages."""

    def test_full_snatch_sequence_yields_possible_assault(self):
        candidates = [
            _cand("sudden_approach", 1.0, 1.0, 0.55),
            _cand("contact_confrontation", 9.0, 10.0, 0.72),
            _cand("rapid_departure", 16.0, 18.0, 0.72),
        ]
        seqs = correlate_sequences(candidates, gap_seconds=10.0)
        assert len(seqs) == 1, f"Expected 1 sequence, got {len(seqs)}"
        assert seqs[0].category == "possible-assault", seqs[0].category
        stages = set(seqs[0].stages)
        assert "approach" in stages
        assert "contact" in stages
        assert "departure" in stages

    def test_approach_only_is_not_possible_assault(self):
        candidates = [_cand("sudden_approach", 1.0, 1.0, 0.55)]
        seqs = correlate_sequences(candidates)
        assert all(s.category != "possible-assault" for s in seqs)

    def test_contact_plus_departure_without_approach_is_assault_not_approach_departure(self):
        candidates = [
            _cand("contact_confrontation", 9.0, 10.0, 0.72),
            _cand("rapid_departure", 16.0, 18.0, 0.72),
        ]
        seqs = correlate_sequences(candidates, gap_seconds=10.0)
        assert len(seqs) == 1
        # contact present → possible-assault even without approach
        assert seqs[0].category in ("possible-assault", "confrontation")

    def test_gap_too_large_splits_into_two_sequences(self):
        candidates = [
            _cand("sudden_approach", 1.0, 2.0, 0.6),
            _cand("rapid_departure", 40.0, 41.0, 0.7),
        ]
        seqs = correlate_sequences(candidates, gap_seconds=8.0)
        assert len(seqs) == 2, "Candidates >8s apart must not merge into one sequence"


# ── 2. Stationary phone user must not be primary finding ──────────────────

@pytest.mark.asyncio
class TestStationaryPersonNotPrimaryFinding:
    """A stationary person using a phone must rank below the temporal incident event."""

    async def test_temporal_event_outranks_stationary_person(self, db_path):
        async with get_db(db_path) as db:
            # Recording 1: stationary person using phone (earlier date → sorts first)
            await insert_recording(db, RECORDING_ID_1,
                                   recording_datetime="2024-10-16T07:32:09Z")
            await insert_event(db, RECORDING_ID_1,
                               event_type="person_detected",
                               action_description=(
                                   "standing stationary on left sidewalk in "
                                   "foreground in front of shop, looking at phone, "
                                   "no motion blur, appears stationary"),
                               observed_facts=[
                                   "Observed: green shirt, dark pants person "
                                   "(gold chain, head tilted down holding phone in both hands)"],
                               confidence=0.79,
                               recording_datetime="2024-10-16T07:32:09Z")

            # Recording 2: temporal incident event (later date)
            await insert_recording(db, RECORDING_ID_2,
                                   recording_datetime="2024-10-17T06:17:09Z")
            await _insert_temporal_event(db, RECORDING_ID_2, "possible-assault",
                                         1.0, 18.0, 0.67)

        result = await investigate("chain snatch detected", db_path)
        assert len(result.events) >= 1
        primary = next((e for e in result.events if e.is_primary_finding), None)
        assert primary is not None, "Must have a primary finding"
        assert primary.event_type in ("possible-assault", "possible-theft",
                                      "confrontation"), (
            f"Primary finding must be the incident event, got {primary.event_type!r}")

    async def test_stationary_phone_user_not_flagged_as_incident(self, db_path):
        """A person standing still using a phone must score < 0.5 relevance for chain-snatch."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await insert_event(db, RECORDING_ID_1,
                               event_type="person_detected",
                               action_description=(
                                   "standing stationary, looking at phone"),
                               observed_facts=["Observed: person with phone"],
                               confidence=0.79)
        result = await investigate("chain snatch", db_path)
        person_events = [e for e in result.events
                         if e.event_type == "person_detected"]
        for e in person_events:
            assert (e.relevance_score or 0) < 0.5, (
                f"Stationary phone user relevance {e.relevance_score} >= 0.5 "
                f"for chain-snatch query — false positive")


# ── 3. Evidence frames must correspond to the event window ─────────────────

class TestEvidenceFrameSelection:
    """_select_evidence_frames must pick from within the event window, not outside."""

    def _make_observations(self):
        """Simulate observations for a 27s recording with a contact candidate at t=9-10s."""
        obs = []
        # Before window (t=0): background frame — motorcycle high score
        obs.append(_obs(0.0, "dark motorcycle", "Stationary on street", 0.80, "frames/f0.jpg"))
        # Inside window (t=1-18s): various observations
        obs.append(_obs(1.0, "motorcyclist", "Moving toward foreground", 0.75, "frames/f1.jpg"))
        obs.append(_obs(2.0, "dark motorcycle", "Positioned center-right, moving", 0.85, "frames/f2.jpg"))
        obs.append(_obs(9.0, "person in gray", "arms raised toward motorcycle", 0.70, "frames/f9.jpg"))
        obs.append(_obs(10.0, "person in gray", "beside motorcycle stationary", 0.70, "frames/f10.jpg"))
        obs.append(_obs(16.0, "motorcycle", "moving away from camera", 0.65, "frames/f16.jpg"))
        # After window
        obs.append(_obs(19.0, "person in gray dress", "stationary, holding object", 0.85, "frames/f19.jpg"))
        return obs

    def test_during_frame_is_from_event_window(self):
        obs = self._make_observations()
        frames = _select_evidence_frames(obs, start=1.0, end=18.0,
                                         timestamp_source="coarse_grid")
        during = [f for f in frames if f["role"] == "during"]
        assert len(during) == 1
        ts = during[0]["timestamp_seconds"]
        assert 1.0 <= ts <= 18.0, f"During frame ts={ts} is outside event window [1.0, 18.0]"

    def test_before_frame_is_before_event_window(self):
        obs = self._make_observations()
        frames = _select_evidence_frames(obs, start=1.0, end=18.0,
                                         timestamp_source="coarse_grid")
        before = [f for f in frames if f["role"] == "before"]
        if before:
            assert before[0]["timestamp_seconds"] < 1.0

    def test_after_frame_is_after_event_window(self):
        obs = self._make_observations()
        frames = _select_evidence_frames(obs, start=1.0, end=18.0,
                                         timestamp_source="coarse_grid")
        after = [f for f in frames if f["role"] == "after"]
        if after:
            assert after[0]["timestamp_seconds"] > 18.0

    def test_during_frame_prefers_candidate_peak_over_max_score(self):
        """With candidates supplied, 'during' frame should be near the contact peak."""
        obs = self._make_observations()
        candidates = [
            _cand("sudden_approach", 1.0, 1.0, 0.55),
            _cand("contact_confrontation", 9.0, 10.0, 0.72),  # peak candidate
            _cand("rapid_departure", 16.0, 18.0, 0.72),
        ]
        frames = _select_evidence_frames(obs, start=1.0, end=18.0,
                                         timestamp_source="coarse_grid",
                                         candidates=candidates)
        during = [f for f in frames if f["role"] == "during"]
        assert len(during) == 1
        # The contact_confrontation candidate peaks at 9.0-10.0s.
        # Without candidates, max detection_score picks t=2.0s (motorcycle, 0.85).
        # With candidates, it should pick the frame nearest t=9.5s.
        ts = during[0]["timestamp_seconds"]
        assert 9.0 <= ts <= 10.0, (
            f"During frame ts={ts} should be near contact peak (9-10s), "
            f"not the max-score frame — evidence frame not anchored to incident moment")

    def test_no_observations_returns_empty(self):
        assert _select_evidence_frames([], 1.0, 18.0, "coarse_grid") == []

    def test_all_observations_outside_window_returns_no_during(self):
        obs = [_obs(0.0, "person", "before", 0.9), _obs(20.0, "person", "after", 0.9)]
        frames = _select_evidence_frames(obs, start=1.0, end=18.0,
                                         timestamp_source="coarse_grid")
        during = [f for f in frames if f["role"] == "during"]
        assert during == []


# ── 4. Event timestamp seeks to correct video moment ──────────────────────

@pytest.mark.asyncio
class TestTimestampSeekAccuracy:
    """start_seconds is what the video Preview button seeks to — must match the event window start."""

    async def test_temporal_event_start_seconds_preserved(self, db_path):
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1,
                                   recording_datetime="2024-10-17T06:17:09Z")
            await _insert_temporal_event(db, RECORDING_ID_1, "possible-assault",
                                         1.0, 18.0, 0.67)

        result = await investigate("chain snatch", db_path)
        incident = next((e for e in result.events
                         if e.event_type == "possible-assault"), None)
        assert incident is not None
        assert incident.start_seconds == 1.0, (
            f"start_seconds={incident.start_seconds} — Preview button would seek "
            f"to wrong moment in the video")
        assert incident.end_seconds == 18.0

    async def test_timestamp_seconds_is_midpoint_of_span(self, db_path):
        """timestamp_seconds (displayed in UI) should be the midpoint of start/end."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1)
            await _insert_temporal_event(db, RECORDING_ID_1, "possible-assault",
                                         4.0, 14.0, 0.7)
        result = await investigate("assault", db_path)
        incident = next((e for e in result.events
                         if e.event_type in ("possible-assault", "confrontation")), None)
        assert incident is not None
        assert abs(incident.timestamp_seconds - 9.0) < 0.5, (
            f"timestamp_seconds={incident.timestamp_seconds}, expected ~9.0 "
            f"(midpoint of 4–14s)")


# ── 5. Explicit uncertainty when chain removal unconfirmed ─────────────────

class TestExplicitTransferUncertainty:
    """When clip analysis unavailable, possible-theft/assault must carry an explicit note
    that the object transfer was not visually confirmed."""

    def _make_row(self, category, clip_msg="clip analysis unavailable"):
        return {
            "event_id": str(uuid.uuid4()),
            "recording_id": RECORDING_ID_1,
            "category": category,
            "start_seconds": 1.0,
            "end_seconds": 18.0,
            "timestamp_seconds": 9.5,
            "confidence": 0.67,
            "evidence_json": json.dumps({
                "action_sequence": [],
                "observable_actions": [],
                "observed_facts": ["Observed sequence span 1.0s–18.0s"],
                "confirmed": [],
                "uncertain": [clip_msg],
                "evidence_frames": [],
                "tracks": [],
            }),
        }

    def test_possible_theft_with_no_clip_gets_transfer_note(self):
        row = self._make_row("possible-theft")
        result = _temporal_row_to_event_dict(row, {RECORDING_ID_1: {}})
        assert result is not None
        notes = result["uncertainty_notes"]
        assert any("transfer" in n.lower() or "chain" in n.lower() or "removal" in n.lower()
                   for n in notes), (
            f"No transfer-uncertainty note found in: {notes}")

    def test_possible_assault_with_no_clip_gets_transfer_note(self):
        row = self._make_row("possible-assault")
        result = _temporal_row_to_event_dict(row, {RECORDING_ID_1: {}})
        assert result is not None
        notes = result["uncertainty_notes"]
        assert any("transfer" in n.lower() or "visually confirmed" in n.lower()
                   for n in notes), f"No transfer note: {notes}"

    def test_possible_assault_with_clip_analysis_does_not_get_transfer_note(self):
        """When clip analysis succeeded, the transfer note must NOT appear."""
        row = self._make_row("possible-assault", clip_msg="person reached toward victim")
        result = _temporal_row_to_event_dict(row, {RECORDING_ID_1: {}})
        notes = result["uncertainty_notes"]
        assert not any("Object transfer" in n for n in notes), (
            f"Spurious transfer note when clip analysis was available: {notes}")

    def test_sudden_fall_does_not_get_transfer_note(self):
        """Non-theft categories must not get the chain-transfer note."""
        row = self._make_row("sudden-fall")
        result = _temporal_row_to_event_dict(row, {RECORDING_ID_1: {}})
        notes = result["uncertainty_notes"]
        assert not any("Object transfer" in n for n in notes), (
            f"sudden-fall wrongly got transfer note: {notes}")


# ── 6. Internal Candidate lines filtered from UI-facing observed_facts ─────

class TestCandidateLinesFiltered:
    """_temporal_row_to_event_dict must not surface 'Candidate …' lines in primary_match."""

    def test_primary_match_is_not_candidate_line(self):
        row = {
            "event_id": str(uuid.uuid4()),
            "recording_id": RECORDING_ID_1,
            "category": "possible-assault",
            "start_seconds": 1.0,
            "end_seconds": 18.0,
            "timestamp_seconds": 9.5,
            "confidence": 0.67,
            "evidence_json": json.dumps({
                "action_sequence": [],
                "observable_actions": [],
                "observed_facts": [
                    "Observed sequence span 1.0s–18.0s",
                    "Candidate sudden_approach at 1.0s–1.0s (confidence 0.55)",
                    "Candidate contact_confrontation at 9.0s–10.0s (confidence 0.72)",
                    "Candidate rapid_departure at 16.0s–18.0s (confidence 0.72)",
                ],
                "confirmed": [],
                "uncertain": ["clip analysis unavailable"],
                "evidence_frames": [],
                "tracks": [],
            }),
        }
        result = _temporal_row_to_event_dict(row, {RECORDING_ID_1: {}})
        assert result is not None
        primary = result.get("primary_match")
        assert primary is None or not primary.startswith("Candidate "), (
            f"primary_match is an internal candidate line: {primary!r}")

    def test_action_description_is_not_candidate_line(self):
        row = {
            "event_id": str(uuid.uuid4()),
            "recording_id": RECORDING_ID_1,
            "category": "possible-assault",
            "start_seconds": 1.0,
            "end_seconds": 18.0,
            "timestamp_seconds": 9.5,
            "confidence": 0.67,
            "evidence_json": json.dumps({
                "action_sequence": [],
                "observable_actions": [],
                "observed_facts": [
                    "Candidate sudden_approach at 1.0s–1.0s (confidence 0.55)",
                ],
                "confirmed": [],
                "uncertain": ["clip analysis unavailable"],
                "evidence_frames": [],
                "tracks": [],
            }),
        }
        result = _temporal_row_to_event_dict(row, {RECORDING_ID_1: {}})
        ad = result["action_description"]
        assert not ad.startswith("Candidate "), (
            f"action_description is an internal candidate label: {ad!r}")

    def test_action_description_uses_observable_actions_when_available(self):
        row = {
            "event_id": str(uuid.uuid4()),
            "recording_id": RECORDING_ID_1,
            "category": "possible-assault",
            "start_seconds": 9.0,
            "end_seconds": 10.0,
            "timestamp_seconds": 9.5,
            "confidence": 0.72,
            "evidence_json": json.dumps({
                "action_sequence": [],
                "observable_actions": ["person raised arms toward motorcycle"],
                "observed_facts": [
                    "Observed sequence span 9.0s–10.0s",
                    "Candidate contact_confrontation at 9.0s–10.0s (confidence 0.72)",
                ],
                "confirmed": [],
                "uncertain": ["clip analysis unavailable"],
                "evidence_frames": [],
                "tracks": [],
            }),
        }
        result = _temporal_row_to_event_dict(row, {RECORDING_ID_1: {}})
        assert "raised" in result["action_description"] or "motorcycle" in result["action_description"], (
            f"observable_actions not used: {result['action_description']!r}")


# ── 7. Normal scene must not produce assault/theft sequences ───────────────

class TestNormalSceneNoFalsePositive:
    """Neutral pedestrian observations must not produce theft/assault candidates."""

    def test_normal_pedestrian_walking_produces_no_theft(self):
        obs = [
            _obs(1.0, "person", "walking along the sidewalk normally"),
            _obs(2.0, "person", "continuing to walk away from camera"),
            _obs(3.0, "person", "passing another pedestrian without interaction"),
        ]
        candidates = detect_candidates(obs)
        seqs = correlate_sequences(candidates)
        bad_cats = {"possible-theft", "possible-assault"}
        for seq in seqs:
            assert seq.category not in bad_cats, (
                f"Normal pedestrian walk produced {seq.category} — false positive")

    def test_two_people_talking_is_not_assault(self):
        obs = [
            _obs(1.0, "person A", "standing and talking to another person calmly"),
            _obs(2.0, "person B", "facing person A, gesturing normally"),
        ]
        candidates = detect_candidates(obs)
        seqs = correlate_sequences(candidates)
        for seq in seqs:
            assert seq.category not in {"possible-assault", "possible-theft"}, (
                f"Two people talking produced {seq.category}")

    def test_walking_away_alone_is_not_rapid_departure_assault(self):
        obs = [_obs(1.0, "person", "walking away from camera down the street")]
        candidates = detect_candidates(obs)
        seqs = correlate_sequences(candidates)
        for seq in seqs:
            assert seq.category not in {"possible-assault", "possible-theft"}, (
                f"Person walking away produced {seq.category}")

    def test_motorcycle_moving_toward_then_away_is_suspicious_not_assault(self):
        """A motorcycle passing through (toward + away) → suspicious-approach-departure,
        NOT possible-assault. Assault requires contact evidence."""
        obs = [
            _obs(1.0, "motorcycle", "moving toward foreground"),
            _obs(3.0, "motorcycle", "moving away from camera"),
        ]
        candidates = detect_candidates(obs)
        seqs = correlate_sequences(candidates, gap_seconds=8.0)
        for seq in seqs:
            assert seq.category != "possible-assault", (
                f"Motorcycle pass-through must not produce possible-assault; got {seq.category}")


# ── Integration: full investigate() chain ─────────────────────────────────

@pytest.mark.asyncio
class TestInvestigateChainSnatchIntegration:
    """End-to-end: stored temporal event surfaces correctly for chain-snatch query."""

    async def test_chain_snatch_investigation_surfaces_incident_event(self, db_path):
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1,
                                   recording_datetime="2024-10-17T06:17:09Z")
            await _insert_temporal_event(db, RECORDING_ID_1, "possible-assault",
                                         1.0, 18.0, 0.67)
        result = await investigate("chain snatch detected", db_path)
        assert len(result.events) >= 1
        incident = next((e for e in result.events
                         if e.event_type in ("possible-assault", "possible-theft")), None)
        assert incident is not None
        assert incident.start_seconds == 1.0
        assert incident.end_seconds == 18.0
        assert any("not a confirmed crime" in n for n in incident.uncertainty_notes)

    async def test_investigation_summary_does_not_start_with_candidate(self, db_path):
        """Incident Summary text must not start with 'Candidate <type>'."""
        async with get_db(db_path) as db:
            await insert_recording(db, RECORDING_ID_1,
                                   recording_datetime="2024-10-17T06:17:09Z")
            await _insert_temporal_event(db, RECORDING_ID_1, "possible-assault",
                                         1.0, 18.0, 0.67)
        result = await investigate("chain snatch", db_path)
        assert result.summary is not None
        assert not result.summary.startswith("Best match: Candidate "), (
            f"Incident Summary starts with internal candidate label: {result.summary!r}")
