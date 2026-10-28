"""Phase 8 cost & performance tests — deterministic, no live API.

Covers: parallel agent pairs (overlap, order, fallback, partial failure),
LLM concurrency bounds, result-cache hit/miss/invalidation/TTL/trim,
idempotency keys, in-flight duplicate coalescing, bounded model requests,
stable ordering, evidence preservation across cache hits, and the
benchmark report schema.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.agents.base import AgentResult, AgentStatus  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import create_tables, get_db  # noqa: E402
from app.guards import bounded_llm_call, reset_llm_semaphore  # noqa: E402
from app.investigation_cache import (build_fingerprint, cached_or_compute,  # noqa: E402
                                     lookup, model_config_part,
                                     recording_fingerprint, store)
from tests.fixtures import RECORDING_ID_1, insert_event, insert_recording  # noqa: E402


@pytest.fixture
def _env_settings(monkeypatch):
    def apply(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
    yield apply
    get_settings.cache_clear()


async def _seed_person(db_path, n_events=1):
    async with get_db(db_path) as db:
        import aiosqlite
        db.row_factory = aiosqlite.Row
        await insert_recording(db, RECORDING_ID_1, camera_name="Cam")
        for i in range(n_events):
            await insert_event(db, RECORDING_ID_1, event_id=f"ev-{i}",
                               timestamp_seconds=float(i),
                               action_description=f"person detected {i}",
                               object_descriptions=["person"],
                               observed_facts=["Observed: person"])


# ── Parallel agent pairs ─────────────────────────────────────────────────

@pytest.mark.asyncio
class TestParallelPairs:
    async def test_pairs_overlap_in_time(self, db_path, monkeypatch):
        """Two 0.2s pair members complete in well under 0.4s sequentially."""
        from app.agents import object_action_analyst, video_intelligence
        from app.agents.orchestrator import InvestigationOrchestrator
        await _seed_person(db_path)

        async def slow_video(ctx):
            await asyncio.sleep(0.2)
            return AgentResult(agent_name="VideoIntelligenceAgent",
                               status=AgentStatus.COMPLETED, output=None,
                               duration_ms=200.0)

        async def slow_object(ctx):
            await asyncio.sleep(0.2)
            return AgentResult(agent_name="ObjectActionAnalystAgent",
                               status=AgentStatus.COMPLETED, output=None,
                               duration_ms=200.0)

        monkeypatch.setattr(video_intelligence, "run", slow_video)
        monkeypatch.setattr(object_action_analyst, "run", slow_object)
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=5.0,
                                         parallel_pairs=True)
        t0 = time.monotonic()
        result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        wall = time.monotonic() - t0
        assert wall < 0.35  # overlapped; sequential would exceed 0.4s
        assert [a["agent_name"] for a in result["agents"]][:3] == [
            "QueryInvestigationAgent", "VideoIntelligenceAgent",
            "ObjectActionAnalystAgent"]

    async def test_sequential_fallback_when_disabled(self, db_path, monkeypatch):
        from app.agents import object_action_analyst, video_intelligence
        from app.agents.orchestrator import InvestigationOrchestrator
        await _seed_person(db_path)
        order: list[str] = []

        async def mk(name):
            async def _run(ctx):
                order.append(f"{name}-start")
                await asyncio.sleep(0.05)
                order.append(f"{name}-end")
                return AgentResult(agent_name=name, status=AgentStatus.COMPLETED,
                                   output=None, duration_ms=50.0)
            return _run

        monkeypatch.setattr(video_intelligence, "run", await mk("VideoIntelligenceAgent"))
        monkeypatch.setattr(object_action_analyst, "run", await mk("ObjectActionAnalystAgent"))
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=5.0,
                                         parallel_pairs=False)
        result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        assert order == ["VideoIntelligenceAgent-start", "VideoIntelligenceAgent-end",
                         "ObjectActionAnalystAgent-start", "ObjectActionAnalystAgent-end"]
        assert len(result["agents"]) == 8

    async def test_pair_partial_failure_isolated(self, db_path, monkeypatch):
        from app.agents import object_action_analyst
        from app.agents.orchestrator import InvestigationOrchestrator
        await _seed_person(db_path)

        async def boom(ctx):
            raise RuntimeError("pair member exploded")

        monkeypatch.setattr(object_action_analyst, "run", boom)
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=5.0,
                                         parallel_pairs=True)
        result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        by_name = {a["agent_name"]: a for a in result["agents"]}
        assert by_name["ObjectActionAnalystAgent"]["status"] == "failed"
        # The pair peer is unaffected; order still deterministic.
        assert by_name["VideoIntelligenceAgent"]["status"] in ("completed", "mocked")
        assert [a["agent_name"] for a in result["agents"]] == [
            "QueryInvestigationAgent", "VideoIntelligenceAgent",
            "ObjectActionAnalystAgent", "TemporalReasoningAgent",
            "CrossCameraCorrelationAgent", "ContradictionHunterAgent",
            "EvidenceAuditorAgent", "ReportSynthesisAgent"]
        assert result["status"] == "PARTIAL"

    async def test_deadline_cancels_pair_member(self, db_path, monkeypatch):
        from app.agents import video_intelligence
        from app.agents.orchestrator import InvestigationOrchestrator
        await _seed_person(db_path)

        async def slow(ctx):
            await asyncio.sleep(5)
            return AgentResult(agent_name="VideoIntelligenceAgent",
                               status=AgentStatus.COMPLETED, output=None,
                               duration_ms=0.0)

        monkeypatch.setattr(video_intelligence, "run", slow)
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=5.0,
                                         parallel_pairs=True,
                                         agent_timeout_s=30.0,
                                         investigation_timeout_s=0.05)
        result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        assert result["status"] == "PARTIAL"
        assert len(result["agents"]) == 8

    async def test_llm_call_cap_shared_across_pair(self, db_path):
        from app.agents.orchestrator import InvestigationOrchestrator
        await _seed_person(db_path)
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=5.0,
                                         max_llm_calls=1, parallel_pairs=True)
        result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        assert result["llm_calls_made"] <= 1
        statuses = {a["agent_name"]: a["status"] for a in result["agents"]}
        skipped = [n for n, s in statuses.items() if s == "skipped"]
        assert len(skipped) >= 3  # at most one of the four LLM agents ran


# ── LLM concurrency semaphore ────────────────────────────────────────────

@pytest.mark.asyncio
class TestLlmConcurrency:
    async def test_parallel_calls_bounded(self):
        current = 0
        peak = 0

        async def fake_call():
            nonlocal current, peak
            async with bounded_llm_call(limit=2):
                current += 1
                peak = max(peak, current)
                await asyncio.sleep(0.05)
                current -= 1

        await asyncio.gather(*[fake_call() for _ in range(5)])
        assert peak <= 2
        reset_llm_semaphore()

    async def test_zero_limit_disables_bound(self):
        current = 0
        peak = 0

        async def fake_call():
            nonlocal current, peak
            async with bounded_llm_call(limit=0):
                current += 1
                peak = max(peak, current)
                await asyncio.sleep(0.05)
                current -= 1

        await asyncio.gather(*[fake_call() for _ in range(5)])
        assert peak == 5
        reset_llm_semaphore()


# ── Result cache ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestInvestigationCache:
    async def test_miss_then_hit(self, client, tmp_data_dir):
        from app.config import get_settings
        app_db = str(tmp_data_dir / "findit.db")
        await create_tables(app_db)
        async with get_db(app_db) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1, camera_name="Cam")
            await insert_event(db, RECORDING_ID_1, action_description="red figure",
                               object_descriptions=["red figure"],
                               observed_facts=["Observed: red figure"])
        first = await client.post("/api/investigations/", json={"query": "red figure"})
        assert first.json()["cache_hit"] is False
        second = await client.post("/api/investigations/", json={"query": "red figure"})
        assert second.json()["cache_hit"] is True
        assert (second.json()["events"][0]["event_id"]
                == first.json()["events"][0]["event_id"])
        get_settings.cache_clear()

    async def test_different_query_misses(self, client, tmp_data_dir):
        from app.config import get_settings
        await client.post("/api/investigations/", json={"query": "alpha"})
        resp = await client.post("/api/investigations/", json={"query": "beta"})
        assert resp.json()["cache_hit"] is False
        get_settings.cache_clear()

    async def test_recording_change_invalidates(self, client, tmp_data_dir):
        from app.config import get_settings
        app_db = str(tmp_data_dir / "findit.db")
        await create_tables(app_db)
        await client.post("/api/investigations/", json={"query": "anything"})
        hit = await client.post("/api/investigations/", json={"query": "anything"})
        assert hit.json()["cache_hit"] is True
        # A new recording changes the 'all indexed' fingerprint -> miss.
        async with get_db(app_db) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1, camera_name="Cam")
        miss = await client.post("/api/investigations/", json={"query": "anything"})
        assert miss.json()["cache_hit"] is False
        get_settings.cache_clear()

    async def test_ttl_zero_disables(self, client, tmp_data_dir, _env_settings):
        from app.config import get_settings
        _env_settings(INVESTIGATION_CACHE_TTL_S="0")
        await client.post("/api/investigations/", json={"query": "x"})
        resp = await client.post("/api/investigations/", json={"query": "x"})
        assert resp.json()["cache_hit"] is False
        get_settings.cache_clear()

    async def test_idempotency_replay_and_mismatch(self, client, tmp_data_dir):
        from app.config import get_settings
        first = await client.post("/api/investigations/", json={"query": "same"},
                                  headers={"Idempotency-Key": "key-1"})
        assert first.json()["cache_hit"] is False
        replay = await client.post("/api/investigations/", json={"query": "same"},
                                   headers={"Idempotency-Key": "key-1"})
        assert replay.json()["cache_hit"] is True
        assert (replay.json()["investigation_id"]
                == first.json()["investigation_id"])
        conflict = await client.post("/api/investigations/", json={"query": "different"},
                                     headers={"Idempotency-Key": "key-1"})
        assert conflict.status_code == 422
        get_settings.cache_clear()

    async def test_transient_failures_not_cached(self, client, tmp_data_dir, monkeypatch):
        """A multi-agent result with a failed agent must not be cached."""
        from app.config import get_settings
        from app.agents import object_action_analyst
        app_db = str(tmp_data_dir / "findit.db")
        await create_tables(app_db)
        async with get_db(app_db) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1, camera_name="Cam")
            await insert_event(db, RECORDING_ID_1, action_description="person")

        async def boom(ctx):
            raise RuntimeError("transient trouble")

        monkeypatch.setattr(object_action_analyst, "run", boom)
        first = await client.post("/api/investigations/multi-agent",
                                  json={"query": "person", "budget_usd": 5.0})
        assert first.json()["cache_hit"] is False
        # The failed run left nothing cacheable: the retry also misses
        # (and fails the same way — proving recomputation happened).
        second = await client.post("/api/investigations/multi-agent",
                                   json={"query": "person", "budget_usd": 5.0})
        assert second.json()["cache_hit"] is False
        by_name = {a["agent_name"]: a for a in second.json()["agents"]}
        assert by_name["ObjectActionAnalystAgent"]["status"] == "failed"
        get_settings.cache_clear()

    async def test_concurrent_duplicates_coalesce(self, db_path):
        calls = 0

        async def compute():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return {"n": calls}

        results = await asyncio.gather(*[
            cached_or_compute("same-key", compute) for _ in range(4)])
        assert calls == 1
        assert all(r[0] == {"n": 1} for r in results)
        assert sum(1 for r in results if r[1] is True) == 3  # 3 waiters

    async def test_coalesced_failure_caches_nothing(self, db_path):
        await create_tables(db_path)
        calls = 0

        async def failing():
            nonlocal calls
            calls += 1
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await cached_or_compute("fail-key", failing)
        with pytest.raises(RuntimeError):
            await cached_or_compute("fail-key", failing)
        assert calls == 2  # recomputed: failures are never cached

    async def test_entry_trim_bounded(self, db_path):
        await create_tables(db_path)
        for i in range(12):
            await store(db_path, f"key-{i}", "standard", {"i": i},
                        max_entries=10)
        async with get_db(db_path) as db:
            row = await (await db.execute(
                "SELECT COUNT(*) FROM investigation_cache")).fetchone()
        assert row[0] == 10  # trims oldest 10% each time the cap is exceeded
        # Fingerprints differ per input; identical inputs collide.
        assert (build_fingerprint("standard", "q", "r", None, None, None, "m")
                == build_fingerprint("standard", "q", "r", None, None, None, "m"))
        assert (build_fingerprint("standard", "q", "r", None, None, None, "m")
                != build_fingerprint("multi_agent", "q", "r", None, None, None, "m"))
        assert (build_fingerprint("standard", "q1", "r", None, None, None, "m")
                != build_fingerprint("standard", "q2", "r", None, None, None, "m"))


# ── Ordering, evidence, and benchmark schema ─────────────────────────────

@pytest.mark.asyncio
class TestOrderingAndEvidence:
    async def test_stable_event_ordering(self, db_path):
        await _seed_person(db_path, n_events=5)
        from app.investigate import investigate
        orders = []
        for _ in range(3):
            result = await investigate("person", db_path)
            orders.append([e.event_id for e in result.events])
        assert orders[0] == orders[1] == orders[2]
        assert len(orders[0]) == 5

    async def test_cache_hit_preserves_evidence(self, client, tmp_data_dir):
        from app.config import get_settings
        app_db = str(tmp_data_dir / "findit.db")
        await create_tables(app_db)
        async with get_db(app_db) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1, camera_name="Cam")
            await insert_event(
                db, RECORDING_ID_1, action_description="red figure",
                object_descriptions=["red figure"],
                observed_facts=["Observed: red figure"],
                evidence_frames=[{"frame_path": "frames/r/f.jpg",
                                  "timestamp_seconds": 2.0, "role": "during"}])
        miss = (await client.post("/api/investigations/",
                                  json={"query": "red figure"})).json()
        hit = (await client.post("/api/investigations/",
                                 json={"query": "red figure"})).json()
        assert hit["cache_hit"] is True
        for key in ("investigation_id", "events", "narrative",
                    "timestamp_ordering_reliable", "evidence_gaps"):
            assert hit[key] == miss[key]
        assert hit["events"][0]["evidence_frames"] == miss["events"][0]["evidence_frames"]
        get_settings.cache_clear()


@pytest.mark.asyncio
class TestBenchmarkReport:
    async def test_benchmark_schema_and_warm_hits(self):
        import eval.benchmark as benchmark
        old_data_dir = os.environ.get("DATA_DIR")
        try:
            report = await benchmark.main_async(repeats=2)
        finally:
            if old_data_dir is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = old_data_dir
            get_settings.cache_clear()
        assert report["dataset"]["version"].startswith("2.")
        assert report["config"]["llm_mode"].startswith("mocked")
        for mode in ("standard", "multi_agent"):
            cold = report["summary"][mode]["cold"]
            warm = report["summary"][mode]["warm_1"]
            assert cold["cache_hits"] == 0
            assert warm["cache_hits"] == warm["n_queries"] == 11
            assert cold["latency"]["median_ms"] is not None
            assert cold["latency"]["p95_ms"] is None  # n=11 < 20
        assert report["passes"]["cold"]["multi_agent"]["Q1"]["llm_calls"] >= 0
        # Warm passes execute zero new model calls.
        assert report["summary"]["multi_agent"]["warm_1"]["llm_calls_new"] == 0
        assert report["summary"]["multi_agent"]["warm_1"]["llm_calls_avoided"] > 0
