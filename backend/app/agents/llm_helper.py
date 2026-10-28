"""Shared helper for text-only LLM calls used by investigative agents."""
import json
import logging
import os
import re
import time
from typing import Optional

import httpx

from ..meta_client import API_BASE_URL, MUSE_SPARK_MODEL, _parse_json_response
from ..provider import UsageRecord, record_usage

import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def llm_json_call(
    prompt: str,
    db_path: str,
    operation: str,
    model: Optional[str] = None,
) -> tuple[dict, int, int, float]:
    """
    Make a text-only JSON-mode call to the Meta LLM.
    Returns (parsed_dict, tokens_in, tokens_out, cost_usd).
    Falls back gracefully when no API key is set.
    """
    api_key = (
        os.environ.get("MODEL_API_KEY")
        or os.environ.get("META_API_KEY")
        or ""
    )
    base_url = os.environ.get("META_API_BASE_URL", API_BASE_URL)
    model_name = model or MUSE_SPARK_MODEL

    t0 = time.monotonic()

    if not api_key:
        logger.warning("No API key — LLM agent %s using empty fallback", operation)
        return {}, 0, 0, 0.0

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": 1500,
        "temperature": 0.1,
    }

    latency_ms = 0.0
    tokens_in = 0
    tokens_out = 0
    success = True
    result: dict = {}

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
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

        content = data["choices"][0]["message"]["content"] or ""
        result = _parse_json_response(content)
    except Exception as exc:
        latency_ms = (time.monotonic() - t0) * 1000
        success = False
        logger.warning("LLM call failed for %s: %s", operation, exc)

    from ..provider import _COST_PER_1K_TOKENS_IN, _COST_PER_1K_TOKENS_OUT
    cost = (
        tokens_in * _COST_PER_1K_TOKENS_IN.get(model_name, 0) / 1000
        + tokens_out * _COST_PER_1K_TOKENS_OUT.get(model_name, 0) / 1000
    )

    usage_record = UsageRecord(
        usage_id=str(uuid.uuid4()),
        provider="meta",
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

    return result, tokens_in, tokens_out, cost
