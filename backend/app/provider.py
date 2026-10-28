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
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import aiosqlite
import httpx

from .database import get_db
from .meta_client import FrameAnalyzer

logger = logging.getLogger(__name__)

# Cost per 1K tokens — source: dev.meta.ai/docs/pricing-rate-limits
# Standard mode: input $1.25/1M = $0.00125/1K, output $4.25/1M = $0.00425/1K
# Contributor mode: input $0.10/1M = $0.0001/1K, output $0.20/1M = $0.0002/1K
# Using standard mode rates; switch to contributor IDs if access granted.
_COST_PER_1K_TOKENS_IN: dict[str, float] = {
    "muse-spark-1.3": 0.00125,
    "muse-spark-1.2": 0.00125,
    "muse-spark-1.3-contributor": 0.0001,
    "muse-spark-1.2-contributor": 0.0001,
    "mock-v1": 0.0,
}
_COST_PER_1K_TOKENS_OUT: dict[str, float] = {
    "muse-spark-1.3": 0.00425,
    "muse-spark-1.2": 0.00425,
    "muse-spark-1.3-contributor": 0.0002,
    "muse-spark-1.2-contributor": 0.0002,
    "mock-v1": 0.0,
}
# Vision: images billed as tokens; no separate flat per-image fee published.
_COST_PER_IMAGE: dict[str, float] = {
    "muse-spark-1.3": 0.0,
    "muse-spark-1.2": 0.0,
    "muse-spark-1.3-contributor": 0.0,
    "muse-spark-1.2-contributor": 0.0,
    "mock-v1": 0.0,
}

MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 8.0

# Phase 6: only transient failures are retried. Everything else — auth
# failures, malformed requests/responses, budget guards — fails fast so a
# retry that cannot succeed never burns another expensive request.
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
RETRYABLE_TRANSPORT_ERRORS = (
    httpx.TimeoutException,  # ConnectTimeout, ReadTimeout, WriteTimeout, PoolTimeout
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
)


class BudgetExceededError(RuntimeError):
    """Raised before a call that would exhaust the configured budget reserve."""


def is_transient_error(exc: BaseException) -> bool:
    """True when retrying the same request could plausibly succeed."""
    if isinstance(exc, BudgetExceededError):
        return False
    if isinstance(exc, ValueError):
        # Malformed model response / bad arguments — same request, same result.
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            status = exc.response.status_code
        except Exception:
            return False
        return status in RETRYABLE_STATUS_CODES
    if isinstance(exc, RETRYABLE_TRANSPORT_ERRORS):
        return True
    if isinstance(exc, asyncio.TimeoutError):
        return True
    return False


def compute_backoff_seconds(
    failed_attempt: int,
    base_seconds: float = BASE_BACKOFF_SECONDS,
    max_seconds: float = MAX_BACKOFF_SECONDS,
    jitter: bool = True,
) -> float:
    """Exponential backoff with full jitter. failed_attempt is 1-based."""
    capped = min(base_seconds * (2 ** max(failed_attempt - 1, 0)), max_seconds)
    if jitter and capped > 0:
        return random.uniform(0, capped)
    return capped


def retry_after_seconds(
    exc: BaseException,
    default_seconds: float,
    max_seconds: float = MAX_BACKOFF_SECONDS,
) -> float:
    """Honor the server's Retry-After header (seconds or HTTP date) when present."""
    try:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) if response is not None else None
        raw = headers.get("retry-after") if headers else None
    except Exception:
        return default_seconds
    if not raw:
        return default_seconds
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(raw))
            now = datetime.now(timezone.utc)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            delay = (retry_at - now).total_seconds()
        except Exception:
            return default_seconds
    if delay < 0:
        return default_seconds
    return min(delay, max_seconds)


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


async def get_cache_stats(db_path: str) -> dict:
    """Phase 6: frame-cache size and prompt-version breakdown (observability).

    Tolerates pre-Phase-6 databases without the prompt_version column.
    """
    async with get_db(db_path) as db:
        try:
            rows = await (await db.execute(
                "SELECT prompt_version, COUNT(*) FROM frame_cache "
                "GROUP BY prompt_version")).fetchall()
            by_version = {str(r[0]): r[1] for r in rows}
            total = sum(by_version.values())
        except Exception:
            row = await (await db.execute(
                "SELECT COUNT(*) FROM frame_cache")).fetchone()
            total = row[0] if row else 0
            by_version = {}
        return {"cached_analyses": total, "by_prompt_version": by_version}


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
        max_backoff: float = MAX_BACKOFF_SECONDS,
        retry_jitter: bool = True,
    ):
        self.delegate = delegate
        self.db_path = db_path
        self.provider = provider
        self.budget_limit_usd = budget_limit_usd
        self.budget_reserve_usd = budget_reserve_usd
        self.recording_id = recording_id
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.retry_jitter = retry_jitter
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
            max_backoff=self.max_backoff,
            retry_jitter=self.retry_jitter,
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
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            # Budget is re-checked before every attempt so retries stop as
            # soon as the reserve would be breached.
            await self._check_budget()

            t0 = time.monotonic()
            try:
                result = await self.delegate.analyze_frame(frame_data)
                latency_ms = (time.monotonic() - t0) * 1000.0

                # Extract actual token counts if the delegate embedded them.
                # MuseSparkClient attaches _usage; MockMetaClient does not.
                import json as _json
                usage_meta = result.pop("_usage", None) or {}
                tokens_in_actual = usage_meta.get("tokens_in", 0)
                if tokens_in_actual == 0:
                    # Heuristic fallback: 4 chars ≈ 1 token for output JSON.
                    # Input tokens estimated as ~546 per frame (measured average).
                    tokens_in_actual = 0  # leave at 0 for mock; real client always provides
                tokens_out_actual = usage_meta.get("tokens_out", 0)
                if tokens_out_actual == 0:
                    response_text = _json.dumps(result)
                    tokens_out_actual = max(1, len(response_text) // 4)

                cost = _estimate_cost(
                    self.delegate.model_version,
                    tokens_in=tokens_in_actual,
                    tokens_out=tokens_out_actual,
                    images=1,
                )
                record = UsageRecord(
                    provider=self.provider,
                    model=self.delegate.model_version,
                    operation="analyze_frame",
                    tokens_in=tokens_in_actual,
                    tokens_out=tokens_out_actual,
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

                # Phase 6: retry only transient failures (429/5xx/transport).
                # Auth failures, malformed requests/responses, and unexpected
                # errors fail fast — a retry could not succeed.
                if not is_transient_error(exc):
                    logger.warning(
                        "analyze_frame attempt %d/%d failed with non-retryable %s; "
                        "not retrying",
                        attempt,
                        self.max_attempts,
                        type(exc).__name__,
                    )
                    raise

                if attempt < self.max_attempts:
                    backoff = compute_backoff_seconds(
                        attempt,
                        base_seconds=self.base_backoff,
                        max_seconds=self.max_backoff,
                        jitter=self.retry_jitter,
                    )
                    backoff = retry_after_seconds(
                        exc, backoff, max_seconds=self.max_backoff
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
    """Factory: wrap any FrameAnalyzer with accounting and budget safeguards.

    Retry behavior comes from Settings when available (Phase 6), falling
    back to module defaults.
    """
    max_attempts, base_backoff, max_backoff, jitter = (
        MAX_ATTEMPTS, BASE_BACKOFF_SECONDS, MAX_BACKOFF_SECONDS, True,
    )
    try:
        from .config import get_settings
        settings = get_settings()
        max_attempts = settings.max_api_attempts
        base_backoff = settings.base_backoff_seconds
        max_backoff = settings.meta_backoff_max_s
        jitter = settings.meta_retry_jitter
    except Exception:
        pass
    return AccountedFrameAnalyzer(
        delegate=delegate,
        db_path=db_path,
        provider=provider,
        budget_limit_usd=budget_limit_usd,
        budget_reserve_usd=budget_reserve_usd,
        max_attempts=max_attempts,
        base_backoff=base_backoff,
        max_backoff=max_backoff,
        retry_jitter=jitter,
    )
