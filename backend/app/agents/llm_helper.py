"""Shared helper for text-only LLM calls used by investigative agents."""
import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from ..meta_client import (
    API_BASE_URL,
    MUSE_SPARK_MODEL,
    ModelResponseError,
    extract_message_content,
    parse_model_json,
)
from ..provider import UsageRecord, record_usage

import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LLMCallResult:
    """Phase 5: structured outcome of one text LLM call.

    mocked=True means NO live model call was made (no API key configured) and
    the caller must label its output accordingly. tokens/cost are provider-
    reported (measured) only when mocked is False and success is True.
    Phase 6 adds attempts/timeouts for retry observability.
    """

    data: dict
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model: str
    provider: str = "meta"
    mocked: bool = False
    success: bool = True
    attempts: int = 1
    timeouts: int = 0


def _llm_runtime_config() -> tuple[float, float, int, int, float, float, bool]:
    """Resolve (connect_s, read_s, max_attempts, max_tokens, base_backoff, max_backoff, jitter)."""
    connect_s, read_s, attempts, tokens = 10.0, 45.0, 3, 1500
    base_backoff, max_backoff, jitter = 1.0, 8.0, True
    try:
        from ..config import get_settings
        settings = get_settings()
        connect_s = settings.meta_connect_timeout_s
        read_s = settings.meta_text_read_timeout_s
        attempts = settings.max_api_attempts
        tokens = settings.text_max_output_tokens
        base_backoff = settings.base_backoff_seconds
        max_backoff = settings.meta_backoff_max_s
        jitter = settings.meta_retry_jitter
    except Exception:
        pass
    return connect_s, read_s, attempts, tokens, base_backoff, max_backoff, jitter


async def llm_json_call_ex(
    prompt: str,
    db_path: str,
    operation: str,
    model: Optional[str] = None,
    max_attempts: Optional[int] = None,
    provider: Optional[str] = None,
) -> LLMCallResult:
    """
    Make a text-only JSON-mode call to the configured LLM provider.
    Returns an LLMCallResult with measured usage and a mocked flag.
    Falls back gracefully when no API key is set.
    Phase 6: transient failures are retried with backoff (honoring
    Retry-After); auth/malformed failures fail fast without retry.
    Provider routing: explicit `provider` wins, else INVESTIGATION_PROVIDER
    (default "meta"). Model defaults follow the provider unless explicit.
    """
    from ..provider import (compute_backoff_seconds, is_transient_error,
                            retry_after_seconds)
    from ..provider_config import resolve_text_spec

    try:
        from ..config import get_settings
        settings = get_settings()
    except Exception:
        settings = None
    if settings is not None:
        spec = resolve_text_spec(settings, provider=provider, model=model)
        api_key, base_url, model_name = spec.api_key, spec.base_url, spec.model
        provider_name = spec.provider
    else:  # pragma: no cover — settings import effectively infallible
        api_key = (
            os.environ.get("MODEL_API_KEY")
            or os.environ.get("META_API_KEY")
            or ""
        )
        base_url = os.environ.get("META_API_BASE_URL", API_BASE_URL)
        model_name = model or MUSE_SPARK_MODEL
        provider_name = provider or "meta"

    t0 = time.monotonic()

    if not api_key:
        logger.warning("No API key — LLM agent %s using empty fallback", operation)
        return LLMCallResult(data={}, tokens_in=0, tokens_out=0, cost_usd=0.0,
                             model=model_name, provider=provider_name,
                             mocked=True, success=False,
                             attempts=0, timeouts=0)

    connect_s, read_s, cfg_attempts, max_tokens, base_backoff, max_backoff, jitter = (
        _llm_runtime_config())
    attempts_allowed = max_attempts if max_attempts is not None else cfg_attempts

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": max_tokens,
        "temperature": 0.1,
        # P0.1: provider-enforced JSON validity, same as the vision path.
        "response_format": {"type": "json_object"},
    }
    timeout = httpx.Timeout(connect=connect_s, read=read_s,
                            write=read_s, pool=connect_s)

    latency_ms = 0.0
    tokens_in = 0
    tokens_out = 0
    success = True
    result: dict = {}
    attempts = 0
    timeouts = 0
    current_tokens = max_tokens
    truncation_retried = False

    for attempt in range(1, attempts_allowed + 1):
        attempts = attempt
        payload["max_completion_tokens"] = current_tokens
        try:
            from ..guards import bounded_llm_call
            async with bounded_llm_call():
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()

            latency_ms = (time.monotonic() - t0) * 1000
            usage = data.get("usage") or {}
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)

            # Shared normalization path: envelope validation, null/type
            # checks, and strict JSON parsing. Malformed content raises
            # ModelResponseError (a ValueError) and fails fast — retrying the
            # identical request cannot help.
            content, finish_reason = extract_message_content(data)
            result = parse_model_json(content, finish_reason=finish_reason)
            success = True
            break
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            success = False
            if isinstance(exc, httpx.TimeoutException):
                timeouts += 1
            # Log the exception class only — never response bodies, prompts,
            # or credentials.
            logger.warning("LLM call failed for %s: %s", operation, type(exc).__name__)
            # Truncated output gets ONE retry with a larger token budget — a
            # changed request strategy (same policy as the vision path). The
            # retry consumes one attempt slot, so total calls stay bounded.
            from ..meta_client import TEXT_TRUNCATED_RETRY_MAX_TOKENS
            if (
                isinstance(exc, ModelResponseError)
                and exc.code == "truncated"
                and not truncation_retried
                and current_tokens < TEXT_TRUNCATED_RETRY_MAX_TOKENS
            ):
                truncation_retried = True
                current_tokens = min(
                    current_tokens * 2, TEXT_TRUNCATED_RETRY_MAX_TOKENS)
                logger.warning(
                    "LLM response truncated; retrying once with "
                    "max_completion_tokens=%d",
                    current_tokens,
                )
                if attempt < attempts_allowed:
                    continue
                break
            if not is_transient_error(exc) or attempt >= attempts_allowed:
                break
            backoff = compute_backoff_seconds(attempt, base_backoff, max_backoff, jitter)
            backoff = retry_after_seconds(exc, backoff, max_seconds=max_backoff)
            await asyncio.sleep(backoff)

    from ..provider import _COST_PER_1K_TOKENS_IN, _COST_PER_1K_TOKENS_OUT
    cost = (
        tokens_in * _COST_PER_1K_TOKENS_IN.get(model_name, 0) / 1000
        + tokens_out * _COST_PER_1K_TOKENS_OUT.get(model_name, 0) / 1000
    )

    usage_record = UsageRecord(
        usage_id=str(uuid.uuid4()),
        provider=provider_name,
        model=model_name,
        operation=operation,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        success=success,
        estimated_cost_usd=cost,
        created_at=_now(),
    )
    try:
        await record_usage(db_path, usage_record)
    except Exception:
        pass

    return LLMCallResult(data=result, tokens_in=tokens_in, tokens_out=tokens_out,
                         cost_usd=cost, model=model_name, provider=provider_name,
                         mocked=False, success=success,
                         attempts=attempts, timeouts=timeouts)


async def llm_json_call(
    prompt: str,
    db_path: str,
    operation: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> tuple[dict, int, int, float]:
    """
    Make a text-only JSON-mode call to the configured LLM provider.
    Returns (parsed_dict, tokens_in, tokens_out, cost_usd).
    Falls back gracefully when no API key is set.
    """
    result = await llm_json_call_ex(prompt, db_path, operation, model,
                                    provider=provider)
    return result.data, result.tokens_in, result.tokens_out, result.cost_usd
