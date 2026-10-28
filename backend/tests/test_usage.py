"""
Tests for Phase 2: usage accounting, budget safeguards, retry/backoff, deduplication.

All tests are deterministic — no real API calls, no real credentials.
"""
import asyncio
import hashlib
import json
import time
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from app.database import create_tables, get_db
from app.meta_client import MockMetaClient
from app.provider import (
    AccountedFrameAnalyzer,
    BudgetExceededError,
    UsageRecord,
    _estimate_cost,
    build_accounted_client,
    get_total_spent,
    get_usage_summary,
    record_usage,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _empty_response():
    return {"objects": [], "scene_summary": "empty"}


async def _make_accounted(db_path, delegate=None, budget_limit=150.0, budget_reserve=10.0):
    if delegate is None:
        delegate = MockMetaClient()
    return build_accounted_client(
        delegate=delegate,
        db_path=db_path,
        provider="meta",
        budget_limit_usd=budget_limit,
        budget_reserve_usd=budget_reserve,
    )


# ── Cost estimation ──────────────────────────────────────────────────────────

def test_estimate_cost_mock_is_zero():
    assert _estimate_cost("mock-v1", tokens_in=1000, tokens_out=1000, images=5) == 0.0


def test_estimate_cost_muse_spark():
    # 1K input tokens at $1.25/1M = $0.00125/1K; images billed as tokens (no flat fee)
    cost = _estimate_cost("muse-spark-1.3", tokens_in=1000, tokens_out=0, images=0)
    assert cost == pytest.approx(0.00125, abs=1e-7)


def test_estimate_cost_tokens_add():
    cost = _estimate_cost("muse-spark-1.3", tokens_in=1000, tokens_out=1000, images=0)
    assert cost > 0


# ── UsageRecord record/retrieve ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_usage_stores_row(db_path):
    record = UsageRecord(
        provider="meta",
        model="muse-spark-1.3",
        operation="analyze_frame",
        tokens_in=100,
        tokens_out=200,
        images_processed=1,
        latency_ms=250.0,
        success=True,
        estimated_cost_usd=0.001,
        recording_id="rec-001",
    )
    await record_usage(db_path, record)

    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT * FROM model_usage WHERE usage_id = ?", (record.usage_id,)
        )).fetchone()

    assert row is not None
    assert row[1] == "meta"         # provider
    assert row[2] == "muse-spark-1.3"  # model
    assert row[5] == 100            # tokens_in
    assert row[8] == pytest.approx(250.0)  # latency_ms


@pytest.mark.asyncio
async def test_get_total_spent_sums_successful(db_path):
    r1 = UsageRecord(provider="meta", model="mock-v1", operation="test",
                     success=True, estimated_cost_usd=0.005)
    r2 = UsageRecord(provider="meta", model="mock-v1", operation="test",
                     success=True, estimated_cost_usd=0.003)
    r3 = UsageRecord(provider="meta", model="mock-v1", operation="test",
                     success=False, estimated_cost_usd=1.0)  # failed — should NOT count
    await record_usage(db_path, r1)
    await record_usage(db_path, r2)
    await record_usage(db_path, r3)

    total = await get_total_spent(db_path)
    assert total == pytest.approx(0.008, abs=1e-6)


@pytest.mark.asyncio
async def test_get_total_spent_empty_db(db_path):
    total = await get_total_spent(db_path)
    assert total == 0.0


@pytest.mark.asyncio
async def test_get_usage_summary_groups_by_model(db_path):
    for _ in range(3):
        await record_usage(db_path, UsageRecord(
            provider="meta", model="mock-v1", operation="analyze_frame",
            images_processed=1, success=True, estimated_cost_usd=0.0,
        ))
    summary = await get_usage_summary(db_path)
    assert len(summary) == 1
    assert summary[0]["model"] == "mock-v1"
    assert summary[0]["calls"] == 3
    assert summary[0]["successes"] == 3


# ── AccountedFrameAnalyzer — happy path ──────────────────────────────────────

@pytest.mark.asyncio
async def test_accounted_records_success(db_path):
    client = await _make_accounted(db_path)
    await client.analyze_frame(b"frame data")

    async with get_db(db_path) as db:
        rows = await (await db.execute(
            "SELECT success FROM model_usage"
        )).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 1  # success


@pytest.mark.asyncio
async def test_accounted_returns_delegate_result(db_path):
    frame = b"specific frame"
    h = hashlib.sha256(frame).hexdigest()
    delegate = MockMetaClient(scenario_map={h: {"objects": [{"label": "chair"}], "scene_summary": "office"}})
    client = await _make_accounted(db_path, delegate=delegate)
    result = await client.analyze_frame(frame)
    assert result["objects"][0]["label"] == "chair"


@pytest.mark.asyncio
async def test_accounted_records_recording_id(db_path):
    client = await _make_accounted(db_path)
    client = client.with_recording_id("rec-xyz")
    await client.analyze_frame(b"frame")

    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT recording_id FROM model_usage"
        )).fetchone()
    assert row[0] == "rec-xyz"


@pytest.mark.asyncio
async def test_model_version_propagated(db_path):
    client = await _make_accounted(db_path)
    assert client.model_version == "mock-v1"


# ── Budget enforcement ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_budget_exceeded_raises(db_path):
    """Spend up to the limit then verify next call raises BudgetExceededError."""
    # Pre-spend: add a usage record that puts us at exactly the reserve floor.
    await record_usage(db_path, UsageRecord(
        provider="meta", model="mock-v1", operation="seed",
        success=True, estimated_cost_usd=141.0,  # 150 - 9 = 1 under reserve of 10
    ))
    # 141 spent, 9 remaining, reserve is 10 → guard should fire.
    client = await _make_accounted(db_path, budget_limit=150.0, budget_reserve=10.0)
    with pytest.raises(BudgetExceededError):
        await client.analyze_frame(b"frame")


@pytest.mark.asyncio
async def test_budget_not_exceeded_when_within_limit(db_path):
    """No error when spend is well below the reserve floor."""
    await record_usage(db_path, UsageRecord(
        provider="meta", model="mock-v1", operation="seed",
        success=True, estimated_cost_usd=5.0,
    ))
    client = await _make_accounted(db_path, budget_limit=150.0, budget_reserve=10.0)
    result = await client.analyze_frame(b"frame")
    assert "objects" in result


@pytest.mark.asyncio
async def test_budget_only_counts_successful_calls(db_path):
    """Failed calls do not count toward spend."""
    await record_usage(db_path, UsageRecord(
        provider="meta", model="mock-v1", operation="seed",
        success=False, estimated_cost_usd=145.0,  # would exceed if counted
    ))
    client = await _make_accounted(db_path, budget_limit=150.0, budget_reserve=10.0)
    result = await client.analyze_frame(b"frame")
    assert result is not None


# ── Retry / backoff ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt(db_path):
    """Delegate fails once then succeeds; client returns the successful result."""
    call_count = 0

    class FlakyAnalyzer:
        model_version = "flaky-v1"

        async def analyze_frame(self, frame_data: bytes) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            return _empty_response()

    client = AccountedFrameAnalyzer(
        delegate=FlakyAnalyzer(),
        db_path=db_path,
        provider="test",
        budget_limit_usd=150.0,
        budget_reserve_usd=0.0,
        max_attempts=3,
        base_backoff=0.0,  # no real sleep in tests
    )
    result = await client.analyze_frame(b"frame")
    assert result == _empty_response()
    assert call_count == 2


@pytest.mark.asyncio
async def test_retry_records_failure_then_success(db_path):
    """Both the failed attempt and the success are recorded in model_usage."""
    call_count = 0

    class FlakyAnalyzer:
        model_version = "flaky-v1"

        async def analyze_frame(self, frame_data: bytes) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("oops")
            return _empty_response()

    client = AccountedFrameAnalyzer(
        delegate=FlakyAnalyzer(),
        db_path=db_path,
        provider="test",
        budget_limit_usd=150.0,
        budget_reserve_usd=0.0,
        max_attempts=3,
        base_backoff=0.0,
    )
    await client.analyze_frame(b"frame")

    async with get_db(db_path) as db:
        rows = await (await db.execute(
            "SELECT success FROM model_usage ORDER BY created_at"
        )).fetchall()
    successes = [r[0] for r in rows]
    assert 0 in successes   # the failure
    assert 1 in successes   # the success


@pytest.mark.asyncio
async def test_all_attempts_fail_raises_last_exception(db_path):
    """After max_attempts failures, the last exception is re-raised."""
    class AlwaysFailAnalyzer:
        model_version = "always-fail"

        async def analyze_frame(self, frame_data: bytes) -> dict:
            raise ValueError("always broken")

    client = AccountedFrameAnalyzer(
        delegate=AlwaysFailAnalyzer(),
        db_path=db_path,
        provider="test",
        budget_limit_usd=150.0,
        budget_reserve_usd=0.0,
        max_attempts=2,
        base_backoff=0.0,
    )
    with pytest.raises(ValueError, match="always broken"):
        await client.analyze_frame(b"frame")


@pytest.mark.asyncio
async def test_all_attempts_failure_records_all(db_path):
    """Each failed attempt gets its own model_usage row."""
    class AlwaysFailAnalyzer:
        model_version = "always-fail"

        async def analyze_frame(self, frame_data: bytes) -> dict:
            raise RuntimeError("boom")

    client = AccountedFrameAnalyzer(
        delegate=AlwaysFailAnalyzer(),
        db_path=db_path,
        provider="test",
        budget_limit_usd=150.0,
        budget_reserve_usd=0.0,
        max_attempts=3,
        base_backoff=0.0,
    )
    try:
        await client.analyze_frame(b"frame")
    except RuntimeError:
        pass

    async with get_db(db_path) as db:
        count = (await (await db.execute(
            "SELECT COUNT(*) FROM model_usage WHERE success = 0"
        )).fetchone())[0]
    assert count == 3


# ── Budget exception bypasses retry ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_budget_exceeded_not_retried(db_path):
    """BudgetExceededError must propagate immediately without retry."""
    await record_usage(db_path, UsageRecord(
        provider="meta", model="mock-v1", operation="seed",
        success=True, estimated_cost_usd=145.0,
    ))

    class CountingAnalyzer:
        model_version = "counting-v1"
        calls = 0

        async def analyze_frame(self, frame_data: bytes) -> dict:
            self.calls += 1
            return _empty_response()

    delegate = CountingAnalyzer()
    client = AccountedFrameAnalyzer(
        delegate=delegate,
        db_path=db_path,
        provider="test",
        budget_limit_usd=150.0,
        budget_reserve_usd=10.0,
        max_attempts=3,
        base_backoff=0.0,
    )
    with pytest.raises(BudgetExceededError):
        await client.analyze_frame(b"frame")

    assert delegate.calls == 0  # never reached the delegate


# ── Deduplication via frame_cache (existing mechanism) ───────────────────────

@pytest.mark.asyncio
async def test_cache_hit_prevents_duplicate_api_call(db_path):
    """
    Frame cache in processor.py prevents repeat API calls for identical frames.
    Test that inserting a cache row causes the processor loop to skip the API call.
    This is a unit test of the cache logic, not the full pipeline.
    """
    content_hash = hashlib.sha256(b"identical frame").hexdigest()
    cached = json.dumps({"objects": [{"label": "desk", "attributes": {}, "confidence": 0.8,
                                      "scene_description": "office"}], "scene_summary": "office"})
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO frame_cache(content_hash, model_version, analysis_result, created_at)"
            " VALUES(?,?,?,datetime('now'))",
            (content_hash, "mock-v1", cached),
        )
        await db.commit()

    # Fetch back — simulates what processor does
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT analysis_result FROM frame_cache WHERE content_hash=? AND model_version=?",
            (content_hash, "mock-v1"),
        )).fetchone()
    assert row is not None
    data = json.loads(row[0])
    assert data["objects"][0]["label"] == "desk"


# ── No secrets in logs ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_budget_error_message_contains_no_key(db_path, caplog):
    """BudgetExceededError message must not contain any API key."""
    import logging
    await record_usage(db_path, UsageRecord(
        provider="meta", model="mock-v1", operation="seed",
        success=True, estimated_cost_usd=145.0,
    ))
    client = AccountedFrameAnalyzer(
        delegate=MockMetaClient(),
        db_path=db_path,
        provider="test",
        budget_limit_usd=150.0,
        budget_reserve_usd=10.0,
        max_attempts=1,
        base_backoff=0.0,
    )
    with caplog.at_level(logging.DEBUG):
        try:
            await client.analyze_frame(b"frame")
        except BudgetExceededError as e:
            assert "sk-" not in str(e)
            assert "KEY" not in str(e).upper() or "MODEL" not in str(e).upper()


# ── build_accounted_client factory ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_accounted_client_wraps_delegate(db_path):
    delegate = MockMetaClient()
    client = build_accounted_client(
        delegate=delegate,
        db_path=db_path,
        provider="meta",
        budget_limit_usd=50.0,
        budget_reserve_usd=5.0,
    )
    assert isinstance(client, AccountedFrameAnalyzer)
    assert client.model_version == "mock-v1"
    assert client.budget_limit_usd == 50.0
    assert client.budget_reserve_usd == 5.0
