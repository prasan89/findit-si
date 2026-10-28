"""
Meta Model API client — Muse Spark 1.3 for frame analysis, SAM 3.1 optional.

Auth: Bearer $MODEL_API_KEY  (backwards-compatible alias: $META_API_KEY)
Base: https://api.meta.ai/v1
"""
import base64
import hashlib
import json
import logging
import os
import re
from typing import Optional, Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.meta.ai/v1"
MUSE_SPARK_MODEL = "muse-spark-1.3"

ANALYSIS_PROMPT = """You are analyzing a CCTV video frame for a security investigation. Describe what is OBSERVED — not inferred. This frame may be one of several analyzed from the same clip; describe only this frame.

Report:
1. Any people or vehicles present — appearance (clothing color, size, gait), position in the frame, and directly visible posture or motion cues (standing, walking with direction, sitting, entering/exiting a doorway if the doorway and crossing are both visible)
2. Any notable objects — bags, packages, equipment, vehicles — with color, size, and location
3. Scene context — location type (entrance, corridor, parking, etc.), lighting, and any unusual features
4. Movement evidence — for each subject state whether it appears stationary or moving and what visible cue shows this (posture, motion blur, position). Do NOT infer identity, intent, or cause.

Rules:
- Observed facts only. If something is occluded, low-resolution, or ambiguous, say so in scene_summary and lower the confidence instead of guessing.
- Never identify individuals; describe appearance only.
- One frame cannot prove entry/exit, appearance, or disappearance — describe the visible state and let the timeline decide.

Return ONLY valid JSON — no prose, no markdown fences:
{
  "objects": [
    {
      "label": "concise label (e.g. 'person in blue jacket', 'dark sedan', 'black backpack')",
      "attributes": {
        "color": "primary color(s)",
        "shape": "body type or form factor",
        "size": "approximate size relative to scene",
        "distinctive_details": "any notable features: markings, logos, gait, posture"
      },
      "scene_description": "what this subject/object is doing and where in the scene",
      "confidence": 0.9
    }
  ],
  "scene_summary": "one sentence: location type, who/what is present, and the primary observable activity"
}"""


@runtime_checkable
class FrameAnalyzer(Protocol):
    """Interface every provider must satisfy."""
    model_version: str

    async def analyze_frame(self, frame_data: bytes) -> dict:
        """Return parsed analysis dict with 'objects' and 'scene_summary' keys."""
        ...


class MuseSparkClient:
    """
    Muse Spark 1.3 via Meta Model API chat completions.
    OpenAI-compatible endpoint: POST /v1/chat/completions
    Images: image_url content part with base64 data URI.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = API_BASE_URL,
        model: str = MUSE_SPARK_MODEL,
        connect_timeout_s: Optional[float] = None,
        read_timeout_s: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
    ):
        if not api_key:
            raise ValueError("MODEL_API_KEY is required for MuseSparkClient")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.model_version = model
        # Phase 6: configurable timeouts/token cap (constructor overrides win;
        # otherwise Settings; otherwise the historical defaults).
        connect_default, read_default, tokens_default = 10.0, 60.0, 2000
        try:
            from .config import get_settings
            _settings = get_settings()
            connect_default = _settings.meta_connect_timeout_s
            read_default = _settings.meta_read_timeout_s
            tokens_default = _settings.vision_max_output_tokens
        except Exception:
            pass
        self.connect_timeout_s = connect_default if connect_timeout_s is None else connect_timeout_s
        self.read_timeout_s = read_default if read_timeout_s is None else read_timeout_s
        self.max_output_tokens = tokens_default if max_output_tokens is None else max_output_tokens

    def _httpx_timeout(self) -> "httpx.Timeout":
        return httpx.Timeout(
            connect=self.connect_timeout_s,
            read=self.read_timeout_s,
            write=self.read_timeout_s,
            pool=self.connect_timeout_s,
        )

    async def analyze_frame(self, frame_data: bytes) -> dict:
        b64 = base64.b64encode(frame_data).decode("utf-8")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": ANALYSIS_PROMPT},
                    ],
                }
            ],
            "max_completion_tokens": self.max_output_tokens,
            "temperature": 0.1,
        }

        from .guards import bounded_llm_call
        # Truncated output (finish_reason=length) gets ONE retry with a larger
        # token budget — a changed request strategy, not a blind repeat. Every
        # other malformed response fails fast via the shared normalization path.
        max_tokens = self.max_output_tokens
        data = None
        parsed: dict = {}
        async with bounded_llm_call():
            for attempt in (1, 2):
                payload["max_completion_tokens"] = max_tokens
                async with httpx.AsyncClient(timeout=self._httpx_timeout()) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                content, finish_reason = extract_message_content(data)
                try:
                    parsed = parse_model_json(content, finish_reason=finish_reason)
                except ModelResponseError as exc:
                    if (
                        exc.code != "truncated"
                        or attempt > 1
                        or max_tokens >= TRUNCATED_RETRY_MAX_TOKENS
                    ):
                        raise
                    max_tokens = min(max_tokens * 2, TRUNCATED_RETRY_MAX_TOKENS)
                    logger.warning(
                        "vision response truncated; retrying once with "
                        "max_completion_tokens=%d",
                        max_tokens,
                    )
                    continue
                break

        # Validate shape before the result reaches agents. A valid
        # analysis is a dict with an "objects" list; anything else fails fast
        # (ModelResponseError is a ValueError — non-retryable).
        if not isinstance(parsed.get("objects"), list):
            raise ModelResponseError(
                "schema_violation", "Model response missing required 'objects' list"
            )

        # Attach actual token usage so the accounting layer can record it.
        usage = data.get("usage") or {}
        parsed["_usage"] = {
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
        }
        return parsed


class MockMetaClient:
    """
    Deterministic mock for tests and development.
    scenario_map: content_hash (hex) -> response dict.
    Falls back to an empty-objects response when hash not in map.
    """

    model_version = "mock-v1"

    def __init__(self, scenario_map: Optional[dict] = None):
        self.scenario_map = scenario_map or {}
        self._call_count = 0

    async def analyze_frame(self, frame_data: bytes) -> dict:
        self._call_count += 1
        h = hashlib.sha256(frame_data).hexdigest()
        if h in self.scenario_map:
            return self.scenario_map[h]
        return {"objects": [], "scene_summary": "No identifiable objects"}


class ModelResponseError(ValueError):
    """Structured, actionable model-response failure.

    Subclasses ValueError so the Phase 6 retry policy keeps treating
    malformed responses as non-retryable (same request, same result).
    `code` is a stable machine-readable category; the message carries a
    truncated, credential-free excerpt for diagnostics.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        finish_reason: Optional[str] = None,
        content_type: Optional[str] = None,
        excerpt: Optional[str] = None,
    ):
        super().__init__(message)
        self.code = code
        self.finish_reason = finish_reason
        self.content_type = content_type
        self.excerpt = excerpt


# Finish reasons that mean the output was cut short (OpenAI-compatible API).
TRUNCATED_FINISH_REASONS = frozenset({"length"})
# Upper bound for the one-shot truncation retry's token budget.
TRUNCATED_RETRY_MAX_TOKENS = 8000


def extract_message_content(data) -> tuple[Optional[str], Optional[str]]:
    """Validate a chat-completions envelope; return (text_or_None, finish_reason).

    Shared by every vision/LLM call site. text is None only when the provider
    returned JSON null content. Raises ModelResponseError on unexpected shapes
    or provider-reported errors. Never raises AttributeError on None.
    """
    if not isinstance(data, dict):
        raise ModelResponseError(
            "invalid_envelope",
            f"Model API returned unexpected response type: {type(data).__name__}",
            content_type=type(data).__name__,
        )
    provider_error = data.get("error")
    if provider_error:
        detail = (
            provider_error.get("message")
            if isinstance(provider_error, dict)
            else provider_error
        )
        raise ModelResponseError(
            "provider_error",
            f"Model API reported an error: {str(detail)[:200]}",
        )
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelResponseError(
            "missing_content", "Model response missing choices[0].message.content"
        )
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if not isinstance(message, dict):
        raise ModelResponseError(
            "missing_content", "Model response missing choices[0].message.content"
        )
    finish_reason = first.get("finish_reason")
    content = message.get("content")
    if content is None:
        return None, finish_reason
    if isinstance(content, str):
        return content, finish_reason
    if isinstance(content, list):
        # Content-block form: join the text parts, ignore the rest.
        parts = [
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and isinstance(p.get("text"), str)
        ]
        text = "".join(parts)
        return (text if text else None), finish_reason
    raise ModelResponseError(
        "invalid_content_type",
        f"Model response content has unexpected type: {type(content).__name__}",
        content_type=type(content).__name__,
        finish_reason=finish_reason,
    )


def _excerpt(text: str, pos: Optional[int] = None, width: int = 80) -> str:
    """Short credential-free excerpt around a parse error (or the head)."""
    flat = " ".join(str(text).split())
    if pos is None:
        return flat[:160]
    start = max(0, pos - width)
    return flat[start:start + 2 * width]


def parse_model_json(content, *, finish_reason: Optional[str] = None) -> dict:
    """Strictly parse model text output as a JSON object.

    Tolerates one markdown-fenced block and prose-embedded JSON (legacy
    provider behavior), but the candidate must parse as COMPLETE valid JSON —
    repairs are never accepted. Raises ModelResponseError (never AttributeError
    or raw json.JSONDecodeError) with a machine-readable code.
    """
    truncated = finish_reason in TRUNCATED_FINISH_REASONS
    if content is None:
        if truncated:
            raise ModelResponseError(
                "truncated",
                "Model response was truncated (finish_reason=length) before "
                "producing content",
                finish_reason=finish_reason,
            )
        raise ModelResponseError(
            "empty_content",
            "Model returned an empty response",
            finish_reason=finish_reason,
        )
    if not isinstance(content, str):
        raise ModelResponseError(
            "invalid_content_type",
            f"Model response content has unexpected type: {type(content).__name__}",
            content_type=type(content).__name__,
            finish_reason=finish_reason,
        )
    text = content.strip()
    if not text:
        if truncated:
            raise ModelResponseError(
                "truncated",
                "Model response was truncated (finish_reason=length)",
                finish_reason=finish_reason,
            )
        raise ModelResponseError(
            "empty_content",
            "Model returned an empty response",
            finish_reason=finish_reason,
        )
    # Tolerate a single markdown-fenced block (legacy behavior).
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as first_exc:
        if fenced is None:
            # Legacy prose tolerance: accept ONLY a complete, valid object.
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                except json.JSONDecodeError as second_exc:
                    raise ModelResponseError(
                        "malformed_json",
                        f"Model returned malformed JSON ({second_exc}): "
                        f"{_excerpt(match.group(), second_exc.pos)}",
                        finish_reason=finish_reason,
                        excerpt=_excerpt(match.group(), second_exc.pos),
                    ) from second_exc
                return _require_json_object(parsed, finish_reason)
        if truncated:
            raise ModelResponseError(
                "truncated",
                f"Model response was truncated (finish_reason=length): "
                f"{_excerpt(candidate, first_exc.pos)}",
                finish_reason=finish_reason,
                excerpt=_excerpt(candidate, first_exc.pos),
            ) from first_exc
        if "{" not in candidate:
            raise ModelResponseError(
                "malformed_json",
                f"Model returned non-JSON response: {_excerpt(candidate)}",
                finish_reason=finish_reason,
                excerpt=_excerpt(candidate),
            ) from first_exc
        raise ModelResponseError(
            "malformed_json",
            f"Model returned malformed JSON ({first_exc}): "
            f"{_excerpt(candidate, first_exc.pos)}",
            finish_reason=finish_reason,
            excerpt=_excerpt(candidate, first_exc.pos),
        ) from first_exc
    return _require_json_object(parsed, finish_reason)


def _require_json_object(parsed, finish_reason: Optional[str]) -> dict:
    if not isinstance(parsed, dict):
        raise ModelResponseError(
            "schema_violation",
            "Model response is not a JSON object",
            finish_reason=finish_reason,
        )
    return parsed


def _parse_json_response(content: str) -> dict:
    """Backwards-compatible wrapper around parse_model_json (no finish reason)."""
    return parse_model_json(content)


def get_meta_client(
    mock: bool = False,
    scenario_map: Optional[dict] = None,
) -> FrameAnalyzer:
    """
    Factory. Resolution order for the API key:
      1. MODEL_API_KEY  (preferred — Meta Model API naming)
      2. META_API_KEY   (backwards-compatible alias)
    Falls back to MockMetaClient when no key is set.
    """
    if mock:
        return MockMetaClient(scenario_map=scenario_map)

    api_key = (
        os.environ.get("MODEL_API_KEY")
        or os.environ.get("META_API_KEY")
        or ""
    )
    base_url = os.environ.get("META_API_BASE_URL", API_BASE_URL)

    if not api_key:
        logger.warning(
            "Neither MODEL_API_KEY nor META_API_KEY is set; "
            "falling back to mock client — no real AI analysis will occur"
        )
        return MockMetaClient()

    return MuseSparkClient(api_key=api_key, base_url=base_url)
