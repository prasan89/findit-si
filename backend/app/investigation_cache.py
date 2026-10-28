"""Phase 8 investigation-result cache.

Caches successful investigation responses by a fingerprint of everything that
determines the result: mode, normalized query, recording set + per-recording
versions, time bounds, budget/model configuration, and a code version. Hits
skip ALL work (retrieval, agents, model calls). Concurrent duplicate requests
coalesce onto one in-flight computation.

Safety rules:
- Only successful, non-transient results are cached (multi-agent results with
  failed/timed_out agents are never cached).
- TTL-bounded; expired entries are treated as misses and lazily deleted.
- Any recording change (updated_at bump) changes the fingerprint, so stale
  results are never served.
- Optional Idempotency-Key requests replay the stored response for the same
  key+parameters, and 422 when a key is reused with different parameters.
"""
import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Optional

from .database import get_db

# Bump when retrieval/scoring logic changes in ways that alter responses.
# v2: "detected"/"detection" became query stopwords (generic-token fix);
# earlier cached answers (e.g. dog query -> bird events) must not be served.
# v3: insect subject class added; never-indexed/failed recordings are now
# reported in evidence gaps.
CACHE_VERSION = "3"

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# fingerprint -> Future[dict]; waiters share one in-flight computation.
_in_flight: dict[str, "asyncio.Future[dict]"] = {}


def reset_investigation_cache_state() -> None:
    """Clear in-flight coalescing (tests call this between cases)."""
    _in_flight.clear()


def normalize_idempotency_key(presented: Optional[str]) -> Optional[str]:
    if presented and _IDEMPOTENCY_RE.match(presented):
        return presented
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def recording_fingerprint(db_path: str,
                                recording_ids: Optional[list[str]]) -> str:
    """Fingerprint the recording set: ids + per-recording versions.

    recording_ids=None means 'all indexed recordings' — the fingerprint then
    covers the count and latest update across indexed rows, so uploads,
    deletes, and reprocessing all invalidate.
    """
    async with get_db(db_path) as db:
        if recording_ids:
            placeholders = ",".join("?" * len(recording_ids))
            rows = await (await db.execute(
                f"SELECT recording_id, updated_at, processing_status FROM recordings "
                f"WHERE recording_id IN ({placeholders})",
                sorted(recording_ids),
            )).fetchall()
            parts = [f"{r[0]}:{r[1]}:{r[2]}" for r in rows]
            return "ids:" + "|".join(parts)
        rows = await (await db.execute(
            "SELECT COUNT(*), MAX(updated_at) FROM recordings "
            "WHERE processing_status IN ('INDEXED', 'PARTIAL')"
        )).fetchone()
        return f"all:{rows[0]}:{rows[1]}"


def build_fingerprint(
    mode: str,
    query: str,
    recording_part: str,
    time_start: Optional[float],
    time_end: Optional[float],
    budget_usd: Optional[float],
    model_config_part: str,
) -> str:
    """Stable cache key from every input that determines the response."""
    canonical = json.dumps({
        "v": CACHE_VERSION,
        "mode": mode,
        "query": (query or "").strip(),
        "recordings": recording_part,
        "t_start": time_start,
        "t_end": time_end,
        "budget": budget_usd,
        "model": model_config_part,
    }, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def model_config_part(investigation_model: str, vision_model: str,
                      max_llm_calls: Optional[int] = None) -> str:
    return f"{investigation_model}|{vision_model}|{max_llm_calls}"


async def lookup(db_path: str, fingerprint: str, ttl_s: int,
                 idempotency_key: Optional[str] = None) -> Optional[dict]:
    """Return the cached response dict on hit, else None.

   TTL <= 0 disables the cache. Idempotency-key hits verify the stored
    fingerprint matches; a key reused with different parameters raises
    KeyError (callers translate to 422).
    """
    if ttl_s <= 0:
        return None
    async with get_db(db_path) as db:
        if idempotency_key:
            row = await (await db.execute(
                "SELECT cache_key, response_json, created_at FROM investigation_cache "
                "WHERE idempotency_key = ?", (idempotency_key,))).fetchone()
            if row is None:
                return None
            if row[0] != fingerprint:
                raise KeyError("idempotency-key-mismatch")
        else:
            row = await (await db.execute(
                "SELECT response_json, created_at FROM investigation_cache "
                "WHERE cache_key = ?", (fingerprint,))).fetchone()
            if row is None:
                return None
            row = (None, row[0], row[1])
        try:
            age = time.time() - datetime.fromisoformat(row[2]).timestamp()
        except Exception:
            age = float("inf")
        if age > ttl_s:
            await db.execute("DELETE FROM investigation_cache WHERE cache_key = ?",
                             (fingerprint,))
            await db.commit()
            return None
        try:
            payload = json.loads(row[1])
        except Exception:
            return None
        await db.execute(
            "UPDATE investigation_cache SET hits = hits + 1 WHERE cache_key = ?",
            (fingerprint,))
        await db.commit()
        return payload


async def store(db_path: str, fingerprint: str, mode: str, response: dict,
                max_entries: int,
                idempotency_key: Optional[str] = None) -> None:
    """Store a successful response. Best-effort: failures never break requests."""
    try:
        async with get_db(db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO investigation_cache(
                    cache_key, mode, response_json, idempotency_key,
                    created_at, hits) VALUES(?,?,?,?,?,?)""",
                (fingerprint, mode, json.dumps(response, default=str),
                 idempotency_key, _now(), 0))
            await db.commit()
            if max_entries > 0:
                row = await (await db.execute(
                    "SELECT COUNT(*) FROM investigation_cache")).fetchone()
                if row and row[0] > max_entries:
                    # Trim oldest 10% (bounded, deterministic).
                    trim = max(1, max_entries // 10)
                    await db.execute(
                        """DELETE FROM investigation_cache WHERE cache_key IN (
                            SELECT cache_key FROM investigation_cache
                            ORDER BY created_at ASC LIMIT ?)""", (trim,))
                    await db.commit()
    except Exception:
        pass


async def cached_or_compute(fingerprint: str, compute_coro_factory) -> tuple[dict, bool]:
    """Return (response, served_from_in_flight).

    Concurrent requests with the same fingerprint share one computation: the
    first caller runs compute_coro_factory(); waiters await its result. The
    factory result is NOT stored here — the caller stores it after validating
    success (failures/exceptions propagate to all waiters and cache nothing).
    """
    existing = _in_flight.get(fingerprint)
    if existing is not None:
        return await asyncio.shield(existing), True
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    _in_flight[fingerprint] = future
    try:
        result = await compute_coro_factory()
    except BaseException as exc:
        if not future.done():
            future.set_exception(exc)
        raise
    else:
        if not future.done():
            future.set_result(result)
        return result, False
    finally:
        _in_flight.pop(fingerprint, None)


async def cache_stats(db_path: str) -> dict:
    """Entry/hit totals for observability (tolerates missing table)."""
    try:
        async with get_db(db_path) as db:
            row = await (await db.execute(
                "SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM investigation_cache"
            )).fetchone()
            return {"entries": row[0] if row else 0,
                    "hits": row[1] if row else 0}
    except Exception:
        return {"entries": 0, "hits": 0}
