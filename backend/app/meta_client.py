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

ANALYSIS_PROMPT = """You are analyzing a CCTV video frame for a security investigation. Describe what is OBSERVED — not inferred.

Report:
1. Any people or vehicles present — appearance (clothing color, size, gait), position, and what they are doing
2. Any notable objects — bags, packages, equipment, vehicles — with color, size, and location
3. Scene context — location type (entrance, corridor, parking, etc.), lighting, and any unusual features
4. Activity or motion — entry, exit, movement direction, stationary, interacting with something

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
    ):
        if not api_key:
            raise ValueError("MODEL_API_KEY is required for MuseSparkClient")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.model_version = model

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
            "max_completion_tokens": 2000,
            "temperature": 0.1,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
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

        content = data["choices"][0]["message"]["content"]
        parsed = _parse_json_response(content)

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


def _parse_json_response(content: str) -> dict:
    """Extract JSON from model response, tolerating surrounding prose."""
    content = content.strip()
    # Strip markdown code fences if present
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"Model returned non-JSON response: {content[:300]}")


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
