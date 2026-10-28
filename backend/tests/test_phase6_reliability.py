"""Phase 6 reliability & security tests — deterministic, no live API.

Covers: transient-only retry (429/5xx/timeouts), non-retry (401/403/400/
malformed), Retry-After, agent + investigation timeouts, concurrency caps,
budget/LLM-call enforcement, cache hit/miss/invalidation/prune, upload
hardening, traversal, Range validation, API-token gate, credential-leakage
prevention, request IDs, rate limits, and input bounds.
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.agents.base import AgentStatus
from app.config import get_settings
from app.database import create_tables, get_db
from app.guards import (check_rate_limit, normalize_request_id,
                        release_investigation_slot,
                        try_acquire_investigation_slot)
from app.meta_client import MockMetaClient, MuseSparkClient
from app.processor import (PROMPT_VERSION, get_cached_analysis,
                           prune_frame_cache, store_cached_analysis)
from app.provider import (AccountedFrameAnalyzer, BudgetExceededError,
                          compute_backoff_seconds, is_transient_error,
                          retry_after_seconds)
from app.storage import LocalStorageBackend, sniff_video_container
from tests.fixtures import (RECORDING_ID_1, RECORDING_ID_2, insert_event,
                            insert_recording)


@pytest.fixture
def _env_settings(monkeypatch):
    """Override Settings via env vars for one test (cache cleared)."""
    def apply(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
    yield apply
    get_settings.cache_clear()


def _status_error(status: int, headers: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.meta.ai/v1/chat/completions")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError(f"{status} error", request=request, response=response)


# ── Transient classification ─────────────────────────────────────────────

class TestTransientClassification:
    @pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
    def test_retryable_statuses(self, status):
        assert is_transient_error(_status_error(status)) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_non_retryable_statuses(self, status):
        assert is_transient_error(_status_error(status)) is False

    def test_transport_timeouts_retryable(self):
        assert is_transient_error(httpx.ConnectTimeout("c")) is True
        assert is_transient_error(httpx.ReadTimeout("r")) is True
        assert is_transient_error(httpx.ConnectError("c")) is True
        assert is_transient_error(asyncio.TimeoutError()) is True

    def test_permanent_errors_not_retried(self):
        assert is_transient_error(BudgetExceededError("x")) is False
        assert is_transient_error(ValueError("malformed")) is False
        assert is_transient_error(RuntimeError("unexpected")) is False


class TestBackoffAndRetryAfter:
    def test_exponential_no_jitter_exact(self):
        assert compute_backoff_seconds(1, 1.0, 8.0, False) == 1.0
        assert compute_backoff_seconds(2, 1.0, 8.0, False) == 2.0
        assert compute_backoff_seconds(3, 1.0, 8.0, False) == 4.0
        assert compute_backoff_seconds(9, 1.0, 8.0, False) == 8.0

    def test_jitter_bounded(self):
        for _ in range(50):
            delay = compute_backoff_seconds(3, 1.0, 8.0, True)
            assert 0.0 <= delay <= 4.0

    def test_retry_after_seconds_honored(self):
        exc = _status_error(429, {"retry-after": "2"})
        assert retry_after_seconds(exc, 0.5) == 2.0

    def test_retry_after_capped(self):
        exc = _status_error(503, {"retry-after": "120"})
        assert retry_after_seconds(exc, 0.5, max_seconds=8.0) == 8.0

    def test_retry_after_garbage_falls_back(self):
        exc = _status_error(429, {"retry-after": "soon-ish"})
        assert retry_after_seconds(exc, 0.5) == 0.5

    def test_retry_after_missing_falls_back(self):
        assert retry_after_seconds(_status_error(500), 0.5) == 0.5


# ── Retry behavior (mocked delegates, patched sleep) ─────────────────────

def _accounted(db_path, delegate, **kw):
    params = {"max_attempts": 3, "base_backoff": 0.0,
              "budget_limit_usd": 150.0, "budget_reserve_usd": 0.0}
    params.update(kw)
    return AccountedFrameAnalyzer(delegate=delegate, db_path=db_path,
                                  provider="test", **params)


@pytest.mark.asyncio
class TestRetryBehavior:
    async def test_connection_timeout_retried_then_raises(self, db_path, monkeypatch):
        sleeps = []
        monkeypatch.setattr("app.provider.asyncio.sleep",
                            AsyncMock(side_effect=lambda s: sleeps.append(s)))

        class TimeoutAnalyzer:
            model_version = "t"
            calls = 0

            async def analyze_frame(self, frame_data: bytes):
                type(self).calls += 1
                raise httpx.ConnectTimeout("connect timed out")

        client = _accounted(db_path, TimeoutAnalyzer(), base_backoff=1.0)
        with pytest.raises(httpx.ConnectTimeout):
            await client.analyze_frame(b"frame")
        assert TimeoutAnalyzer.calls == 3
        assert len(sleeps) == 2  # jittered sleeps between 3 attempts

    async def test_read_timeout_retried(self, db_path, monkeypatch):
        monkeypatch.setattr("app.provider.asyncio.sleep", AsyncMock())
        calls = 0

        class Flaky:
            model_version = "t"

            async def analyze_frame(self, frame_data: bytes):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise httpx.ReadTimeout("read timed out")
                return {"objects": [], "scene_summary": "ok"}

        result = await _accounted(db_path, Flaky()).analyze_frame(b"f")
        assert result["scene_summary"] == "ok"
        assert calls == 2

    async def test_429_retries_and_honors_retry_after(self, db_path, monkeypatch):
        sleeps = []
        monkeypatch.setattr("app.provider.asyncio.sleep",
                            AsyncMock(side_effect=lambda s: sleeps.append(s)))
        calls = 0

        class RateLimited:
            model_version = "t"

            async def analyze_frame(self, frame_data: bytes):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise _status_error(429, {"retry-after": "2"})
                return {"objects": [], "scene_summary": "ok"}

        await _accounted(db_path, RateLimited()).analyze_frame(b"f")
        assert calls == 2
        assert sleeps == [2.0]

    async def test_503_retried_until_exhausted(self, db_path, monkeypatch):
        monkeypatch.setattr("app.provider.asyncio.sleep", AsyncMock())
        calls = 0

        class Broken:
            model_version = "t"

            async def analyze_frame(self, frame_data: bytes):
                nonlocal calls
                calls += 1
                raise _status_error(503)

        with pytest.raises(httpx.HTTPStatusError):
            await _accounted(db_path, Broken()).analyze_frame(b"f")
        assert calls == 3

    @pytest.mark.parametrize("status", [401, 403, 400, 422])
    async def test_auth_and_client_errors_not_retried(self, db_path, status):
        calls = 0

        class Refused:
            model_version = "t"

            async def analyze_frame(self, frame_data: bytes):
                nonlocal calls
                calls += 1
                raise _status_error(status)

        with pytest.raises(httpx.HTTPStatusError):
            await _accounted(db_path, Refused()).analyze_frame(b"f")
        assert calls == 1

    async def test_malformed_response_not_retried(self, db_path):
        calls = 0

        class Garbage:
            model_version = "t"

            async def analyze_frame(self, frame_data: bytes):
                nonlocal calls
                calls += 1
                raise ValueError("Model returned non-JSON")

        with pytest.raises(ValueError):
            await _accounted(db_path, Garbage()).analyze_frame(b"f")
        assert calls == 1

    async def test_budget_rechecked_each_attempt(self, db_path, monkeypatch):
        """A budget exhausted mid-retry aborts remaining attempts."""
        monkeypatch.setattr("app.provider.asyncio.sleep", AsyncMock())
        calls = 0

        class Flaky:
            model_version = "t"

            async def analyze_frame(self, frame_data: bytes):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise httpx.ConnectError("blip")
                return {"objects": [], "scene_summary": "ok"}

        # Simulate a concurrent spender exhausting the budget after attempt 1.
        checks = 0
        orig_check = AccountedFrameAnalyzer._check_budget

        async def counting_check(self):
            nonlocal checks
            checks += 1
            if checks > 1:
                raise BudgetExceededError("reserve breached mid-retry")
            return await orig_check(self)

        monkeypatch.setattr(AccountedFrameAnalyzer, "_check_budget", counting_check)
        client = _accounted(db_path, Flaky())
        with pytest.raises(BudgetExceededError):
            await client.analyze_frame(b"f")
        assert calls == 1
        assert checks == 2


# ── Model response validation ────────────────────────────────────────────

@pytest.mark.asyncio
class TestModelResponseValidation:
    def _mock_http(self, payload: dict):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json = MagicMock(return_value=payload)
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_http.post = AsyncMock(return_value=mock_resp)
        return mock_http

    def _payload(self, content: str):
        return {"choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7}}

    async def test_missing_objects_list_rejected(self):
        client = MuseSparkClient(api_key="k")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = self._mock_http(self._payload('{"scene": "x"}'))
            with pytest.raises(ValueError, match="objects"):
                await client.analyze_frame(b"frame")

    async def test_non_dict_json_rejected(self):
        client = MuseSparkClient(api_key="k")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = self._mock_http(self._payload('[1,2,3]'))
            # P0: the shared strict parser now rejects non-object JSON with its
            # own precise message (it is generic — text agents have no 'objects'
            # list). Still a ValueError, still fail-fast.
            with pytest.raises(ValueError, match="not a JSON object"):
                await client.analyze_frame(b"frame")

    async def test_missing_choices_rejected(self):
        client = MuseSparkClient(api_key="k")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = self._mock_http({"usage": {}})
            with pytest.raises(ValueError, match="choices"):
                await client.analyze_frame(b"frame")

    async def test_valid_response_passes_with_usage(self):
        client = MuseSparkClient(api_key="k")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = self._mock_http(
                self._payload('{"objects": [], "scene_summary": "ok"}'))
            result = await client.analyze_frame(b"frame")
        assert result["objects"] == []
        assert result["_usage"] == {"tokens_in": 5, "tokens_out": 7}

    async def test_llm_helper_empty_response_fails_fast(self, db_path, monkeypatch):
        """Empty model content is non-transient: exactly one attempt."""
        monkeypatch.setenv("MODEL_API_KEY", "fake-key")
        from app.agents.llm_helper import llm_json_call_ex
        posts = []

        async def fake_post(*args, **kwargs):
            posts.append(1)
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value=self._payload("   "))
            mock_resp.raise_for_status = MagicMock(return_value=None)
            return mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(side_effect=fake_post)
            mock_cls.return_value = mock_http
            call = await llm_json_call_ex(prompt="{}", db_path=db_path,
                                          operation="probe", max_attempts=3)
        assert call.success is False
        assert call.attempts == 1
        assert len(posts) == 1

    async def test_llm_helper_401_fails_fast(self, db_path, monkeypatch):
        monkeypatch.setenv("MODEL_API_KEY", "fake-key")
        from app.agents.llm_helper import llm_json_call_ex

        async def fake_post(*args, **kwargs):
            raise _status_error(401)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(side_effect=fake_post)
            mock_cls.return_value = mock_http
            call = await llm_json_call_ex(prompt="{}", db_path=db_path,
                                          operation="probe", max_attempts=3)
        assert call.success is False
        assert call.attempts == 1
        assert call.timeouts == 0

    async def test_llm_helper_503_retries_then_succeeds(self, db_path, monkeypatch):
        monkeypatch.setenv("MODEL_API_KEY", "fake-key")
        monkeypatch.setattr("app.agents.llm_helper.asyncio.sleep", AsyncMock())
        from app.agents.llm_helper import llm_json_call_ex
        calls = 0

        async def fake_post(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _status_error(503)
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value=self._payload('{"ok": true}'))
            mock_resp.raise_for_status = MagicMock(return_value=None)
            return mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(side_effect=fake_post)
            mock_cls.return_value = mock_http
            call = await llm_json_call_ex(prompt="{}", db_path=db_path,
                                          operation="probe", max_attempts=3)
        assert call.success is True
        assert call.attempts == 2
        assert call.data == {"ok": True}


# ── Agent + investigation timeouts ─────────────────────────────────────────

@pytest.mark.asyncio
class TestPipelineTimeouts:
    async def _seed(self, db_path):
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1, camera_name="Cam")
            await insert_event(db, RECORDING_ID_1, action_description="person detected",
                               object_descriptions=["person"],
                               observed_facts=["Observed: person"])

    async def test_slow_agent_times_out_pipeline_continues(self, db_path, monkeypatch):
        from app.agents import temporal_reasoning
        from app.agents.base import AgentResult, AgentStatus
        from app.agents.orchestrator import InvestigationOrchestrator
        await self._seed(db_path)

        async def slow_run(ctx):
            await asyncio.sleep(5)
            return AgentResult(agent_name="TemporalReasoningAgent",
                               status=AgentStatus.COMPLETED, output=None, duration_ms=0.0)

        monkeypatch.setattr(temporal_reasoning, "run", slow_run)
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=0.0,
                                         agent_timeout_s=0.05,
                                         investigation_timeout_s=30.0)
        result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        by_name = {a["agent_name"]: a for a in result["agents"]}
        assert by_name["TemporalReasoningAgent"]["status"] == "timed_out"
        assert "timeout" in by_name["TemporalReasoningAgent"]["error"]
        assert by_name["TemporalReasoningAgent"]["timeouts"] == 1
        # Other agents still ran; nothing fabricated as completed.
        assert by_name["QueryInvestigationAgent"]["status"] == "completed"
        assert result["status"] == "PARTIAL"
        assert len(result["agents"]) == 8
        assert result["final_report"] is not None

    async def test_overall_deadline_persists_partial_trace(self, db_path, monkeypatch):
        from app.agents import video_intelligence
        from app.agents.base import AgentResult, AgentStatus
        from app.agents.orchestrator import InvestigationOrchestrator
        await self._seed(db_path)

        async def slow_run(ctx):
            await asyncio.sleep(5)
            return AgentResult(agent_name="VideoIntelligenceAgent",
                               status=AgentStatus.COMPLETED, output=None, duration_ms=0.0)

        monkeypatch.setattr(video_intelligence, "run", slow_run)
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=1.0,
                                         agent_timeout_s=30.0,
                                         investigation_timeout_s=0.05)
        result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        assert result["status"] == "PARTIAL"
        assert len(result["agents"]) == 8
        by_name = {a["agent_name"]: a for a in result["agents"]}
        # In-flight agent is timed_out; later agents never started.
        assert by_name["VideoIntelligenceAgent"]["status"] == "timed_out"
        assert by_name["ObjectActionAnalystAgent"]["status"] == "skipped"
        assert by_name["QueryInvestigationAgent"]["status"] == "completed"
        # Best-effort report from verified evidence still produced.
        assert result["final_report"] is not None

        import aiosqlite
        async with get_db(db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT status, agent_results FROM investigation_traces "
                "WHERE trace_id = ?", (result["trace_id"],))).fetchone()
        assert row["status"] == "PARTIAL"
        assert len(json.loads(row["agent_results"])) == 8

    async def test_deadline_before_retrieval_has_no_report(self, db_path, monkeypatch):
        from app.agents import query_investigation
        from app.agents.base import AgentResult, AgentStatus
        from app.agents.orchestrator import InvestigationOrchestrator
        await self._seed(db_path)

        async def slow_run(ctx):
            await asyncio.sleep(5)
            return AgentResult(agent_name="QueryInvestigationAgent",
                               status=AgentStatus.COMPLETED, output=None, duration_ms=0.0)

        monkeypatch.setattr(query_investigation, "run", slow_run)
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=0.0,
                                         agent_timeout_s=30.0,
                                         investigation_timeout_s=0.05)
        result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        assert result["status"] == "PARTIAL"
        # No retrieval happened: no report is synthesized, never fabricated.
        assert result["final_report"] is None


# ── Concurrency + cost caps ──────────────────────────────────────────────

class TestConcurrencyCap:
    def test_counter_allows_up_to_limit(self):
        assert try_acquire_investigation_slot(2) is True
        assert try_acquire_investigation_slot(2) is True
        assert try_acquire_investigation_slot(2) is False
        release_investigation_slot(2)
        assert try_acquire_investigation_slot(2) is True

    def test_zero_disables_cap(self):
        assert try_acquire_investigation_slot(0) is True


@pytest.mark.asyncio
class TestInvestigationConcurrencyEndpoint:
    async def test_503_when_full(self, client, tmp_data_dir):
        from app.config import get_settings
        settings = get_settings()
        limit = settings.investigation_max_concurrent
        assert limit > 0
        for _ in range(limit):
            assert try_acquire_investigation_slot(limit) is True
        try:
            resp = await client.post("/api/investigations/",
                                     json={"query": "person"})
            assert resp.status_code == 503
            assert resp.headers.get("Retry-After") == "5"
        finally:
            for _ in range(limit):
                release_investigation_slot(limit)
        get_settings.cache_clear()

    async def test_llm_call_cap_skips_llm_agents(self, db_path):
        from app.agents.orchestrator import InvestigationOrchestrator
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1, camera_name="Cam")
            await insert_event(db, RECORDING_ID_1, action_description="person detected",
                               object_descriptions=["person"],
                               observed_facts=["Observed: person"])
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=5.0,
                                         max_llm_calls=0)
        result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        by_name = {a["agent_name"]: a for a in result["agents"]}
        assert by_name["VideoIntelligenceAgent"]["status"] == "skipped"
        assert "model-call cap" in by_name["VideoIntelligenceAgent"]["error"]
        assert result["llm_calls_made"] == 0
        assert result["llm_call_cap"] == 0

    async def test_llm_calls_counted(self, db_path):
        from app.agents.orchestrator import InvestigationOrchestrator
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1, camera_name="Cam")
            await insert_event(db, RECORDING_ID_1, action_description="person detected",
                               object_descriptions=["person"],
                               observed_facts=["Observed: person"],
                               inferred_claims=["claim needing audit"])
        # Budget > 0, no API keys: LLM agents run mocked and consume call slots.
        orch = InvestigationOrchestrator(db_path=db_path, budget_usd=5.0)
        result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        assert result["llm_calls_made"] == 4
        assert result["llm_call_cap"] == 8
        assert result["retries_total"] == 0
        assert result["timeouts_total"] == 0


# ── Cache: hit / miss / invalidation / prune ─────────────────────────────

@pytest.mark.asyncio
class TestVersionedCache:
    async def test_store_and_hit(self, db_path):
        await create_tables(db_path)
        async with get_db(db_path) as db:
            await store_cached_analysis(db, "h1", "m1", {"objects": []})
            await db.commit()
            hit = await get_cached_analysis(db, "h1", "m1")
        assert hit == {"objects": []}

    async def test_miss_on_other_model(self, db_path):
        await create_tables(db_path)
        async with get_db(db_path) as db:
            await store_cached_analysis(db, "h1", "m1", {"objects": []})
            await db.commit()
            assert await get_cached_analysis(db, "h1", "m2") is None

    async def test_legacy_prompt_version_never_hits(self, db_path):
        """v1 rows (pre-Phase-4 prompt) are invisible to current lookups."""
        await create_tables(db_path)
        async with get_db(db_path) as db:
            await store_cached_analysis(db, "h1", "m1", {"objects": []},
                                        prompt_version="1")
            await db.commit()
            assert await get_cached_analysis(db, "h1", "m1",
                                             prompt_version=PROMPT_VERSION) is None
            # ...but the legacy row is intact under its own version.
            legacy = await get_cached_analysis(db, "h1", "m1", prompt_version="1")
            assert legacy == {"objects": []}

    async def test_corrupt_json_is_miss(self, db_path):
        await create_tables(db_path)
        async with get_db(db_path) as db:
            await db.execute(
                "INSERT INTO frame_cache(content_hash, model_version, prompt_version,"
                " analysis_result, created_at) VALUES(?,?,?,?,?)",
                ("h1", "m1", PROMPT_VERSION, "not-json{{{", "2026-01-01T00:00:00+00:00"))
            await db.commit()
            assert await get_cached_analysis(db, "h1", "m1") is None

    async def test_prune_deletes_only_old_rows(self, db_path):
        await create_tables(db_path)
        async with get_db(db_path) as db:
            await db.execute(
                "INSERT INTO frame_cache(content_hash, model_version, prompt_version,"
                " analysis_result, created_at) VALUES(?,?,?,?,?)",
                ("old", "m1", PROMPT_VERSION, "{}", "2020-01-01T00:00:00+00:00"))
            await db.execute(
                "INSERT INTO frame_cache(content_hash, model_version, prompt_version,"
                " analysis_result, created_at) VALUES(?,?,?,?,?)",
                ("new", "m1", PROMPT_VERSION, "{}",
                 "2999-01-01T00:00:00+00:00"))
            await db.commit()
        deleted = await prune_frame_cache(db_path, retention_days=30)
        assert deleted == 1
        async with get_db(db_path) as db:
            rows = await (await db.execute(
                "SELECT content_hash FROM frame_cache")).fetchall()
        assert [r[0] for r in rows] == ["new"]

    async def test_prune_zero_keeps_everything(self, db_path):
        await create_tables(db_path)
        async with get_db(db_path) as db:
            await db.execute(
                "INSERT INTO frame_cache(content_hash, model_version, prompt_version,"
                " analysis_result, created_at) VALUES(?,?,?,?,?)",
                ("old", "m1", PROMPT_VERSION, "{}", "2020-01-01T00:00:00+00:00"))
            await db.commit()
        assert await prune_frame_cache(db_path, retention_days=0) == 0


# ── Upload hardening ─────────────────────────────────────────────────────

FAKE_MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 100


class TestContainerSniffing:
    def test_mp4_family_detected(self):
        assert sniff_video_container(FAKE_MP4) == "mp4"

    def test_avi_detected(self):
        assert sniff_video_container(b"RIFF\x00\x00\x00\x00AVI ") == "avi"

    def test_mkv_ebml_detected(self):
        assert sniff_video_container(b"\x1a\x45\xdf\xa3" + b"\x00" * 8) == "mkv"

    def test_unknown_allowed(self):
        assert sniff_video_container(b"\x00" * 100) is None

    @pytest.mark.parametrize("blob", [b"<html>" + b"x" * 20, b'{"a": 1}' + b"x" * 20,
                                      b"%PDF-1.4" + b"x" * 20, b"\x89PNG\r\n\x1a\n" + b"x" * 20,
                                      b"\xff\xd8\xff\xe0" + b"x" * 20])
    def test_clear_non_video_rejected(self, blob):
        with pytest.raises(ValueError, match="not look like a video"):
            sniff_video_container(blob)


@pytest.mark.asyncio
class TestUploadHardening:
    async def test_duplicate_bytes_create_separate_recordings_with_same_hash(
            self, client, tmp_data_dir):
        """Pinned behavior: each upload is its own recording; the content hash
        records the duplication for observability (analysis is deduped via
        the content-hash frame cache instead)."""
        from app.config import get_settings
        ids = []
        for _ in range(2):
            resp = await client.post(
                "/api/recordings/upload",
                files={"file": ("dup.mp4", FAKE_MP4, "video/mp4")},
            )
            assert resp.status_code == 202
            ids.append(resp.json()["recording_id"])
        assert ids[0] != ids[1]
        app_db = str(tmp_data_dir / "findit.db")
        async with get_db(app_db) as db:
            rows = await (await db.execute(
                "SELECT recording_id, content_hash FROM recordings")).fetchall()
        by_id = {r[0]: r[1] for r in rows}
        assert by_id[ids[0]] == by_id[ids[1]]
        assert by_id[ids[0]] is not None
        get_settings.cache_clear()

    async def test_magic_mismatch_rejected(self, client, tmp_data_dir):
        from app.config import get_settings
        resp = await client.post(
            "/api/recordings/upload",
            files={"file": ("evil.mp4", b"<html>not video</html>" + b"x" * 50,
                            "video/mp4")},
        )
        assert resp.status_code == 400
        assert "not look like a video" in resp.json()["detail"]
        get_settings.cache_clear()

    async def test_oversized_rejected(self, client, tmp_data_dir, _env_settings):
        from app.config import get_settings
        _env_settings(FINDIT_MAX_VIDEO_SIZE_MB="0")  # 0 bytes allowed
        resp = await client.post(
            "/api/recordings/upload",
            files={"file": ("big.mp4", FAKE_MP4, "video/mp4")},
        )
        assert resp.status_code == 413
        get_settings.cache_clear()

    async def test_quota_enforced(self, client, tmp_data_dir, _env_settings):
        from app.config import get_settings
        _env_settings(UPLOAD_MAX_RECORDINGS="1")
        first = await client.post(
            "/api/recordings/upload",
            files={"file": ("a.mp4", FAKE_MP4, "video/mp4")},
        )
        assert first.status_code == 202
        second = await client.post(
            "/api/recordings/upload",
            files={"file": ("b.mp4", FAKE_MP4, "video/mp4")},
        )
        assert second.status_code == 429
        assert second.headers.get("Retry-After") == "60"
        get_settings.cache_clear()

    async def test_bad_datetime_rejected(self, client, tmp_data_dir):
        from app.config import get_settings
        resp = await client.post(
            "/api/recordings/upload",
            files={"file": ("a.mp4", FAKE_MP4, "video/mp4")},
            data={"recording_datetime": "yesterday-ish"},
        )
        assert resp.status_code == 400
        get_settings.cache_clear()

    async def test_oversized_form_field_rejected(self, client, tmp_data_dir):
        from app.config import get_settings
        resp = await client.post(
            "/api/recordings/upload",
            files={"file": ("a.mp4", FAKE_MP4, "video/mp4")},
            data={"camera_name": "x" * 500},
        )
        assert resp.status_code == 400
        get_settings.cache_clear()


# ── Path traversal ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestPathTraversal:
    async def test_getters_reject_traversal(self, tmp_data_dir):
        store = LocalStorageBackend(tmp_data_dir)
        # NOTE: backslash paths are inert literal filenames on POSIX and are
        # not traversal there; Windows-only semantics are not asserted here.
        for evil in ("../../etc/passwd", "/etc/passwd",
                     "videos/../../etc/passwd", "", "   "):
            with pytest.raises(ValueError, match="[Tt]raversal"):
                await store.get_video_path(evil)
            with pytest.raises(ValueError, match="[Tt]raversal"):
                await store.get_frame_path(evil)

    async def test_sibling_prefix_bypass_rejected(self, tmp_path):
        """<data>_evil must not pass via a startswith-style prefix match."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        evil_dir = tmp_path / "data_evil"
        evil_dir.mkdir()
        (evil_dir / "x.mp4").write_bytes(b"evil")
        store = LocalStorageBackend(data_dir)
        with pytest.raises(ValueError, match="[Tt]raversal"):
            await store.get_video_path("../data_evil/x.mp4")

    async def test_delete_thumbnail_rejects_traversal(self, tmp_data_dir):
        store = LocalStorageBackend(tmp_data_dir)
        with pytest.raises(ValueError, match="[Tt]raversal"):
            await store.delete_thumbnail("../../etc/passwd")

    async def test_save_rejects_bad_recording_id(self, tmp_data_dir):
        store = LocalStorageBackend(tmp_data_dir)
        with pytest.raises(ValueError, match="recording_id"):
            await store.save_video("../evil", "x.mp4", b"data")
        with pytest.raises(ValueError, match="recording_id"):
            await store.save_thumbnail("not-a-uuid", b"data")


# ── HTTP Range validation ────────────────────────────────────────────────

STREAM_UUID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
STREAM_BYTES = b"\x00\x00\x00\x18ftypisom" + b"\xAB" * 2048


@pytest.mark.asyncio
class TestRangeValidation:
    async def _seed(self, tmp_data_dir):
        app_db = str(tmp_data_dir / "findit.db")
        videos = tmp_data_dir / "videos"
        videos.mkdir(parents=True, exist_ok=True)
        (videos / f"{STREAM_UUID}.mp4").write_bytes(STREAM_BYTES)
        now = "2026-01-01T00:00:00+00:00"
        await create_tables(app_db)
        async with get_db(app_db) as db:
            await db.execute(
                """INSERT INTO recordings(
                    recording_id, original_filename, upload_timestamp,
                    video_path, processing_status, frame_count,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (STREAM_UUID, "test.mp4", now, f"videos/{STREAM_UUID}.mp4",
                 "INDEXED", 0, now, now),
            )
            await db.commit()

    async def test_malformed_range_416(self, client, tmp_data_dir):
        await self._seed(tmp_data_dir)
        for bad in ("bytes=abc", "bytes=-500", "items=0-10", "bytes=5", "bytes=--"):
            resp = await client.get(f"/api/recordings/{STREAM_UUID}/video",
                                    headers={"Range": bad})
            assert resp.status_code == 416, bad

    async def test_start_past_end_416(self, client, tmp_data_dir):
        await self._seed(tmp_data_dir)
        resp = await client.get(f"/api/recordings/{STREAM_UUID}/video",
                                headers={"Range": "bytes=2000-100"})
        assert resp.status_code == 416

    async def test_start_beyond_size_416(self, client, tmp_data_dir):
        await self._seed(tmp_data_dir)
        resp = await client.get(f"/api/recordings/{STREAM_UUID}/video",
                                headers={"Range": f"bytes={len(STREAM_BYTES) + 100}-"})
        assert resp.status_code == 416

    async def test_valid_range_still_206(self, client, tmp_data_dir):
        await self._seed(tmp_data_dir)
        resp = await client.get(f"/api/recordings/{STREAM_UUID}/video",
                                headers={"Range": "bytes=0-99"})
        assert resp.status_code == 206
        assert resp.content == STREAM_BYTES[:100]


# ── API token gate ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestApiTokenGate:
    async def test_mutations_open_without_token(self, client, tmp_data_dir):
        from app.config import get_settings
        resp = await client.post("/api/investigations/", json={"query": "x"})
        assert resp.status_code == 200
        get_settings.cache_clear()

    async def test_mutations_require_token_when_configured(
            self, client, tmp_data_dir, _env_settings):
        from app.config import get_settings
        _env_settings(API_TOKEN="s3cret-token")
        denied = await client.post("/api/investigations/", json={"query": "x"})
        assert denied.status_code == 401
        assert "token" in denied.json()["detail"].lower()

        wrong = await client.post("/api/investigations/", json={"query": "x"},
                                  headers={"X-API-Token": "wrong"})
        assert wrong.status_code == 401

        allowed = await client.post("/api/investigations/", json={"query": "x"},
                                    headers={"X-API-Token": "s3cret-token"})
        assert allowed.status_code == 200
        get_settings.cache_clear()

    async def test_reads_stay_open_with_token_configured(
            self, client, tmp_data_dir, _env_settings):
        from app.config import get_settings
        _env_settings(API_TOKEN="s3cret-token")
        resp = await client.get("/api/recordings/")
        assert resp.status_code == 200
        get_settings.cache_clear()

    async def test_upload_and_delete_gated(self, client, tmp_data_dir, _env_settings):
        from app.config import get_settings
        _env_settings(API_TOKEN="s3cret-token")
        denied = await client.post(
            "/api/recordings/upload",
            files={"file": ("a.mp4", FAKE_MP4, "video/mp4")},
        )
        assert denied.status_code == 401
        resp = await client.post(
            "/api/recordings/upload",
            files={"file": ("a.mp4", FAKE_MP4, "video/mp4")},
            headers={"X-API-Token": "s3cret-token"},
        )
        assert resp.status_code == 202
        rid = resp.json()["recording_id"]
        del_denied = await client.delete(f"/api/recordings/{rid}")
        assert del_denied.status_code == 401
        del_ok = await client.delete(f"/api/recordings/{rid}",
                                     headers={"X-API-Token": "s3cret-token"})
        assert del_ok.status_code == 204
        get_settings.cache_clear()


# ── Credential leakage ───────────────────────────────────────────────────

@pytest.mark.asyncio
class TestCredentialLeakage:
    async def test_failure_paths_never_echo_key(self, db_path, monkeypatch, caplog):
        """Failing provider calls must not leak the key in logs/errors/rows."""
        import logging
        secret = "leak-check-secret-001"
        monkeypatch.setenv("MODEL_API_KEY", secret)

        async def fake_post(*args, **kwargs):
            raise _status_error(500)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(side_effect=fake_post)
            mock_cls.return_value = mock_http
            from app.agents.llm_helper import llm_json_call_ex
            with caplog.at_level(logging.DEBUG):
                call = await llm_json_call_ex(prompt="{}", db_path=db_path,
                                              operation="probe", max_attempts=1)
        assert call.success is False
        for record in caplog.records:
            assert secret not in record.getMessage()
        async with get_db(db_path) as db:
            rows = await (await db.execute("SELECT * FROM model_usage")).fetchall()
        assert secret not in json.dumps([list(r) for r in rows])

    async def test_trace_contains_no_key(self, db_path, monkeypatch, _env_settings):
        secret = "leak-check-secret-002"
        monkeypatch.setenv("MODEL_API_KEY", secret)
        _env_settings(MAX_API_ATTEMPTS="1")  # fail fast: no backoff sleeps
        from app.agents.orchestrator import InvestigationOrchestrator
        async with get_db(db_path) as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            await insert_recording(db, RECORDING_ID_1, camera_name="Cam")
            await insert_event(db, RECORDING_ID_1, action_description="person detected")
        # Mock the transport so no real call happens; the key must still not
        # appear anywhere in the persisted trace.
        async def fake_post(*args, **kwargs):
            raise httpx.ConnectError("unreachable")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(side_effect=fake_post)
            mock_cls.return_value = mock_http
            orch = InvestigationOrchestrator(db_path=db_path, budget_usd=5.0)
            result = await orch.run(query="person", recording_ids=[RECORDING_ID_1])
        assert secret not in json.dumps(result)
        import aiosqlite
        async with get_db(db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT agent_results, final_report, model_config, usage_summary"
                " FROM investigation_traces WHERE trace_id = ?",
                (result["trace_id"],))).fetchone()
        assert secret not in json.dumps(dict(row))

    async def test_frontend_source_has_no_credential_references(self):
        """No credential identifiers may live in browser code.

        (tokens_used is usage accounting, not a credential — the pattern
        targets credential-shaped identifiers only.)
        """
        import re
        frontend_src = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
        hits = []
        for path in frontend_src.rglob("*"):
            if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".jsx"}:
                text = path.read_text()
                if re.search(r"API_KEY|SECRET|PRIVATE_KEY|PASSWORD|BEARER|X-Api-Token",
                             text, re.IGNORECASE):
                    hits.append(str(path))
        assert hits == []


# ── Request IDs + rate limits + input bounds ─────────────────────────────

class TestRequestIdsAndLimiter:
    def test_normalize_accepts_safe_ids(self):
        assert normalize_request_id("abc-123_X", "gen") == "abc-123_X"

    def test_normalize_rejects_unsafe_ids(self):
        assert normalize_request_id("../../x", "gen") == "gen"
        assert normalize_request_id("x" * 65, "gen") == "gen"
        assert normalize_request_id(None, "gen") == "gen"

    def test_limiter_allows_then_denies(self):
        for _ in range(3):
            allowed, _ = check_rate_limit("k1", 3)
            assert allowed is True
        allowed, retry_after = check_rate_limit("k1", 3)
        assert allowed is False
        assert 0.0 < retry_after <= 60.0

    def test_limiter_zero_disables(self):
        allowed, _ = check_rate_limit("k2", 0)
        assert allowed is True

    def test_limiter_keys_isolated(self):
        allowed, _ = check_rate_limit("ka", 1)
        assert allowed is True
        allowed, _ = check_rate_limit("ka", 1)
        assert allowed is False
        allowed, _ = check_rate_limit("kb", 1)
        assert allowed is True


@pytest.mark.asyncio
class TestRequestIdAndRateLimitEndpoints:
    async def test_request_id_header_and_echo(self, client, tmp_data_dir):
        from app.config import get_settings
        resp = await client.post("/api/investigations/", json={"query": "x"})
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID")
        assert resp.json()["request_id"] == resp.headers["X-Request-ID"]
        get_settings.cache_clear()

    async def test_caller_request_id_honored(self, client, tmp_data_dir):
        from app.config import get_settings
        resp = await client.post("/api/investigations/", json={"query": "x"},
                                 headers={"X-Request-ID": "demo-req-1"})
        assert resp.headers.get("X-Request-ID") == "demo-req-1"
        assert resp.json()["request_id"] == "demo-req-1"
        get_settings.cache_clear()

    async def test_rate_limited_endpoint_429(self, client, tmp_data_dir, _env_settings):
        from app.config import get_settings
        _env_settings(RATE_LIMIT_PER_MINUTE="2")
        assert (await client.post("/api/investigations/", json={"query": "a"})).status_code == 200
        assert (await client.post("/api/investigations/", json={"query": "b"})).status_code == 200
        third = await client.post("/api/investigations/", json={"query": "c"})
        assert third.status_code == 429
        assert "Retry-After" in third.headers
        assert third.headers.get("X-Request-ID")
        get_settings.cache_clear()

    async def test_oversized_query_rejected(self, client, tmp_data_dir):
        from app.config import get_settings
        resp = await client.post("/api/investigations/", json={"query": "x" * 5000})
        assert resp.status_code == 422
        get_settings.cache_clear()

    async def test_too_many_recording_ids_rejected(self, client, tmp_data_dir):
        from app.config import get_settings
        resp = await client.post(
            "/api/investigations/",
            json={"query": "x", "recording_ids": [f"id-{i}" for i in range(60)]})
        assert resp.status_code == 422
        get_settings.cache_clear()

    async def test_negative_budget_rejected(self, client, tmp_data_dir):
        from app.config import get_settings
        resp = await client.post("/api/investigations/multi-agent",
                                 json={"query": "x", "budget_usd": -1.0})
        assert resp.status_code == 422
        get_settings.cache_clear()

    async def test_negative_time_rejected(self, client, tmp_data_dir):
        from app.config import get_settings
        resp = await client.post("/api/investigations/",
                                 json={"query": "x", "time_start_seconds": -5.0})
        assert resp.status_code == 422
        get_settings.cache_clear()


# ── Decoder timeouts ─────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDecoderTimeouts:
    def _slow_proc(self):
        proc = MagicMock()

        async def slow_communicate():
            await asyncio.sleep(5)
            return (b"", b"")

        proc.communicate = slow_communicate
        proc.kill = MagicMock()
        proc.returncode = 0
        return proc

    async def test_extract_frames_timeout_kills_proc(self, tmp_path, monkeypatch):
        import app.processor as processor
        monkeypatch.setattr(processor, "_ffmpeg_timeout_s", lambda: 0.05)
        proc = self._slow_proc()

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(processor.asyncio, "create_subprocess_exec", fake_exec)
        with pytest.raises(RuntimeError, match="timed out"):
            await processor.extract_frames(tmp_path / "v.mp4", tmp_path / "out")
        assert proc.kill.called

    async def test_get_video_duration_timeout_returns_none(self, tmp_path, monkeypatch):
        import app.processor as processor
        monkeypatch.setattr(processor, "_ffmpeg_timeout_s", lambda: 0.05)
        proc = self._slow_proc()

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(processor.asyncio, "create_subprocess_exec", fake_exec)
        assert await processor.get_video_duration(tmp_path / "v.mp4") is None
        assert proc.kill.called
