"""Meta Llama Vision API client with a mock for testing."""
import base64
import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "Llama-3.2-90B-Vision-Instruct"
ANALYSIS_PROMPT = """Analyze this image and identify all visible objects. For each object, provide:
1. A specific label (e.g. "blue notebook", "car keys", "water bottle")
2. Attributes: color, shape, size (approximate), and any distinctive details
3. A brief scene description mentioning where the object is located
4. A confidence score from 0.0 to 1.0

Return ONLY valid JSON in this exact format:
{
  "objects": [
    {
      "label": "object name",
      "attributes": {
        "color": "color description",
        "shape": "shape description",
        "size": "size description",
        "distinctive_details": "any notable features"
      },
      "scene_description": "where the object is and surrounding context",
      "confidence": 0.9
    }
  ],
  "scene_summary": "brief overall scene description"
}

Be specific about colors, patterns, and distinguishing features that would help identify the object later."""


class MetaClient:
    def __init__(self, api_key: str, base_url: str = "https://api.llama.com", model: str = DEFAULT_MODEL):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.model_version = model

    async def analyze_frame(self, frame_data: bytes) -> dict:
        """Send a frame to the Meta Llama Vision API and return parsed objects."""
        b64 = base64.b64encode(frame_data).decode('utf-8')

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                        },
                        {
                            "type": "text",
                            "text": ANALYSIS_PROMPT
                        }
                    ]
                }
            ],
            "max_tokens": 2000,
            "temperature": 0.1,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        content = data['choices'][0]['message']['content']
        # Extract JSON from response (model may include surrounding text)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON block
            import re
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise ValueError(f"Model returned non-JSON response: {content[:200]}")


class MockMetaClient:
    """
    Deterministic mock for testing and development.
    Returns responses based on the content_hash or a fixed scenario.
    """
    model_version = "mock-v1"

    def __init__(self, scenario_map: Optional[dict] = None):
        """
        scenario_map: dict mapping content_hash -> response dict.
        If None, returns a generic empty-objects response.
        """
        self.scenario_map = scenario_map or {}
        self._call_count = 0

    async def analyze_frame(self, frame_data: bytes) -> dict:
        self._call_count += 1
        import hashlib
        h = hashlib.sha256(frame_data).hexdigest()
        if h in self.scenario_map:
            return self.scenario_map[h]
        return {"objects": [], "scene_summary": "No identifiable objects"}


def get_meta_client(mock: bool = False, scenario_map: Optional[dict] = None):
    if mock:
        return MockMetaClient(scenario_map=scenario_map)
    api_key = os.environ.get("META_API_KEY", "")
    base_url = os.environ.get("META_API_BASE_URL", "https://api.llama.com")
    if not api_key:
        logger.warning("META_API_KEY not set; using mock client")
        return MockMetaClient()
    return MetaClient(api_key=api_key, base_url=base_url)
