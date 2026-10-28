"""Phase 6 operational guards: rate limiting, API token gate, concurrency cap.

All guards are dependency-free helpers so routes stay thin and tests stay
deterministic (no network, no wall-clock sleeps).
"""
import asyncio
import hashlib
import hmac
import re
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import HTTPException, Request

# ── Fixed-window rate limiter (per key, occurrences per 60s) ────────────────

_rate_buckets: dict[str, tuple[float, int]] = {}


def reset_rate_limiter() -> None:
    """Clear all buckets (tests call this between cases)."""
    _rate_buckets.clear()


def check_rate_limit(key: str, limit_per_minute: int) -> tuple[bool, float]:
    """Return (allowed, retry_after_seconds). limit <= 0 disables limiting."""
    if limit_per_minute <= 0:
        return True, 0.0
    now = time.monotonic()
    window_start, count = _rate_buckets.get(key, (now, 0))
    if now - window_start >= 60.0:
        window_start, count = now, 0
    if count >= limit_per_minute:
        return False, max(0.0, 60.0 - (now - window_start))
    _rate_buckets[key] = (window_start, count + 1)
    return True, 0.0


# ── Optional shared API token for mutating endpoints ───────────────────────

def _token_configured() -> str:
    try:
        from .config import get_settings
        return get_settings().api_token or ""
    except Exception:
        return ""


async def require_api_token(request: Request) -> None:
    """FastAPI dependency: when API_TOKEN is configured, mutating routes
    require a matching X-API-Token header. Reads stay open. Uses a
    constant-time comparison and never echoes the presented value."""
    expected = _token_configured()
    if not expected:
        return
    presented = request.headers.get("x-api-token", "") or ""
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API token.")


# ── Shared Meta-API concurrency semaphore (Phase 8) ──────────────────────
# Bounds parallel Meta requests across vision + text paths in this process.

_llm_semaphore: "asyncio.Semaphore | None" = None
_llm_semaphore_limit: int = 0


def reset_llm_semaphore() -> None:
    """Drop the cached semaphore (tests call this between cases)."""
    global _llm_semaphore, _llm_semaphore_limit
    _llm_semaphore = None
    _llm_semaphore_limit = 0


def llm_semaphore(limit: int) -> "asyncio.Semaphore | None":
    """Return the process-wide semaphore for the limit, or None if unbounded.

    limit <= 0 disables the bound. Recreated when the configured limit changes.
    """
    global _llm_semaphore, _llm_semaphore_limit
    if limit <= 0:
        return None
    if _llm_semaphore is None or _llm_semaphore_limit != limit:
        _llm_semaphore = asyncio.Semaphore(limit)
        _llm_semaphore_limit = limit
    return _llm_semaphore


def llm_concurrency_limit() -> int:
    """Configured parallel-Meta-request bound (default 2)."""
    try:
        from .config import get_settings
        return get_settings().llm_max_concurrent
    except Exception:
        return 2


@asynccontextmanager
async def bounded_llm_call(limit: int | None = None):
    """Bound one Meta API request by the process-wide semaphore.

    Acquired per attempt (never held across retry backoff). limit=None reads
    the configured bound; limit <= 0 disables bounding for the call.
    """
    sem = llm_semaphore(llm_concurrency_limit() if limit is None else limit)
    if sem is None:
        yield
    else:
        async with sem:
            yield


# ── Concurrent-investigation cap (atomic in-process counter) ───────────────

_investigations_in_use: int = 0


def reset_investigation_slots() -> None:
    """Reset the counter (tests call this between cases)."""
    global _investigations_in_use
    _investigations_in_use = 0


def try_acquire_investigation_slot(limit: int) -> bool:
    """Non-blocking acquire. limit <= 0 disables the cap. Atomic: no awaits."""
    global _investigations_in_use
    if limit <= 0:
        return True
    if _investigations_in_use >= limit:
        return False
    _investigations_in_use += 1
    return True


def release_investigation_slot(limit: int) -> None:
    """Release a previously acquired slot."""
    global _investigations_in_use
    if limit <= 0:
        return
    _investigations_in_use = max(0, _investigations_in_use - 1)


def investigations_in_use() -> int:
    """Current concurrent-investigation count (observability)."""
    return _investigations_in_use


# ── Request IDs ────────────────────────────────────────────────────────────

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def normalize_request_id(presented: Optional[str], generate: str) -> str:
    """Honor a caller-supplied request ID when syntactically safe, else use generated."""
    if presented and _REQUEST_ID_RE.match(presented):
        return presented
    return generate


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
