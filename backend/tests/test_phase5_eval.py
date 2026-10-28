"""Phase 5 evaluation & observability tests — deterministic, no live API.

Covers: P/R/F1 correctness, event matching rules, empty/duplicate cases,
invalid references, malformed timestamps, false cross-camera associations,
unsupported claims, agent execution states, missing provider metrics,
cache handling, and stable report generation.

API keys are stripped for every test here by the autouse fixture in
conftest.py, so LLM helpers always take the mocked path (no network).
"""
import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from eval import metrics as M  # noqa: E402
from eval.dataset import composition, load_v1_as_v2, load_v2  # noqa: E402
from app.agents.base import AgentResult, AgentStatus  # noqa: E402
from app.agents.schemas import FinalReport  # noqa: E402
from app.correlator import correlate_across_cameras  # noqa: E402
from app.database import create_tables, get_db  # noqa: E402
from tests.fixtures import (RECORDING_ID_1, RECORDING_ID_2, insert_event,  # noqa: E402
                            insert_recording)


def _gt(gt_id="GT-001", camera="Entrance Camera", start=3.0, end=8.0,
         keywords=None, event_type="person_detected"):
    return {"id": gt_id, "camera_name": camera, "start_seconds": start,
            "end_seconds": end, "expected_keywords": keywords or ["red", "person"],
            "event_type": event_type}


def _pred(ts=5.5, camera="Entrance Camera", text="red person figure",
          event_type="person_detected", event_id="p1"):
    return {"event_id": event_id, "camera_name": camera, "timestamp_seconds": ts,
            "action_description": text, "observed_facts": [], "object_descriptions": [],
            "scene_description": "", "event_type": event_type}


# ── P/R/F1 correctness ───────────────────────────────────────────────────

class TestPrfCorrectness:
    def test_hand_verifiable_values(self):
        r = M.prf(tp=2, fp=1, fn=1)
        assert r["precision"] == pytest.approx(2 / 3)
        assert r["recall"] == pytest.approx(2 / 3)
        assert r["f1"] == pytest.approx(2 / 3)
        assert r["precision_denominator"] == 3
        assert r["recall_denominator"] == 3

    def test_perfect_score(self):
        r = M.prf(tp=3, fp=0, fn=0)
        assert (r["precision"], r["recall"], r["f1"]) == (1.0, 1.0, 1.0)

    def test_zero_zero_defined_is_zero_f1(self):
        r = M.prf(tp=0, fp=2, fn=3)
        assert r["precision"] == 0.0
        assert r["recall"] == 0.0
        assert r["f1"] == 0.0

    def test_undefined_when_denominator_zero(self):
        r = M.prf(tp=0, fp=0, fn=0)
        assert r["precision"] is None
        assert r["recall"] is None
        assert r["f1"] is None

    def test_recall_zero_precision_undefined(self):
        # Missing predictions: nothing returned, 3 GT missed.
        r = M.prf(tp=0, fp=0, fn=3)
        assert r["precision"] is None
        assert r["recall"] == 0.0
        assert r["f1"] is None


# ── Event matching ───────────────────────────────────────────────────────

class TestEventMatching:
    def test_match_inside_window(self):
        m = M.match_events([_gt()], [_pred(ts=5.5)])
        assert (m["tp"], m["fp"], m["fn"]) == (1, 0, 0)
        assert m["matches"][0]["timestamp_error_seconds"] == pytest.approx(0.0)

    def test_tolerance_boundary_inclusive(self):
        # GT [3,8] mid 5.5, half-window 2.5 + tol 2.0 = 4.5; |10.0-5.5| = 4.5.
        m = M.match_events([_gt()], [_pred(ts=10.0)], time_tolerance_seconds=2.0)
        assert m["tp"] == 1

    def test_outside_tolerance_is_fp_and_fn(self):
        m = M.match_events([_gt()], [_pred(ts=10.1)], time_tolerance_seconds=2.0)
        assert (m["tp"], m["fp"], m["fn"]) == (0, 1, 1)

    def test_zero_tolerance_uses_exact_window(self):
        m = M.match_events([_gt()], [_pred(ts=8.1)], time_tolerance_seconds=0.0)
        assert m["tp"] == 0  # |8.1-5.5|=2.6 > half-window 2.5
        m2 = M.match_events([_gt()], [_pred(ts=8.0)], time_tolerance_seconds=0.0)
        assert m2["tp"] == 1

    def test_camera_mismatch_rejected(self):
        m = M.match_events([_gt()], [_pred(camera="Other Camera")])
        assert (m["tp"], m["fp"], m["fn"]) == (0, 1, 1)

    def test_camera_not_required_when_disabled(self):
        m = M.match_events([_gt()], [_pred(camera="Other Camera")], require_camera=False)
        assert m["tp"] == 1

    def test_event_type_strictness_optional(self):
        m = M.match_events([_gt()], [_pred(event_type="vehicle_detected")])
        assert m["tp"] == 1  # not required by default
        m2 = M.match_events([_gt()], [_pred(event_type="vehicle_detected")],
                            require_event_type=True)
        assert m2["tp"] == 0

    def test_keyword_overlap_threshold(self):
        m = M.match_events([_gt(keywords=["red", "person"])],
                           [_pred(text="a red shape")], keyword_overlap_min=2)
        assert m["tp"] == 0
        m2 = M.match_events([_gt(keywords=["red", "person"])],
                            [_pred(text="a red person shape")], keyword_overlap_min=2)
        assert m2["tp"] == 1

    def test_duplicate_predictions_extras_are_fp(self):
        m = M.match_events([_gt()], [_pred(event_id="p1"), _pred(event_id="p2")])
        assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 0)

    def test_missing_predictions_all_fn(self):
        m = M.match_events([_gt("GT-001"), _gt("GT-002")], [])
        assert (m["tp"], m["fp"], m["fn"]) == (0, 0, 2)

    def test_empty_both_sides(self):
        m = M.match_events([], [])
        assert (m["tp"], m["fp"], m["fn"]) == (0, 0, 0)

    def test_prediction_without_timestamp_is_fp(self):
        pred = _pred()
        del pred["timestamp_seconds"]
        m = M.match_events([_gt()], [pred])
        assert (m["tp"], m["fp"], m["fn"]) == (0, 1, 1)

    def test_greedy_assignment_prefers_lowest_error(self):
        gt = [_gt("GT-001", start=0.0, end=4.0), _gt("GT-002", start=10.0, end=14.0)]
        preds = [_pred(ts=2.0, event_id="pa"), _pred(ts=2.5, event_id="pb")]
        m = M.match_events(gt, preds)
        assert m["matched_gt_ids"] == ["GT-001"]
        assert m["matches"][0]["pred_event_id"] == "pa"

    def test_unmatchable_gt_counted(self):
        m = M.match_events([{"id": "GT-X"}], [_pred()])
        assert m["n_gt_unmatchable"] == 1
        assert m["tp"] == 0

    def test_per_event_type_breakdown_and_low_sample(self):
        gt = [_gt("GT-001", event_type="person_detected"),
              _gt("GT-002", event_type="vehicle_detected")]
        scored = M.score_event_detection(gt, [_pred(event_id="p1")])
        by_type = scored["by_event_type"]
        assert by_type["person_detected"]["tp"] == 1
        assert by_type["vehicle_detected"]["fn"] == 1
        assert by_type["person_detected"]["low_sample"] is True  # n=1 < 5


# ── Temporal accuracy ────────────────────────────────────────────────────

class TestTemporalAccuracy:
    def _matching(self):
        gt = [_gt()]
        preds = [_pred(ts=6.0, event_id="p1")]
        return M.match_events(gt, preds), gt, preds

    def test_relative_error_stats(self):
        matching, gt, preds = self._matching()
        t = M.score_temporal_accuracy(matching, gt, preds)
        assert t["relative_error"]["n"] == 1
        assert t["relative_error"]["mean"] == pytest.approx(0.5)
        assert t["n_missing_absolute"] == 1  # no absolute timestamps

    def test_absolute_error_when_both_valid(self):
        matching, gt, preds = self._matching()
        gt[0]["absolute_start"] = "2024-01-15T10:00:03+00:00"
        preds[0]["timestamp_absolute"] = "2024-01-15T10:00:05+00:00"
        t = M.score_temporal_accuracy(matching, gt, preds)
        assert t["absolute_error"]["n"] == 1
        assert t["absolute_error"]["mean"] == pytest.approx(2.0)
        assert t["n_malformed_absolute"] == 0

    def test_malformed_absolute_counted_not_filled(self):
        matching, gt, preds = self._matching()
        gt[0]["absolute_start"] = "2024-01-15T10:00:03+00:00"
        preds[0]["timestamp_absolute"] = "not-a-timestamp"
        t = M.score_temporal_accuracy(matching, gt, preds)
        assert t["absolute_error"]["n"] == 0
        assert t["absolute_error"]["mean"] is None
        assert t["n_malformed_absolute"] == 1

    def test_missing_absolute_counted(self):
        matching, gt, preds = self._matching()
        gt[0]["absolute_start"] = "2024-01-15T10:00:03+00:00"
        # pred has no timestamp_absolute
        t = M.score_temporal_accuracy(matching, gt, preds)
        assert t["n_missing_absolute"] == 1
        assert t["absolute_error"]["n"] == 0

    def test_boundary_errors_signed(self):
        matching, gt, preds = self._matching()
        t = M.score_temporal_accuracy(matching, gt, preds)
        per = t["per_match"][0]
        assert per["start_boundary_error_seconds"] == pytest.approx(3.0)  # 6-3
        assert per["end_boundary_error_seconds"] == pytest.approx(-2.0)  # 6-8

    def test_p90_requires_n10(self):
        matching = {"matches": [
            {"gt_id": f"G{i}", "pred_index": 0, "timestamp_error_seconds": float(i)}
            for i in range(3)]}
        gt = [{"id": f"G{i}", "start_seconds": 0.0, "end_seconds": 1.0} for i in range(3)]
        preds = [{"timestamp_seconds": 0.5}]
        t = M.score_temporal_accuracy(matching, gt, preds)
        assert t["relative_error"]["p90"] is None
        assert "n<10" in t["relative_error"]["p90_note"]


# ── Evidence quality ─────────────────────────────────────────────────────

def _recordings():
    return [
        {"key": "rec-a", "recording_id": "rec-a", "duration_seconds": 15.0},
        {"key": "rec-b", "recording_id": "rec-b", "duration_seconds": 10.0},
    ]


class TestEvidenceQuality:
    def test_coverage_and_validity(self):
        claims = [
            {"claim_id": "c1", "evidence": ["rec-a@5.0s"], "supported": None},
            {"claim_id": "c2", "evidence": [{"recording_id": "rec-b",
                                             "timestamp_seconds": 3.0}], "supported": None},
        ]
        r = M.score_evidence_quality(claims, _recordings())
        assert r["evidence_coverage"] == 1.0
        assert r["reference_validity_rate"] == 1.0
        assert r["evidence_support_rate"] is None  # unannotated
        assert r["n_unannotated"] == 2

    def test_invalid_recording_id(self):
        claims = [{"claim_id": "c1", "evidence": ["nope@5.0s"], "supported": None}]
        r = M.score_evidence_quality(claims, _recordings())
        assert r["reference_validity_rate"] == 0.0
        assert r["invalid_references"][0]["reason"] == "unknown_recording"
        # Syntactically valid shape still counts toward coverage (existence != support).
        assert r["evidence_coverage"] == 1.0

    def test_evidence_timestamp_out_of_range(self):
        claims = [{"claim_id": "c1",
                   "evidence": ["rec-a@99.0s", "rec-b@-1.0s"], "supported": None}]
        r = M.score_evidence_quality(claims, _recordings())
        assert r["reference_validity_rate"] == 0.0
        assert {i["reason"] for i in r["invalid_references"]} == {"timestamp_out_of_range"}

    def test_malformed_reference(self):
        claims = [{"claim_id": "c1", "evidence": ["garbage-no-separator"], "supported": None}]
        r = M.score_evidence_quality(claims, _recordings())
        assert r["evidence_coverage"] == 0.0
        assert r["claims_without_evidence"] == ["c1"]

    def test_missing_evidence_references(self):
        claims = [{"claim_id": "c1", "evidence": [], "supported": None}]
        r = M.score_evidence_quality(claims, _recordings())
        assert r["evidence_coverage"] == 0.0
        assert r["reference_validity_rate"] is None

    def test_unsupported_claims_from_annotations_only(self):
        claims = [
            {"claim_id": "c1", "evidence": ["rec-a@1.0s"], "supported": True},
            {"claim_id": "c2", "evidence": ["rec-a@2.0s"], "supported": False},
            {"claim_id": "c3", "evidence": ["rec-a@3.0s"], "supported": None},
        ]
        r = M.score_evidence_quality(claims, _recordings())
        assert r["evidence_support_rate"] == pytest.approx(0.5)
        assert r["unsupported_claim_rate"] == pytest.approx(0.5)
        assert r["n_annotated"] == 2
        # Reference existence must not inflate support.
        assert r["n_references"] == 3

    def test_contradiction_rate(self):
        claims = [
            {"claim_id": "c1", "evidence": [], "supported": True, "contradicted": True},
            {"claim_id": "c2", "evidence": [], "supported": True, "contradicted": False},
        ]
        r = M.score_evidence_quality(claims, _recordings())
        assert r["contradiction_rate"] == pytest.approx(0.5)


# ── Cross-camera association ─────────────────────────────────────────────

class TestCrossCameraScoring:
    def test_false_association_count_and_rate(self):
        r = M.score_cross_camera(
            predicted_pairs=[["GT-001", "GT-003"], ["GT-002", "GT-003"]],
            valid_pairs=[], invalid_pairs=[["GT-001", "GT-003"], ["GT-002", "GT-003"]],
            ambiguous_pairs=[["GT-001", "GT-002"]])
        assert r["false_association_count"] == 2
        assert r["false_association_rate"] == pytest.approx(1.0)
        assert r["false_association_denominator"] == 2
        assert r["precision"] == 0.0
        assert r["recall"] is None  # no valid pairs labeled
        assert r["f1"] is None

    def test_unordered_pair_comparison(self):
        r = M.score_cross_camera(
            predicted_pairs=[["GT-003", "GT-001"]],
            valid_pairs=[], invalid_pairs=[["GT-001", "GT-003"]])
        assert r["false_association_count"] == 1

    def test_ambiguous_hits_excluded_from_pr(self):
        r = M.score_cross_camera(
            predicted_pairs=[["GT-001", "GT-002"]],
            valid_pairs=[["GT-001", "GT-004"]],
            invalid_pairs=[],
            ambiguous_pairs=[["GT-001", "GT-002"]])
        assert r["ambiguous_hits"] == [["GT-001", "GT-002"]]
        assert r["precision"] is None  # no labeled predictions
        assert r["false_negatives"] == [["GT-001", "GT-004"]]

    def test_excluded_pairs_like_unsynced_clocks(self):
        r = M.score_cross_camera(
            predicted_pairs=[["GT-001", "GT-003"]],
            valid_pairs=[], invalid_pairs=[["GT-001", "GT-003"]],
            excluded_pairs=[["GT-001", "GT-003"]])
        assert r["n_predicted"] == 0
        assert r["false_association_count"] == 0
        assert r["false_association_rate"] is None

    def test_unlabeled_predictions_itemized(self):
        r = M.score_cross_camera(
            predicted_pairs=[["GT-001", "GT-009"]],
            valid_pairs=[], invalid_pairs=[])
        assert r["unlabeled_predictions"] == [["GT-001", "GT-009"]]
        assert r["precision"] == 0.0  # conservative: counts as false

    def test_dataset_shape_dict_entries(self):
        r = M.score_cross_camera(
            predicted_pairs=[["GT-002", "GT-003"]],
            valid_pairs=[],
            invalid_pairs=[{"pair": ["GT-002", "GT-003"], "reason": "x"}],
            ambiguous_pairs=[{"pair": ["GT-001", "GT-002"], "reason": "y"}])
        assert r["false_association_count"] == 1


@pytest.mark.asyncio
class TestCrossCameraRegression:
    """Reproduce the recorded false associations against the real correlator."""

    async def test_parking_links_are_false_positives(self, db_path):
        """Phase 4 fix: temporal-only pairs must NOT link (was: 4 false links).

        Phase 5 evidence (comparison_report baseline): GT-001<->GT-003 and
        GT-002<->GT-003 linked with conf 0.40/0.39 from temporal proximity
        alone despite zero shared appearance tokens. The correlator now gates
        on appearance-or-type agreement, so no links form here.
        """
        rec1, rec2, rec3 = "reg-entrance", "reg-hallway", "reg-parking"
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, rec1, camera_name="Entrance Camera",
                                   recording_datetime="2024-01-15T10:00:00")
            await insert_recording(db, rec2, camera_name="Hallway Camera",
                                   recording_datetime="2024-01-15T10:02:00")
            await insert_recording(db, rec3, camera_name="Parking Camera",
                                   recording_datetime="2024-01-15T10:01:00")
            await insert_event(db, rec1, timestamp_seconds=5.5,
                               event_type="person_detected",
                               action_description="Red figure near entrance",
                               object_descriptions=["red person figure"],
                               observed_facts=["Observed: red person figure"])
            await insert_event(db, rec2, timestamp_seconds=8.5,
                               event_type="unattended_object",
                               action_description="Blue bag in hallway",
                               object_descriptions=["blue bag object"],
                               observed_facts=["Observed: blue bag object"])
            await insert_event(db, rec3, timestamp_seconds=4.0,
                               event_type="vehicle_detected",
                               action_description="Green vehicle in lot",
                               object_descriptions=["green vehicle"],
                               observed_facts=["Observed: green vehicle"])

        n_links = await correlate_across_cameras(db_path, [rec1, rec2, rec3])
        assert n_links == 0

        # The gate reason is explicit at the scoring-function level.
        from app.correlator import _correlation_score
        score, expl = _correlation_score(
            {"abs_ts": 1000.0, "object_descriptions": ["red person figure"],
             "event_type": "person_detected"},
            {"abs_ts": 1058.0, "object_descriptions": ["green vehicle"],
             "event_type": "vehicle_detected"})
        assert score == 0.0
        assert "temporal proximity alone is insufficient" in expl

        # Score in GT-id space: no pairs predicted -> zero false associations.
        ds = load_v2()
        cc = ds["cross_camera_associations"]
        r = M.score_cross_camera([], cc["valid"], cc["invalid"], cc["ambiguous"])
        assert r["false_association_count"] == 0
        assert r["false_association_rate"] is None  # no pairs predicted

    async def test_shared_appearance_still_links(self, db_path):
        """The gate must not break legitimate appearance-supported links."""
        rec1, rec2 = "gate-cam1", "gate-cam2"
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, rec1, camera_name="Cam 1",
                                   recording_datetime="2024-01-15T10:00:00")
            await insert_recording(db, rec2, camera_name="Cam 2",
                                   recording_datetime="2024-01-15T10:02:00")
            await insert_event(db, rec1, timestamp_seconds=5.0,
                               event_type="person_detected",
                               action_description="red figure",
                               object_descriptions=["red person figure"])
            await insert_event(db, rec2, timestamp_seconds=6.0,
                               event_type="person_detected",
                               action_description="red figure again",
                               object_descriptions=["red person figure"])
        n_links = await correlate_across_cameras(db_path, [rec1, rec2])
        assert n_links == 2  # one pair x two directions
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                "SELECT link_type FROM event_links")).fetchall()
        assert {r["link_type"] for r in rows} == {"candidate"}

    async def test_expected_pair_does_not_link_on_temporal_only(self, db_path):
        """GT-001<->GT-002 (123s gap -> temporal score 0.295 < 0.3) forms no link."""
        rec1, rec2 = "reg2-entrance", "reg2-hallway"
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, rec1, camera_name="Entrance Camera",
                                   recording_datetime="2024-01-15T10:00:00")
            await insert_recording(db, rec2, camera_name="Hallway Camera",
                                   recording_datetime="2024-01-15T10:02:00")
            await insert_event(db, rec1, timestamp_seconds=5.5,
                               event_type="person_detected",
                               action_description="Red figure near entrance",
                               object_descriptions=["red person figure"])
            await insert_event(db, rec2, timestamp_seconds=8.5,
                               event_type="unattended_object",
                               action_description="Blue bag in hallway",
                               object_descriptions=["blue bag object"])

        assert await correlate_across_cameras(db_path, [rec1, rec2]) == 0


# ── Agent observability ──────────────────────────────────────────────────

def _agent(name, status, **kw):
    base = {"agent_name": name, "status": status, "duration_ms": 10.0,
            "tokens_used": 0, "cost_usd": 0.0, "error": None,
            "retries": None, "timeouts": None, "cache_hits": None,
            "cache_misses": None, "tokens_in": None, "tokens_out": None,
            "usage_measured": False, "event_ids": [], "evidence_ids": []}
    base.update(kw)
    return base


class TestAgentObservability:
    def test_status_counts_and_reasons(self):
        agents = [
            _agent("A", "completed"),
            _agent("B", "skipped", error="Budget limit reached"),
            _agent("C", "failed", error="boom"),
            _agent("D", "mocked"),
        ]
        s = M.summarize_agent_runs(agents)
        assert s["by_status"] == {"completed": 1, "skipped": 1, "failed": 1,
                                  "mocked": 1, "timed_out": 0}
        assert s["skip_reasons"] == [{"agent": "B", "reason": "Budget limit reached"}]
        assert s["failures"] == [{"agent": "C", "reason": "boom"}]
        assert s["unknown_statuses"] == []

    def test_unknown_status_reported(self):
        s = M.summarize_agent_runs([_agent("A", "exploded")])
        assert s["unknown_statuses"] == ["A:exploded"]

    def test_measured_vs_estimated_usage_split(self):
        agents = [
            _agent("A", "completed", tokens_in=100, tokens_out=50, cost_usd=0.001,
                   usage_measured=True),
            _agent("B", "mocked", tokens_used=0, cost_usd=0.0),
        ]
        s = M.summarize_agent_runs(agents)
        assert s["tokens_in_measured"] == 100
        assert s["tokens_out_measured"] == 50
        assert s["cost_measured_usd"] == pytest.approx(0.001)
        assert "B" in s["usage_unavailable_agents"]

    def test_missing_provider_metrics_unavailable(self):
        s = M.summarize_agent_runs([_agent("A", "completed")])
        assert s["tokens_in_measured"] == 0
        assert s["usage_unavailable_agents"] == ["A"]

    def test_cache_hits_and_misses(self):
        agents = [_agent("A", "completed", cache_hits=8, cache_misses=2)]
        s = M.summarize_agent_runs(agents)
        assert s["cache_hit_rate"] == pytest.approx(0.8)
        assert s["cache_unavailable"] is False

    def test_cache_unavailable_when_any_agent_missing(self):
        agents = [_agent("A", "completed", cache_hits=8, cache_misses=2),
                  _agent("B", "completed")]
        s = M.summarize_agent_runs(agents)
        assert s["cache_hits"] is None
        assert s["cache_misses"] is None
        assert s["cache_hit_rate"] is None
        assert s["cache_unavailable"] is True

    def test_retries_timeouts_none_when_unmeasured(self):
        s = M.summarize_agent_runs([_agent("A", "completed")])
        assert s["retries"] is None
        assert s["timeouts"] is None
        s2 = M.summarize_agent_runs(
            [_agent("A", "completed", retries=2, timeouts=1)])
        assert (s2["retries"], s2["timeouts"]) == (2, 1)

    def test_event_and_evidence_id_union(self):
        agents = [_agent("A", "completed", event_ids=["e1"], evidence_ids=["e1@r:1.0s"]),
                  _agent("B", "completed", event_ids=["e1", "e2"])]
        s = M.summarize_agent_runs(agents)
        assert s["event_ids"] == ["e1", "e2"]
        assert s["evidence_ids"] == ["e1@r:1.0s"]


# ── Mocked agent states (no network: keys stripped) ──────────────────────

@pytest.mark.asyncio
class TestMockedAgents:
    async def test_llm_helper_reports_mocked_without_key(self, db_path):
        from app.agents.llm_helper import llm_json_call_ex
        call = await llm_json_call_ex(prompt="{}", db_path=db_path, operation="probe")
        assert call.mocked is True
        assert call.data == {}
        assert (call.tokens_in, call.tokens_out, call.cost_usd) == (0, 0, 0.0)

    async def test_video_intelligence_mocked_without_key(self, db_path):
        from app.agents import video_intelligence
        from app.agents.base import InvestigationContext
        ctx = InvestigationContext(query="red", db_path=db_path, recording_ids=[],
                                   budget_usd=1.0,
                                   events=[{"event_id": "e1", "timestamp_seconds": 1.0,
                                            "action_description": "red figure"}])
        result = await video_intelligence.run(ctx)
        assert result.status == AgentStatus.MOCKED
        assert result.output is not None  # heuristic fallback still populated
        assert result.usage_measured is False

    async def test_evidence_auditor_mocked_with_claims(self, db_path):
        from app.agents import evidence_auditor
        from app.agents.base import InvestigationContext
        ctx = InvestigationContext(
            query="t", db_path=db_path, recording_ids=[], budget_usd=1.0,
            events=[{"event_id": "e1", "action_description": "x",
                     "observed_facts": ["fact"], "inferred_claims": ["claim beyond facts"]}])
        result = await evidence_auditor.run(ctx)
        assert result.status == AgentStatus.MOCKED

    async def test_agent_result_serializes_observability_fields(self):
        r = AgentResult(agent_name="A", status=AgentStatus.COMPLETED, output=None,
                        duration_ms=1.0)
        d = r.to_dict()
        for key in ("retries", "timeouts", "cache_hits", "cache_misses",
                    "tokens_in", "tokens_out", "usage_measured", "model_used",
                    "event_ids", "evidence_ids", "tokens_used", "cost_usd"):
            assert key in d
        assert d["usage_measured"] is False

    async def test_final_report_claim_evidence_defaults_empty(self):
        fr = FinalReport(narrative="n", key_findings=[], uncertainty_notes=[],
                         evidence_citations=[], model_used="m", agents_run=[])
        assert fr.claim_evidence == []


# ── Traceability: provenance + trace endpoints ───────────────────────────

@pytest.mark.asyncio
class TestTraceability:
    async def test_claim_evidence_references_real_events(self, db_path):
        from app.agents.orchestrator import InvestigationOrchestrator
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1, camera_name="Entrance Camera")
            eid = await insert_event(db, RECORDING_ID_1,
                                     action_description="suspect observed at door",
                                     object_descriptions=["person at door"],
                                     observed_facts=["Observed: person at door"])
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=0.0)
        result = await orch.run(query="suspect door", recording_ids=[RECORDING_ID_1])
        fr = result["final_report"]
        assert fr["claim_evidence"], "expected claim-to-evidence provenance"
        cited = {eid for item in fr["claim_evidence"] for eid in item["event_ids"]}
        assert eid in cited
        assert eid in result["retrieved_event_ids"]
        assert result["retrieved_evidence_ids"], "expected evidence refs in trace"

    async def test_trace_persists_phase5_columns(self, db_path):
        from app.agents.orchestrator import InvestigationOrchestrator
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1)
            await insert_event(db, RECORDING_ID_1, action_description="blue bag found")
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=0.0)
        result = await orch.run(query="blue bag", recording_ids=[RECORDING_ID_1])

        import aiosqlite
        async with get_db(db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT code_revision, model_config, usage_summary, agent_results"
                " FROM investigation_traces WHERE trace_id = ?",
                (result["trace_id"],))).fetchone()
        assert row is not None
        assert json.loads(row["model_config"])["budget_usd"] == 0.0
        assert "measured" in json.loads(row["usage_summary"])
        stored_agents = json.loads(row["agent_results"])
        assert len(stored_agents) == 8
        assert all("usage_measured" in a for a in stored_agents)
        # No credentials or video bytes in the trace.
        blob = json.dumps(dict(row))
        assert "Bearer" not in blob and "MODEL_API_KEY" not in blob

    async def test_trace_get_endpoints(self, client, tmp_data_dir):
        from app.config import get_settings
        app_db = str(tmp_data_dir / "findit.db")
        await create_tables(app_db)
        async with get_db(app_db) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1, camera_name="Cam")
            await insert_event(db, RECORDING_ID_1, action_description="person detected")

        resp = await client.post("/api/investigations/multi-agent",
                                 json={"query": "person",
                                       "recording_ids": [RECORDING_ID_1],
                                       "budget_usd": 0.0})
        assert resp.status_code == 200
        trace_id = resp.json()["trace_id"]
        assert resp.json()["retrieved_events"], "response carries scored events"

        one = await client.get(f"/api/investigations/traces/{trace_id}")
        assert one.status_code == 200
        body = one.json()
        assert body["trace_id"] == trace_id
        assert len(body["agent_results"]) == 8
        assert body["final_report"] is not None

        missing = await client.get("/api/investigations/traces/does-not-exist")
        assert missing.status_code == 404

        listing = await client.get("/api/investigations/traces?limit=5")
        assert listing.status_code == 200
        assert any(t["trace_id"] == trace_id for t in listing.json()["traces"])
        get_settings.cache_clear()


# ── Dataset loader ───────────────────────────────────────────────────────

class TestDatasetLoader:
    def test_v2_loads_and_validates(self):
        ds = load_v2()
        assert ds["dataset_id"] == "realitytrace-eval"
        assert ds["version"] == "2.1.0"
        comp = composition(ds)
        assert comp["n_recordings_synthetic"] == 3
        assert comp["n_recordings_real"] == 0
        assert comp["n_events"] == 3
        assert comp["n_queries"] == 11  # v2.1.0 adds Q9/Q10/Q11
        assert comp["n_cc_invalid"] == 2
        assert comp["n_cc_ambiguous"] == 1

    def test_v1_upgrades_to_v2_shape(self):
        ds = load_v1_as_v2()
        assert ds["dataset_id"] == "realitytrace-eval"
        assert len(ds["events"]) == 3
        assert len(ds["queries"]) == 4
        assert all("start_seconds" in e and "end_seconds" in e for e in ds["events"])

    def test_v2_rejects_bad_recording_link(self):
        ds = load_v2()
        ds["events"][0]["recording_key"] = "missing"
        with pytest.raises(ValueError, match="unknown recording"):
            from eval.dataset import _validate_v2
            _validate_v2(ds)


# ── Stable report generation ─────────────────────────────────────────────

class TestStableReports:
    def test_offline_report_deterministic(self):
        from eval.compare import run_offline
        ds = load_v2()
        first = run_offline(ds)
        second = run_offline(ds)
        first.pop("evaluation_timestamp")
        second.pop("evaluation_timestamp")
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_offline_report_has_required_sections(self):
        from eval.compare import run_offline
        report = run_offline(load_v2())
        for key in ("evaluation_mode", "dataset", "code_revision", "model_config",
                    "modes", "metric_definitions", "match_rules", "known_limitations",
                    "usage", "cache"):
            assert key in report, f"missing report section: {key}"
        assert set(report["modes"]) == {"standard", "multi_agent"}
        std = report["modes"]["standard"]
        for key in ("queries", "event_detection", "temporal_accuracy",
                    "cross_camera", "evidence_quality", "observability"):
            assert key in std
        assert report["usage"]["live_api_calls"] == 0

    def test_same_test_cases_both_modes(self):
        from eval.compare import run_offline
        report = run_offline(load_v2())
        std_ids = [q["query_id"] for q in report["modes"]["standard"]["queries"]]
        ma_ids = [q["query_id"] for q in report["modes"]["multi_agent"]["queries"]]
        assert std_ids == ma_ids
        assert len(std_ids) == 11
