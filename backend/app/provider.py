"""
Provider-neutral model interface with usage accounting, budget safeguards, and retry.

Design principles:
- Never log API keys, private video content, or sensitive prompt text.
- Never silently fall back to a different provider without recording it in model_usage.
- Raise BudgetExceededError before spending money when the reserve would be breached.
- Retry up to MAX_ATTEMPTS with exponential backoff; record each attempt.
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from .database import get_db
from .meta_client import FrameAnalyzer

logger = logging.getLogger(__name__)

# Cost estimates per 1K tokens (input / output) for Meta Muse Spark 1.3.
# Update when official pricing is published.
_COST_PER_1K_TOKENS_IN: dict[str, float] = {
    "muse-spark-1.3": 0.0003,
    "mock-v1": 0.0,
}
_COST_PER_1K_TOKENS_OUT: dict[str, float] = {
    "muse-spark-1.3": 0.0006,
    "mock-v1": 0.0,
}
# Flat per-image estimate for vision calls (no separate image pricing announced).
_COST_PER_IMAGE: dict[str, float] = {
    "muse-spark-1.3": 0.001,
    "mock-v1": 0.0,
}

MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 8.0


class BudgetExceededError(RuntimeError):
    """Raised before a call that would exhaust the configured budget reserve."""


@dataclass
class UsageRecord:
    provider: str
    model: str
    operation: str
    tokens_in: int = 0
    tokens_out: int = 0
    images_processed: int = 0
    latency_ms: float = 0.0
    success: bool = True
    estimated_cost_usd: float = 0.0
    recording_id: Optional[str] = None
    fallback_from: Optional[str] = None
    usage_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _estimate_cost(model: str, tokens_in: int, tokens_out: int, images: int) -> float:
    cost = (
        tokens_in / 1000.0 * _COST_PER_1K_TOKENS_IN.get(model, 0.0)
        + tokens_out / 1000.0 * _COST_PER_1K_TOKENS_OUT.get(model, 0.0)
        + images * _COST_PER_IMAGE.get(model, 0.0)
    )
    return round(cost, 6)


async def record_usage(db_path: str, record: UsageRecord) -> None:
    """Persist a UsageRecord to the model_usage table."""
    async with get_db(db_path) as db:
        await db.execute(
            """INSERT INTO model_usage(
                usage_id, provider, model, operation, recording_id,
                tokens_in, tokens_out, images_processed, latency_ms,
                success, estimated_cost_usd, fallback_from, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record.usage_id,
                record.provider,
                record.model,
                record.operation,
                record.recording_id,
                record.tokens_in,
                record.tokens_out,
                record.images_processed,
                record.latency_ms,
                1 if record.success else 0,
                record.estimated_cost_usd,
                record.fallback_from,
                record.created_at,
            ),
        )
        await db.commit()


async def get_total_spent(db_path: str) -> float:
    """Return the sum of estimated_cost_usd for all successful calls."""
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT COALESCE(SUM(estimated_cost_usd), 0.0) FROM model_usage WHERE success = 1"
            )
        ).fetchone()
        return float(row[0]) if row else 0.0


async def get_usage_summary(db_path: str) -> dict:
    """Return per-provider/model totals for display."""
    async with get_db(db_path) as db:
        rows = await (
            await db.execute(
                """SELECT provider, model,
                       COUNT(*) as calls,
                       SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as successes,
                       SUM(tokens_in) as tokens_in,
                       SUM(tokens_out) as tokens_out,
                       SUM(images_processed) as images,
                       SUM(estimated_cost_usd) as cost_usd
                   FROM model_usage
                   GROUP BY provider, model
                   ORDER BY cost_usd DESC"""
            )
        ).fetchall()
        return [
            {
                "provider": r[0],
                "model": r[1],
                "calls": r[2],
                "successes": r[3],
                "tokens_in": r[4],
                "tokens_out": r[5],
                "images": r[6],
                "cost_usd": round(float(r[7]), 4),
            }
            for r in rows
        ]


class AccountedFrameAnalyzer:
    """
    Wraps any FrameAnalyzer with:
    - Usage recording after every call (success or failure)
    - Budget enforcement before each call
    - Retry with bounded exponential backoff
    """

    def __init__(
        self,
        delegate: FrameAnalyzer,
        db_path: str,
        provider: str = "meta",
        budget_limit_usd: float = 150.0,
        budget_reserve_usd: float = 10.0,
        recording_id: Optional[str] = None,
        max_attempts: int = MAX_ATTEMPTS,
        base_backoff: float = BASE_BACKOFF_SECONDS,
    ):
        self.delegate = delegate
        self.db_path = db_path
        self.provider = provider
        self.budget_limit_usd = budget_limit_usd
        self.budget_reserve_usd = budget_reserve_usd
        self.recording_id = recording_id
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff
        self.model_version = delegate.model_version

    def with_recording_id(self, recording_id: str) -> "AccountedFrameAnalyzer":
        """Return a new wrapper bound to a specific recording_id."""
        return AccountedFrameAnalyzer(
            delegate=self.delegate,
            db_path=self.db_path,
            provider=self.provider,
            budget_limit_usd=self.budget_limit_usd,
            budget_reserve_usd=self.budget_reserve_usd,
            recording_id=recording_id,
            max_attempts=self.max_attempts,
            base_backoff=self.base_backoff,
        )

    async def _check_budget(self) -> None:
        """Raise BudgetExceededError if spend is within the reserve floor."""
        total_spent = await get_total_spent(self.db_path)
        available = self.budget_limit_usd - total_spent
        if available <= self.budget_reserve_usd:
            raise BudgetExceededError(
                f"Budget guard triggered: ${total_spent:.4f} spent, "
                f"${available:.4f} remaining (reserve=${self.budget_reserve_usd:.2f})"
            )

    async def analyze_frame(self, frame_data: bytes) -> dict:
        await self._check_budget()

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            t0 = time.monotonic()
            try:
                result = await self.delegate.analyze_frame(frame_data)
                latency_ms = (time.monotonic() - t0) * 1000.0

                # Estimate tokens from response size (no token count in header yet).
                # Use a conservative heuristic: 4 chars ≈ 1 token.
                import json as _json
                response_text = _json.dumps(result)
                tokens_out_est = max(1, len(response_text) // 4)

                cost = _estimate_cost(
                    self.delegate.model_version,
                    tokens_in=0,
                    tokens_out=tokens_out_est,
                    images=1,
                )
                record = UsageRecord(
                    provider=self.provider,
                    model=self.delegate.model_version,
                    operation="analyze_frame",
                    tokens_in=0,
                    tokens_out=tokens_out_est,
                    images_processed=1,
                    latency_ms=latency_ms,
                    success=True,
                    estimated_cost_usd=cost,
                    recording_id=self.recording_id,
                )
                await record_usage(self.db_path, record)
                return result

            except BudgetExceededError:
                raise
            except Exception as exc:
                latency_ms = (time.monotonic() - t0) * 1000.0
                last_exc = exc

                record = UsageRecord(
                    provider=self.provider,
                    model=self.delegate.model_version,
                    operation="analyze_frame",
                    latency_ms=latency_ms,
                    success=False,
                    recording_id=self.recording_id,
                )
                await record_usage(self.db_path, record)

                if attempt < self.max_attempts:
                    backoff = min(
                        self.base_backoff * (2 ** (attempt - 1)),
                        MAX_BACKOFF_SECONDS,
                    )
                    logger.warning(
                        "analyze_frame attempt %d/%d failed (%s); retrying in %.1fs",
                        attempt,
                        self.max_attempts,
                        type(exc).__name__,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        "analyze_frame failed after %d attempts: %s",
                        self.max_attempts,
                        type(exc).__name__,
                    )

        raise last_exc  # type: ignore[misc]


def build_accounted_client(
    delegate: FrameAnalyzer,
    db_path: str,
    provider: str = "meta",
    budget_limit_usd: float = 150.0,
    budget_reserve_usd: float = 10.0,
) -> AccountedFrameAnalyzer:
    """Factory: wrap any FrameAnalyzer with accounting and budget safeguards."""
    return AccountedFrameAnalyzer(
        delegate=delegate,
        db_path=db_path,
        provider=provider,
        budget_limit_usd=budget_limit_usd,
        budget_reserve_usd=budget_reserve_usd,
    )
