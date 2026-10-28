"""Phase 4 visual-intelligence regression tests — deterministic, no live API.

Covers the three evaluation-driven fixes:
1. Cross-camera temporal-only gate (see also test_phase5_eval.py regressions).
2. Temporal query handling (between/before/after/first/last as pure filters).
3. Multi-frame confirmation + persisted event spans + evidence validation.

API keys are stripped for every test here by the autouse fixture in
conftest.py, so LLM helpers always take the mocked path (no network).
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from eval import metrics as M  # noqa: E402
from app.agents.base import AgentStatus  # noqa: E402
from app.correlator import _correlation_score  # noqa: E402
from app.database import get_db  # noqa: E402
from app.event_extractor import extract_events_for_recording  # noqa: E402
from app.investigate import (_parse_temporal_query, _tokenize, investigate,  # noqa: E402
                             validate_evidence_frames)
from app.meta_client import ANALYSIS_PROMPT  # noqa: E402
from app.processor import _parse_observations  # noqa: E402
from tests.fixtures import (RECORDING_ID_1, RECORDING_ID_2, insert_event,  # noqa: E402
                            insert_observation, insert_recording)


# ── Fix 1: cross-camera temporal-only gate ─────────────────────────────────

class TestTemporalOnlyGate:
    def test_temporal_alone_never_links(self):
        score, expl = _correlation_score(
            {"abs_ts": 1000.0, "object_descriptions": ["red person figure"],
             "event_type": "person_detected"},
            {"abs_ts": 1001.0, "object_descriptions": ["green vehicle"],
             "event_type": "vehicle_detected"})
        # 1s apart would score ~0.5 on time alone; the gate must block it.
        assert score == 0.0
        assert "insufficient" in expl

    def test_same_type_can_still_link(self):
        score, _ = _correlation_score(
            {"abs_ts": 1000.0, "object_descriptions": ["red car"],
             "event_type": "vehicle_detected"},
            {"abs_ts": 1060.0, "object_descriptions": ["blue truck"],
             "event_type": "vehicle_detected"})
        assert score >= 0.3  # temporal + same-type agreement

    def test_shared_appearance_can_still_link(self):
        score, _ = _correlation_score(
            {"abs_ts": 1000.0, "object_descriptions": ["red backpack person"],
             "event_type": "person_detected"},
            {"abs_ts": 1120.0, "object_descriptions": ["red backpack"],
             "event_type": "unattended_object"})
        assert score >= 0.3  # temporal + appearance overlap

    def test_far_apart_never_links(self):
        score, expl = _correlation_score(
            {"abs_ts": 1000.0, "object_descriptions": ["red car"],
             "event_type": "vehicle_detected"},
            {"abs_ts": 5000.0, "object_descriptions": ["red car"],
             "event_type": "vehicle_detected"})
        assert score == 0.0
        assert "Too far apart" in expl


# ── Fix 2: temporal queries ──────────────────────────────────────────────

class TestTemporalParsing:
    def test_between(self):
        r = _parse_temporal_query("show all events between 00:00 and 00:15")
        assert (r["start"], r["end"]) == (0, 15)
        assert "select" not in r

    def test_before(self):
        r = _parse_temporal_query("events before 01:30")
        assert r == {"end": 90}

    def test_after(self):
        r = _parse_temporal_query("what happened after 00:06?")
        assert r == {"start": 6}

    def test_first_and_last(self):
        assert _parse_temporal_query("show the first event") == {"select": "first"}
        assert _parse_temporal_query("show the last event") == {"select": "last"}

    def test_plain_query_parses_empty(self):
        assert _parse_temporal_query("red person at entrance") == {}
        assert _parse_temporal_query("Where was the red bag?") == {}

    def test_event_relative_before_not_misparsed(self):
        # "before the person left" has no clock time -> no time bounds.
        r = _parse_temporal_query("what happened before the person left?")
        assert "start" not in r and "end" not in r

    def test_generic_words_are_stopwords(self):
        assert _tokenize("show all events between 00:00 and 00:15") == ["00", "00", "00", "15"]
        assert _tokenize("show the first event") == []
        assert _tokenize("show the last event") == []


@pytest.mark.asyncio
class TestTemporalRetrieval:
    async def _seed(self, db):
        import aiosqlite
        db.row_factory = aiosqlite.Row
        await insert_recording(db, RECORDING_ID_1, camera_name="Cam 1",
                               recording_datetime="2024-01-15T10:00:00")
        await insert_recording(db, RECORDING_ID_2, camera_name="Cam 2",
                               recording_datetime="2024-01-15T10:02:00")
        await insert_event(db, RECORDING_ID_1, event_id="e-early",
                           timestamp_seconds=5.5, action_description="red figure",
                           object_descriptions=["red figure"],
                           observed_facts=["Observed: red figure"])
        await insert_event(db, RECORDING_ID_2, event_id="e-late",
                           timestamp_seconds=8.5, action_description="blue bag",
                           object_descriptions=["blue bag"],
                           observed_facts=["Observed: blue bag"])

    async def test_between_is_pure_filter(self, db_path):
        async with get_db(db_path) as db:
            await self._seed(db)
        result = await investigate("show all events between 00:00 and 00:06", db_path)
        assert [e.event_id for e in result.events] == ["e-early"]

    async def test_before_and_after(self, db_path):
        async with get_db(db_path) as db:
            await self._seed(db)
        before = await investigate("show all events before 00:06", db_path)
        assert [e.event_id for e in before.events] == ["e-early"]
        after = await investigate("show all events after 00:06", db_path)
        assert [e.event_id for e in after.events] == ["e-late"]

    async def test_first_last_use_absolute_order(self, db_path):
        # e-late has the higher video-relative ts but e-early is absolutely first.
        async with get_db(db_path) as db:
            await self._seed(db)
        first = await investigate("show the first event", db_path)
        assert [e.event_id for e in first.events] == ["e-early"]
        last = await investigate("show the last event", db_path)
        assert [e.event_id for e in last.events] == ["e-late"]

    async def test_content_plus_temporal_combines(self, db_path):
        async with get_db(db_path) as db:
            await self._seed(db)
        result = await investigate("red figure after 00:04", db_path)
        assert [e.event_id for e in result.events] == ["e-early"]
        excluded = await investigate("red figure after 00:06", db_path)
        assert excluded.events == []

    async def test_time_exclusion_reported_explicitly(self, db_path):
        async with get_db(db_path) as db:
            await self._seed(db)
        result = await investigate("show all events between 00:12 and 00:15", db_path)
        assert result.events == []
        assert any("outside the requested time range" in g for g in result.evidence_gaps)

    async def test_keyword_queries_unaffected(self, db_path):
        async with get_db(db_path) as db:
            await self._seed(db)
        result = await investigate("blue bag", db_path)
        assert [e.event_id for e in result.events] == ["e-late"]


# ── Fix 3: multi-frame confirmation + spans ──────────────────────────────

@pytest.mark.asyncio
class TestMultiFrameConfirmation:
    async def test_single_frame_group_produces_no_event(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        await insert_observation(test_db, RECORDING_ID_1, object_label="purple helicopter",
                               video_timestamp_seconds=13.0, detection_score=0.9)
        assert await extract_events_for_recording(RECORDING_ID_1, db_path) == 0

    async def test_single_frame_multi_object_still_unconfirmed(self, test_db, db_path):
        # Several objects in ONE frame still count as one confirming frame.
        await insert_recording(test_db, RECORDING_ID_1)
        for label in ("person", "bag", "car"):
            await insert_observation(test_db, RECORDING_ID_1, object_label=label,
                                   video_timestamp_seconds=13.0, detection_score=0.9)
        assert await extract_events_for_recording(RECORDING_ID_1, db_path) == 0

    async def test_two_frames_confirm_event(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1)
        for ts in (13.0, 14.0):
            await insert_observation(test_db, RECORDING_ID_1, object_label="purple helicopter",
                                   video_timestamp_seconds=ts, detection_score=0.9)
        assert await extract_events_for_recording(RECORDING_ID_1, db_path) == 1

    async def test_span_persisted_and_exposed(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1,
                               recording_datetime="2024-06-01T10:00:00+00:00")
        for ts in (5.0, 6.0, 7.0):
            await insert_observation(test_db, RECORDING_ID_1, object_label="person",
                                   video_timestamp_seconds=ts)
        assert await extract_events_for_recording(RECORDING_ID_1, db_path) == 1
        import aiosqlite
        async with get_db(db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT timestamp_seconds, start_seconds, end_seconds FROM events"
            )).fetchone()
        assert row["start_seconds"] == 5.0
        assert row["end_seconds"] == 7.0
        assert row["timestamp_seconds"] == 6.0

        result = await investigate("person", db_path)
        assert len(result.events) == 1
        assert result.events[0].start_seconds == 5.0
        assert result.events[0].end_seconds == 7.0


class TestSpanAwareMetrics:
    def _gt(self):
        return {"id": "GT-001", "camera_name": "Cam", "start_seconds": 3.0,
                "end_seconds": 8.0, "expected_keywords": ["red"],
                "event_type": "person_detected",
                "absolute_start": "2024-01-15T10:00:03+00:00",
                "absolute_end": "2024-01-15T10:00:08+00:00"}

    def _pred(self, **kw):
        base = {"event_id": "p1", "camera_name": "Cam", "timestamp_seconds": 5.5,
                "action_description": "red figure", "event_type": "person_detected",
                "timestamp_absolute": "2024-01-15T10:00:05.500000+00:00"}
        base.update(kw)
        return base

    def test_span_boundaries_exact(self):
        matching = M.match_events([self._gt()], [self._pred(start_seconds=3.0, end_seconds=8.0)])
        t = M.score_temporal_accuracy(matching, [self._gt()],
                                      [self._pred(start_seconds=3.0, end_seconds=8.0)])
        assert t["start_boundary_error"]["mean"] == 0.0
        assert t["end_boundary_error"]["mean"] == 0.0

    def test_point_fallback_without_span(self):
        matching = M.match_events([self._gt()], [self._pred()])
        t = M.score_temporal_accuracy(matching, [self._gt()], [self._pred()])
        assert t["start_boundary_error"]["mean"] == 2.5
        assert t["end_boundary_error"]["mean"] == 2.5

    def test_absolute_error_uses_gt_midpoint(self):
        matching = M.match_events([self._gt()], [self._pred()])
        t = M.score_temporal_accuracy(matching, [self._gt()], [self._pred()])
        assert t["absolute_error"]["mean"] == 0.0

    def test_absolute_single_bound_fallback(self):
        gt = self._gt()
        del gt["absolute_end"]
        matching = M.match_events([gt], [self._pred()])
        t = M.score_temporal_accuracy(matching, [gt], [self._pred()])
        # |10:00:05.5 - 10:00:03| = 2.5s against the lone start bound.
        assert t["absolute_error"]["mean"] == 2.5


# ── Prompt / parse hardening ─────────────────────────────────────────────

class TestPromptAndParsing:
    def test_prompt_keeps_json_contract(self):
        for key in ('"objects"', '"label"', '"scene_summary"', '"confidence"'):
            assert key in ANALYSIS_PROMPT

    def test_prompt_states_observation_rules(self):
        lowered = ANALYSIS_PROMPT.lower()
        assert "observed" in lowered and "infer" in lowered
        assert "occlu" in lowered  # occlusion guidance present

    def _base_kwargs(self):
        return {"recording_id": "r1", "frame_id": "f1",
                "video_timestamp_seconds": 1.0, "recording_datetime": None,
                "recording_timezone": None, "source_frame_path": "frames/r1/f.jpg",
                "content_hash": "h", "model_version": "mock-v1"}

    def test_malformed_analysis_returns_empty(self):
        assert _parse_observations(None, **self._base_kwargs()) == []
        assert _parse_observations({"objects": "not-a-list"}, **self._base_kwargs()) == []
        assert _parse_observations("junk", **self._base_kwargs()) == []

    def test_malformed_objects_skipped_or_coerced(self):
        analysis = {"objects": [
            "not-a-dict",
            {"label": "car", "confidence": "high", "attributes": "red"},
            {"label": None, "confidence": 99.0},
            {"label": "ok", "confidence": -3.0},
        ]}
        obs = _parse_observations(analysis, **self._base_kwargs())
        assert len(obs) == 3  # non-dict entry skipped
        assert obs[0]["detection_score"] == 0.5  # unparseable -> default
        assert obs[0]["object_attributes"] == {}
        assert obs[1]["object_label"] == "unknown"
        assert obs[1]["detection_score"] == 1.0  # clamped
        assert obs[2]["detection_score"] == 0.0  # clamped

    def test_long_strings_capped(self):
        analysis = {"objects": [{"label": "x" * 1000, "scene_description": "y" * 5000,
                                 "confidence": 0.9}]}
        obs = _parse_observations(analysis, **self._base_kwargs())
        assert len(obs[0]["object_label"]) == 500
        assert len(obs[0]["scene_description"]) == 2000


# ── Evidence validation ──────────────────────────────────────────────────

class TestEvidenceValidation:
    def test_valid_frames_pass(self):
        frames = [{"frame_path": "frames/r/f.jpg", "timestamp_seconds": 3.0, "role": "during"}]
        assert validate_evidence_frames(frames, video_duration_seconds=15.0) == []

    def test_unknown_duration_skips_range_check(self):
        frames = [{"frame_path": "f.jpg", "timestamp_seconds": 999.0}]
        assert validate_evidence_frames(frames) == []

    def test_problems_reported(self):
        frames = [
            {"frame_path": "", "timestamp_seconds": 1.0},
            {"frame_path": "f.jpg", "timestamp_seconds": -2.0},
            {"frame_path": "f.jpg", "timestamp_seconds": 99.0},
            {"frame_path": "f.jpg", "timestamp_seconds": "soon"},
            "not-a-dict",
        ]
        problems = validate_evidence_frames(frames, video_duration_seconds=15.0)
        assert len(problems) == 5
        assert any("no frame_path" in p for p in problems)
        assert any("negative" in p for p in problems)
        assert any("exceeds" in p for p in problems)
        assert any("invalid timestamp" in p for p in problems)
        assert any("malformed" in p for p in problems)

    @pytest.mark.asyncio
    async def test_investigate_flags_bad_frames(self, test_db, db_path):
        await insert_recording(test_db, RECORDING_ID_1, camera_name="Cam")
        async with get_db(db_path) as db:
            await db.execute(
                "UPDATE recordings SET video_duration_seconds = 10.0 WHERE recording_id = ?",
                (RECORDING_ID_1,))
            await db.commit()
        await insert_event(test_db, RECORDING_ID_1, action_description="red figure",
                           object_descriptions=["red figure"],
                           observed_facts=["Observed: red figure"],
                           evidence_frames=[{"frame_path": "f.jpg",
                                             "timestamp_seconds": 99.0, "role": "during"}])
        result = await investigate("red figure", db_path)
        assert len(result.events) == 1
        assert any("exceeds" in n for n in result.events[0].uncertainty_notes)


# ── Agent contribution honesty ───────────────────────────────────────────

@pytest.mark.asyncio
class TestAgentContributionHonesty:
    async def test_mocked_agents_excluded_from_agents_run(self, db_path):
        from app.agents.orchestrator import InvestigationOrchestrator
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1, camera_name="Cam")
            await insert_event(db, RECORDING_ID_1, action_description="person detected",
                               object_descriptions=["person"],
                               observed_facts=["Observed: person"])
        # Budget > 0 with no API keys -> LLM agents run as MOCKED.
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=0.01)
        result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        by_name = {a["agent_name"]: a for a in result["agents"]}
        assert by_name["VideoIntelligenceAgent"]["status"] == AgentStatus.MOCKED.value
        agents_run = result["final_report"]["agents_run"]
        assert "VideoIntelligenceAgent" not in agents_run
        assert "QueryInvestigationAgent" in agents_run
