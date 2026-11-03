"""
Meta Model API client — Muse Wonder 1.0 for frame analysis, SAM 3.1 optional.

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
MUSE_SPARK_MODEL = "muse-wonder-1.0"

_RAW_LOG_PATH = "/tmp/meta_raw_responses.log"


def _log_raw_response(method: str, finish_reason: str, content: str) -> None:
    """Append raw Meta API response text to a dedicated log file for inspection."""
    import datetime
    try:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(_RAW_LOG_PATH, "a") as f:
            f.write(f"\n--- [{ts}] {method} finish={finish_reason} ---\n")
            f.write(content if isinstance(content, str) else repr(content))
            f.write("\n")
    except Exception:
        pass


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

HAZARD_ANALYSIS_PROMPT = """You are a second-pass hazard and object detection analyst reviewing a CCTV frame. The general scene analysis has already noted people, vehicles, and common objects. Your task is to find what it may have missed.

Look specifically for:
1. Animals — snakes, rodents, dogs, cats, birds, insects or any creature on the floor, countertop, or in an unusual location. Describe species (if identifiable), color, size, and exact position.
2. Hazards — fire, smoke, flooding/water pooling, sparks, fallen objects blocking paths, spilled liquids, exposed wires, broken glass, structural damage visible in frame.
3. Unusual floor-level objects — items on the ground that are out of place: dropped items, unknown packages, stains, debris.
4. Concealed or partially hidden objects — items tucked behind furniture, partially occluded, or placed in atypical locations.

Rules:
- Report ONLY what is DIRECTLY VISIBLE in this frame. Do not guess if something is too small, occluded, or ambiguous — say so and lower confidence.
- If nothing is found in a category, omit it from objects (do not list empty categories).
- Return an empty objects list if nothing unusual is visible.

Return ONLY valid JSON — no prose, no markdown fences:
{
  "objects": [
    {
      "label": "concise label (e.g. 'snake on floor', 'smoke near ceiling', 'wet floor hazard')",
      "attributes": {
        "color": "primary color(s)",
        "shape": "body type or form factor",
        "size": "approximate size relative to scene",
        "distinctive_details": "any notable features: pattern, texture, position cues"
      },
      "scene_description": "exact location and context of this hazard/animal/object in the frame",
      "confidence": 0.8
    }
  ],
  "scene_summary": "one sentence: what hazards or animals were found, or 'No hazards or unusual objects detected'"
}"""


@runtime_checkable
class FrameAnalyzer(Protocol):
    """Interface every provider must satisfy."""
    model_version: str

    async def analyze_frame(
        self, frame_data: bytes, *, prompt: Optional[str] = None
    ) -> dict:
        """Return parsed analysis dict with 'objects' and 'scene_summary' keys."""
        ...


CLIP_ANALYSIS_PROMPT = """You are analyzing a short CCTV clip for a security investigation. The frames below are consecutive samples from one continuous clip, each labeled with its video timestamp in seconds. Describe what is OBSERVED across the clip — not inferred.

Report:
1. Temporary track IDs — assign track_1, track_2, ... to each distinct person or vehicle visible across frames (IDs are valid for this clip only; never identify individuals, describe appearance only)
2. Observable actions per track — posture and motion cues actually visible (standing, walking with direction, running, reaching, falling, carrying), each tied to the timestamp(s) where it is visible
3. Action sequence — ordered list of observed changes across the clip (e.g. "at 12.0s track_1 begins moving toward track_2")
4. Confirmed vs uncertain — facts visible in multiple frames vs single-frame or occluded/ambiguous observations

Rules:
- Observed facts only. If something is occluded, low-resolution, or ambiguous, say so and put it under uncertain instead of guessing.
- One clip cannot prove intent, cause, or identity — describe visible states and let the timeline decide.
- Never state or imply a crime verdict (no guilty/innocent, assault/theft/robbery confirmations, or accusations against any person). Use neutral observable-action language only.

Return ONLY valid JSON — no prose, no markdown fences:
{
  "tracks": [
    {"track_id": "track_1", "description": "appearance + positions across frames", "confidence": 0.8}
  ],
  "observable_actions": [
    {"track_id": "track_1", "action": "walking left to right", "timestamps": [12.0, 13.0], "confidence": 0.85}
  ],
  "action_sequence": ["at 12.0s track_1 walks left to right across the entrance"],
  "confirmed": ["track_1 visible in all frames"],
  "uncertain": ["track_2 partially occluded in the first frame"]
}"""


UPLOAD_UNDERSTANDING_PROMPT = """You are analyzing a CCTV video segment for general-purpose security indexing. The frames below are evenly-sampled from one continuous segment, each labeled with its video timestamp in seconds.

Your goal is to produce a complete, query-independent understanding of what happened. A user may later ask ANY question about this video — what objects were present, what people did, what changed, whether anything was removed, whether anyone entered or left, whether any unusual behavior occurred. Your analysis must be broad enough to answer all these questions.

Analyze:
1. ENTITIES — identify every distinct entity (person, vehicle, animal, object) visible across frames. Assign entity IDs (entity_1, entity_2...) and describe each: type, appearance, first/last seen timestamps.
2. ACTIONS — for each entity, describe its observable actions in sequence: entering, exiting, standing, walking, running, sitting, picking up, placing down, carrying, interacting with another entity. Use timestamps.
3. INTERACTIONS — describe any observable interactions between entities: one person approaches another, a person picks up an object, a person places something down, two people make contact.
4. CHANGES — identify what changed between the first and last frames: objects that appeared, objects that disappeared, positions that changed, people who entered or left.
5. UNUSUAL — identify anything that stands out as potentially significant: objects in unusual locations, unexpected presence of animals, sudden movements, interactions that seem physically intense, objects being moved or removed.

Rules:
- Only describe what is DIRECTLY VISIBLE. If something is ambiguous or occluded, include it in uncertain_observations with a note.
- Never accuse anyone of a crime. Use neutral observable-behavior language: "person reaches toward bag" not "person steals bag".
- If the scene appears completely static (no motion, no people, nothing changes), say so explicitly.
- If there is an animal present, describe its species (if visible), location, and behavior.
- An object present in early frames but absent in later frames should be noted under changes.

Return ONLY valid JSON — no prose, no markdown fences:
{
  "scene_context": "one sentence: location type, camera angle, general lighting",
  "entities": [
    {
      "entity_id": "entity_1",
      "type": "person|vehicle|animal|object",
      "description": "appearance (color, size, distinctive features)",
      "first_seen_seconds": 0.0,
      "last_seen_seconds": 10.0,
      "confidence": 0.9
    }
  ],
  "actions": [
    {
      "entity_id": "entity_1",
      "action": "walking toward gate",
      "start_seconds": 2.0,
      "end_seconds": 5.0,
      "confidence": 0.85
    }
  ],
  "interactions": [
    {
      "entities": ["entity_1", "entity_2"],
      "description": "entity_1 picks up bag held by entity_2",
      "start_seconds": 4.0,
      "end_seconds": 6.0,
      "confidence": 0.7
    }
  ],
  "changes": [
    {
      "type": "appeared|disappeared|moved|entered|exited",
      "entity_id": "entity_1",
      "description": "person entered from left side",
      "timestamp_seconds": 2.0
    }
  ],
  "unusual_observations": ["person moves rapidly toward another person at 8.0s"],
  "uncertain_observations": ["possible second person partially visible at right edge, frames 3-5"],
  "static_scene": false
}"""


WHOLE_RECORDING_NARRATIVE_PROMPT = """You are analyzing a CCTV recording for a security investigation. The frames below are evenly-sampled across the ENTIRE recording, each labeled with its video timestamp in seconds. Your task is to produce a complete temporal narrative of what happened — exactly as a skilled analyst would write it after watching the full video.

Write:
1. THE SCENE — one sentence: location type, camera angle, approximate time of day
2. THE SEQUENCE — numbered steps describing what actually happened in order. Each step must be tied to a timestamp range. Describe who is present, what they do, how they move, and what changes. Use neutral observable language (no crime verdicts).
3. KEY OBSERVATIONS — 3-5 bullet points summarizing the most significant observable facts: unusual behaviors, entity appearances/disappearances, notable interactions, anything that stands out
4. OPEN QUESTIONS — what cannot be determined from this footage (obstructions, cuts, unclear motion)

Rules:
- Ground every claim in what is DIRECTLY VISIBLE across the frames. Do not fabricate details.
- Describe motion direction, speed, and posture when visible (running vs walking, approaching vs leaving).
- If an entity appears in one frame but not the next, note both the appearance and the disappearance with timestamps.
- Never accuse anyone of a crime. Use: "person reaches toward another person's neck area" not "person steals chain".

Return ONLY valid JSON — no prose, no markdown fences:
{
  "scene": "location, camera angle, lighting",
  "narrative_steps": [
    {
      "step": 1,
      "time_range": "0:00-0:18",
      "start_seconds": 0.0,
      "end_seconds": 18.0,
      "description": "A woman in a pink saree stands alone near a maroon car in a basement parking area. A man in dark clothing enters from the far right background and walks slowly toward her."
    }
  ],
  "key_observations": [
    "Man in dark clothing spends ~30 seconds loitering before approaching the woman"
  ],
  "open_questions": [
    "Camera cut at 0:28 — unclear if the same person reappears outside"
  ],
  "entities": [
    {
      "entity_id": "entity_1",
      "description": "woman in pink saree",
      "first_seen_seconds": 0.0,
      "last_seen_seconds": 35.0
    }
  ]
}"""

WHOLE_RECORDING_PROMPT_VERSION = "n1"

VIDEO_ANALYSIS_PROMPT = """You are analyzing a security camera recording. Watch the entire video carefully and return a complete analysis. Return ONLY valid JSON — no markdown, no prose outside the JSON:
{
  "scene_context": "one sentence: location type, camera angle, lighting",
  "entities": [
    {
      "entity_id": "entity_1",
      "type": "person|vehicle|animal|object",
      "description": "appearance (color, size, distinctive features)",
      "first_seen_seconds": 0.0,
      "last_seen_seconds": 30.0,
      "confidence": 0.9
    }
  ],
  "actions": [
    {
      "entity_id": "entity_1",
      "action": "walks toward the door",
      "start_seconds": 5.0,
      "end_seconds": 8.0,
      "confidence": 0.9
    }
  ],
  "interactions": [
    {
      "entities": ["entity_1", "entity_2"],
      "description": "entity_1 approaches entity_2 from behind",
      "start_seconds": 10.0,
      "end_seconds": 12.0,
      "confidence": 0.8
    }
  ],
  "changes": [
    {
      "type": "appeared|disappeared|moved|entered|exited",
      "entity_id": "entity_1",
      "description": "person entered from left side",
      "timestamp_seconds": 5.0
    }
  ],
  "unusual_observations": [
    "person loiters for 30 seconds before approaching another person"
  ],
  "uncertain_observations": [
    "face not visible due to angle"
  ],
  "static_scene": false,
  "narrative_steps": [
    {
      "step": 1,
      "time_range": "0:00-0:18",
      "start_seconds": 0.0,
      "end_seconds": 18.0,
      "description": "Detailed temporal description of what happens in this time range"
    }
  ],
  "key_observations": [
    "most significant observable facts summarized as bullet points"
  ],
  "open_questions": [
    "what cannot be determined from this footage"
  ]
}

Rules:
- Ground every claim in what is DIRECTLY VISIBLE. Do not fabricate.
- Describe motion direction, speed, and posture when visible.
- Never accuse anyone of a crime. Use neutral observable language.
- If the scene is static, set static_scene to true and entities/actions may be empty."""

VIDEO_ANALYSIS_PROMPT_VERSION = "v1"


@runtime_checkable
class TemporalClipAnalyzer(Protocol):
    """Interface for multi-frame clip analysis (temporal pipeline)."""
    model_version: str

    async def analyze_clip(
        self, frames: list[bytes], timestamps: list[float]
    ) -> dict:
        """Return parsed clip analysis with tracks/action_sequence/confirmed/uncertain."""
        ...


class MuseSparkClient:
    """
    Muse Wonder 1.0 via Meta Model API chat completions.
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
        provider: str = "meta",
    ):
        if not api_key:
            raise ValueError(
                "MODEL_API_KEY (or the selected provider's key) is required "
                "for MuseSparkClient"
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.model_version = model
        # Observability label; the wire protocol is OpenAI-compatible on both.
        self.provider = provider
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

    async def analyze_frame(
        self, frame_data: bytes, *, prompt: Optional[str] = None
    ) -> dict:
        b64 = base64.b64encode(frame_data).decode("utf-8")
        active_prompt = prompt if prompt is not None else ANALYSIS_PROMPT

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
                        {"type": "text", "text": active_prompt},
                    ],
                }
            ],
            "max_completion_tokens": self.max_output_tokens,
            "temperature": 0.1,
            # P0.1: provider-enforced JSON validity (verified accepted by
            # api.meta.ai on 2026-10-29 — replaces prompt-only JSON pleas).
            "response_format": {"type": "json_object"},
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
                logger.debug("META_RAW_RESPONSE finish=%s content=%s", finish_reason, content)
                _log_raw_response("analyze_frame", finish_reason, content)
                try:
                    parsed = parse_model_json(content, finish_reason=finish_reason)
                except ModelResponseError as exc:
                    if exc.raw_response is None and isinstance(content, str):
                        exc.raw_response = content[:MAX_RAW_FAILURE_CHARS]
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

        # Validate shape before the result reaches agents (shared minimal
        # schema; also applied to cached entries before reuse).
        validate_frame_analysis(parsed)

        # Attach actual token usage so the accounting layer can record it.
        usage = data.get("usage") or {}
        parsed["_usage"] = {
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
        }
        return parsed

    async def analyze_clip(
        self, frames: list[bytes], timestamps: list[float]
    ) -> dict:
        """Analyze consecutive clip frames as one temporal unit.

        Each image part is preceded by its video timestamp label so the
        model can order observations. Timestamps come from the caller
        (container PTS or API bounds), never from fixed literals.
        """
        if not frames:
            raise ValueError("analyze_clip requires at least one frame")
        if len(timestamps) != len(frames):
            raise ValueError("frames and timestamps must have equal length")
        content: list[dict] = []
        for frame_data, ts in zip(frames, timestamps):
            b64 = base64.b64encode(frame_data).decode("utf-8")
            content.append(
                {"type": "text",
                 "text": f"[frame at video timestamp {float(ts):.3f}s]"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                              "detail": "high"},
            })
        content.append({"type": "text", "text": CLIP_ANALYSIS_PROMPT})
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "max_completion_tokens": self.max_output_tokens,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        from .guards import bounded_llm_call
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
                content_text, finish_reason = extract_message_content(data)
                _log_raw_response("analyze_clip", finish_reason, content_text)
                try:
                    parsed = parse_model_json(
                        content_text, finish_reason=finish_reason)
                except ModelResponseError as exc:
                    if exc.raw_response is None and isinstance(content_text, str):
                        exc.raw_response = content_text[:MAX_RAW_FAILURE_CHARS]
                    if (exc.code != "truncated" or attempt > 1
                            or max_tokens >= TRUNCATED_RETRY_MAX_TOKENS):
                        raise
                    max_tokens = min(max_tokens * 2, TRUNCATED_RETRY_MAX_TOKENS)
                    logger.warning(
                        "clip response truncated; retrying once with "
                        "max_completion_tokens=%d", max_tokens)
                    continue
                break
        validate_clip_analysis(parsed)
        usage = data.get("usage") or {}
        parsed["_usage"] = {
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
        }
        parsed["_images"] = len(frames)
        return parsed

    async def analyze_upload_segment(
        self, frames: list[bytes], timestamps: list[float]
    ) -> dict:
        """Analyze a video segment during upload for general-purpose event indexing.

        Uses UPLOAD_UNDERSTANDING_PROMPT which produces:
        - entities (people, animals, objects) with first/last seen times
        - actions per entity with timestamps
        - interactions between entities
        - changes between first and last frame (appeared/disappeared/moved)
        - unusual observations

        Called once per video segment (or once per video for short clips).
        The result is used by event_extractor to create rich, action-level events.
        """
        if not frames:
            raise ValueError("analyze_upload_segment requires at least one frame")
        if len(timestamps) != len(frames):
            raise ValueError("frames and timestamps must have equal length")
        content: list[dict] = []
        for frame_data, ts in zip(frames, timestamps):
            b64 = base64.b64encode(frame_data).decode("utf-8")
            content.append(
                {"type": "text",
                 "text": f"[frame at video timestamp {float(ts):.3f}s]"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                              "detail": "high"},
            })
        content.append({"type": "text", "text": UPLOAD_UNDERSTANDING_PROMPT})
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "max_completion_tokens": self.max_output_tokens,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        from .guards import bounded_llm_call
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
                content_text, finish_reason = extract_message_content(data)
                _log_raw_response("analyze_upload_segment", finish_reason, content_text)
                try:
                    parsed = parse_model_json(
                        content_text, finish_reason=finish_reason)
                except ModelResponseError as exc:
                    if exc.raw_response is None and isinstance(content_text, str):
                        exc.raw_response = content_text[:MAX_RAW_FAILURE_CHARS]
                    if (exc.code != "truncated" or attempt > 1
                            or max_tokens >= TRUNCATED_RETRY_MAX_TOKENS):
                        raise
                    max_tokens = min(max_tokens * 2, TRUNCATED_RETRY_MAX_TOKENS)
                    logger.warning(
                        "upload segment response truncated; retrying once with "
                        "max_completion_tokens=%d", max_tokens)
                    continue
                break
        validate_upload_understanding(parsed)
        usage = data.get("usage") or {}
        parsed["_usage"] = {
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
        }
        parsed["_images"] = len(frames)
        return parsed

    async def analyze_whole_recording(
        self, frames: list[bytes], timestamps: list[float]
    ) -> dict:
        """Analyze a representative sample of frames from the entire recording.

        Produces a temporal narrative with numbered steps, key observations,
        and open questions — a story-level understanding of what happened.
        Called once per recording (not per segment). Max 20 frames recommended.
        """
        if not frames:
            raise ValueError("analyze_whole_recording requires at least one frame")
        if len(timestamps) != len(frames):
            raise ValueError("frames and timestamps must have equal length")
        content: list[dict] = []
        for frame_data, ts in zip(frames, timestamps):
            b64 = base64.b64encode(frame_data).decode("utf-8")
            content.append(
                {"type": "text",
                 "text": f"[frame at video timestamp {float(ts):.3f}s]"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                              "detail": "high"},
            })
        content.append({"type": "text", "text": WHOLE_RECORDING_NARRATIVE_PROMPT})
        # Narrative needs more tokens than per-segment analysis
        narrative_max_tokens = max(self.max_output_tokens, 4000)
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "max_completion_tokens": narrative_max_tokens,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        from .guards import bounded_llm_call
        max_tokens = narrative_max_tokens
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
                content_text, finish_reason = extract_message_content(data)
                _log_raw_response("analyze_whole_recording", finish_reason, content_text)
                try:
                    parsed = parse_model_json(
                        content_text, finish_reason=finish_reason)
                except ModelResponseError as exc:
                    if exc.raw_response is None and isinstance(content_text, str):
                        exc.raw_response = content_text[:MAX_RAW_FAILURE_CHARS]
                    if (exc.code != "truncated" or attempt > 1
                            or max_tokens >= TRUNCATED_RETRY_MAX_TOKENS):
                        raise
                    max_tokens = min(max_tokens * 2, TRUNCATED_RETRY_MAX_TOKENS)
                    logger.warning(
                        "whole-recording narrative truncated; retrying with "
                        "max_completion_tokens=%d", max_tokens)
                    continue
                break
        validate_whole_recording_narrative(parsed)
        usage = data.get("usage") or {}
        parsed["_usage"] = {
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
        }
        parsed["_images"] = len(frames)
        return parsed

    async def upload_video_file(self, video_path: str) -> str:
        """Upload a video file to /v1/files and return the file_id.

        Uses multipart form-data upload. Same Bearer auth as other methods.
        Retries up to max_api_attempts times with exponential backoff.
        """
        from pathlib import Path as _Path
        import asyncio as _asyncio

        video_file = _Path(video_path)
        if not video_file.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        try:
            from .config import get_settings
            _settings = get_settings()
            max_attempts = _settings.max_api_attempts
            base_backoff = _settings.base_backoff_seconds
        except Exception:
            max_attempts, base_backoff = 3, 1.0

        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(max_attempts):
            if attempt > 0:
                await _asyncio.sleep(base_backoff * (2 ** (attempt - 1)))
            try:
                with open(video_path, "rb") as fh:
                    files = {
                        "file": (video_file.name, fh, "video/mp4"),
                        "purpose": (None, "user_data"),
                    }
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(connect=self.connect_timeout_s,
                                              read=300.0, write=300.0,
                                              pool=self.connect_timeout_s)
                    ) as client:
                        resp = await client.post(
                            f"{self.base_url}/files",
                            headers=headers,
                            files=files,
                        )
                        resp.raise_for_status()
                        data = resp.json()
                file_id = data.get("id") or data.get("file_id")
                if not file_id:
                    raise ModelResponseError(
                        "missing_file_id",
                        f"Files API response missing 'id': {str(data)[:200]}"
                    )
                logger.info("Uploaded video %s → file_id=%s", video_file.name, file_id)
                return file_id
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                body = exc.response.text[:500] if exc.response is not None else ""
                logger.warning("upload_video_file attempt %d failed: %s — body: %s",
                               attempt + 1, exc, body)
            except (httpx.RequestError, ModelResponseError) as exc:
                last_exc = exc
                logger.warning("upload_video_file attempt %d failed: %s", attempt + 1, exc)
        raise last_exc

    async def analyze_video_file(self, file_id: str) -> dict:
        """Analyze a previously-uploaded video via the Responses API (/v1/responses).

        Returns a dict matching the VIDEO_ANALYSIS_PROMPT schema with
        entities, actions, changes, narrative_steps, etc. Logs raw response
        to /tmp/meta_raw_responses.log.
        """
        import asyncio as _asyncio

        try:
            from .config import get_settings
            _settings = get_settings()
            max_attempts = _settings.max_api_attempts
            base_backoff = _settings.base_backoff_seconds
            responses_max_tokens = getattr(_settings, "responses_api_max_output_tokens",
                                           self.max_output_tokens)
        except Exception:
            max_attempts, base_backoff = 3, 1.0
            responses_max_tokens = self.max_output_tokens

        payload = {
            "model": self.model,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": VIDEO_ANALYSIS_PROMPT},
                        {"type": "input_file", "file_id": file_id},
                    ],
                }
            ],
            "max_output_tokens": responses_max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_exc: Exception = RuntimeError("no attempts made")
        from .guards import bounded_llm_call
        async with bounded_llm_call():
            for attempt in range(max_attempts):
                if attempt > 0:
                    await _asyncio.sleep(base_backoff * (2 ** (attempt - 1)))
                try:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(connect=self.connect_timeout_s,
                                              read=300.0, write=60.0,
                                              pool=self.connect_timeout_s)
                    ) as client:
                        resp = await client.post(
                            f"{self.base_url}/responses",
                            headers=headers,
                            json=payload,
                        )
                        resp.raise_for_status()
                        data = resp.json()

                    content_text = _extract_responses_api_content(data)
                    finish_reason = _extract_responses_api_finish_reason(data)
                    _log_raw_response("analyze_video_file", finish_reason or "unknown",
                                      content_text or "")

                    if not content_text:
                        raise ModelResponseError(
                            "empty_content",
                            "Responses API returned empty content",
                            finish_reason=finish_reason,
                        )

                    parsed = parse_model_json(content_text, finish_reason=finish_reason)
                    validate_video_analysis(parsed)

                    usage = data.get("usage") or {}
                    parsed["_usage"] = {
                        "tokens_in": usage.get("input_tokens", 0),
                        "tokens_out": usage.get("output_tokens", 0),
                    }
                    return parsed
                except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                    last_exc = exc
                    logger.warning("analyze_video_file attempt %d failed: %s",
                                   attempt + 1, exc)
                except ModelResponseError as exc:
                    if exc.raw_response is None and isinstance(content_text, str):
                        exc.raw_response = content_text[:MAX_RAW_FAILURE_CHARS]
                    raise
        raise last_exc


def _extract_responses_api_content(data: dict) -> Optional[str]:
    """Extract text content from a Responses API response envelope.

    The Responses API returns output as a list; the assistant message is
    typically output[1] (output[0] is a reasoning item) with content[0].text.
    Falls back to scanning all output items for text content.
    """
    output = data.get("output") or []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in (item.get("content") or []):
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        return text
    # Fallback: any text in any content block
    for item in output:
        if not isinstance(item, dict):
            continue
        for part in (item.get("content") or []):
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text
    return None


def _extract_responses_api_finish_reason(data: dict) -> Optional[str]:
    """Extract finish reason from a Responses API response."""
    # Top-level status field
    status = data.get("status")
    if isinstance(status, str):
        return status
    # From output message items
    output = data.get("output") or []
    for item in output:
        if isinstance(item, dict) and item.get("type") == "message":
            return item.get("stop_reason") or item.get("finish_reason")
    return None


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

    async def analyze_frame(
        self, frame_data: bytes, *, prompt: Optional[str] = None
    ) -> dict:
        self._call_count += 1
        h = hashlib.sha256(frame_data).hexdigest()
        if h in self.scenario_map:
            return self.scenario_map[h]
        return {"objects": [], "scene_summary": "No identifiable objects"}

    async def analyze_clip(
        self, frames: list[bytes], timestamps: list[float]
    ) -> dict:
        """Deterministic clip synthesis from per-frame scenario entries."""
        if not frames:
            raise ValueError("analyze_clip requires at least one frame")
        if len(timestamps) != len(frames):
            raise ValueError("frames and timestamps must have equal length")
        self._call_count += 1
        per_frame = []
        for frame_data in frames:
            h = hashlib.sha256(frame_data).hexdigest()
            per_frame.append(self.scenario_map.get(
                h, {"objects": [], "scene_summary": "No identifiable objects"}))
        labels: list[str] = []
        for entry in per_frame:
            for obj in entry.get("objects", []):
                label = obj.get("label", "unknown")
                if label not in labels:
                    labels.append(label)
        tracks = [
            {"track_id": f"track_{i + 1}",
             "description": f"{label} observed across clip frames",
             "confidence": 0.7}
            for i, label in enumerate(labels)
        ]
        action_sequence = []
        for ts, entry in zip(timestamps, per_frame):
            summary = entry.get("scene_summary", "no change observed")
            action_sequence.append(f"at {float(ts):.1f}s observed: {summary}")
        multi = len(frames) > 1
        confirmed = ([f"{len(frames)} frames analyzed in sequence"]
                     if multi else [])
        uncertain = ([] if multi else ["single frame only — motion unverified"])
        return {
            "tracks": tracks,
            "observable_actions": [
                {"track_id": t["track_id"], "action": "visible in clip frames",
                 "timestamps": [float(ts) for ts in timestamps],
                 "confidence": 0.6}
                for t in tracks
            ],
            "action_sequence": action_sequence,
            "confirmed": confirmed,
            "uncertain": uncertain,
        }

    async def analyze_upload_segment(
        self, frames: list[bytes], timestamps: list[float]
    ) -> dict:
        """Deterministic mock for upload segment analysis."""
        if not frames:
            raise ValueError("analyze_upload_segment requires at least one frame")
        if len(timestamps) != len(frames):
            raise ValueError("frames and timestamps must have equal length")
        self._call_count += 1
        per_frame = []
        for frame_data in frames:
            h = hashlib.sha256(frame_data).hexdigest()
            per_frame.append(self.scenario_map.get(
                h, {"objects": [], "scene_summary": "No identifiable objects"}))
        entities = []
        for i, entry in enumerate(per_frame):
            for obj in entry.get("objects", []):
                label = obj.get("label", "unknown")
                eid = f"entity_{len(entities) + 1}"
                entities.append({
                    "entity_id": eid,
                    "type": "object",
                    "description": label,
                    "first_seen_seconds": float(timestamps[i]),
                    "last_seen_seconds": float(timestamps[i]),
                    "confidence": obj.get("confidence", 0.7),
                })
        first_summary = per_frame[0].get("scene_summary", "") if per_frame else ""
        last_summary = per_frame[-1].get("scene_summary", "") if per_frame else ""
        return {
            "scene_context": first_summary or "mock scene",
            "entities": entities,
            "actions": [],
            "interactions": [],
            "changes": [],
            "unusual_observations": [],
            "uncertain_observations": [],
            "static_scene": len(entities) == 0,
        }

    async def detect_hazards_for_segment(
        self, frames: list[bytes], timestamps: list[float]
    ) -> dict:
        """Deterministic mock for Guardian hazard detection.

        Checks each frame's SHA-256 against scenario_map using the sentinel key
        pattern "guardian:{sha256}" to allow tests to inject specific hazard
        scenarios. Falls back to {"hazards": [], "scene_clear": True}.
        """
        if not frames:
            raise ValueError("detect_hazards_for_segment requires at least one frame")
        if len(timestamps) != len(frames):
            raise ValueError("frames and timestamps must have equal length")
        self._call_count += 1
        for frame_data in frames:
            h = hashlib.sha256(frame_data).hexdigest()
            sentinel_key = f"guardian:{h}"
            if sentinel_key in self.scenario_map:
                return self.scenario_map[sentinel_key]
        return {"hazards": [], "scene_clear": True}

    async def analyze_whole_recording(
        self, frames: list[bytes], timestamps: list[float]
    ) -> dict:
        """Deterministic mock for whole-recording narrative.

        Checks first frame's SHA-256 against scenario_map using sentinel key
        "narrative:{sha256}" for test injection. Returns a minimal default
        narrative otherwise.
        """
        if not frames:
            raise ValueError("analyze_whole_recording requires at least one frame")
        if len(timestamps) != len(frames):
            raise ValueError("frames and timestamps must have equal length")
        self._call_count += 1
        h = hashlib.sha256(frames[0]).hexdigest()
        sentinel_key = f"narrative:{h}"
        if sentinel_key in self.scenario_map:
            return self.scenario_map[sentinel_key]
        t_start = float(timestamps[0])
        t_end = float(timestamps[-1])
        return {
            "scene": "mock scene — location unknown",
            "narrative_steps": [
                {
                    "step": 1,
                    "time_range": f"{t_start:.1f}s-{t_end:.1f}s",
                    "start_seconds": t_start,
                    "end_seconds": t_end,
                    "description": "Mock narrative: scene observed across recording.",
                }
            ],
            "key_observations": ["Mock analysis — no real model was called"],
            "open_questions": [],
            "entities": [],
        }


class MockVideoClient(MockMetaClient):
    """MockMetaClient extended with direct-video API stubs (Files + Responses APIs).

    Use this in tests that exercise the direct-video pipeline path. The base
    MockMetaClient intentionally does NOT have these methods so that legacy
    frame-extraction tests continue to use the old pipeline.
    """

    async def upload_video_file(self, video_path: str) -> str:
        """Mock Files API upload — returns a deterministic mock file_id."""
        self._call_count += 1
        h = hashlib.sha256(video_path.encode()).hexdigest()[:16]
        return f"file-mock-{h}"

    async def analyze_video_file(self, file_id: str) -> dict:
        """Mock Responses API analysis — checks scenario_map for sentinel key 'video:{file_id}'.

        Returns a default minimal analysis if no sentinel key is found.
        """
        self._call_count += 1
        sentinel_key = f"video:{file_id}"
        if sentinel_key in self.scenario_map:
            return self.scenario_map[sentinel_key]
        return {
            "scene_context": "mock scene — direct video analysis",
            "entities": [
                {
                    "entity_id": "entity_1",
                    "type": "person",
                    "description": "mock entity",
                    "first_seen_seconds": 0.0,
                    "last_seen_seconds": 5.0,
                    "confidence": 0.8,
                }
            ],
            "actions": [
                {
                    "entity_id": "entity_1",
                    "action": "present in scene",
                    "start_seconds": 0.0,
                    "end_seconds": 5.0,
                    "confidence": 0.7,
                }
            ],
            "interactions": [],
            "changes": [],
            "unusual_observations": [],
            "uncertain_observations": [],
            "static_scene": False,
            "narrative_steps": [
                {
                    "step": 1,
                    "time_range": "0:00-0:05",
                    "start_seconds": 0.0,
                    "end_seconds": 5.0,
                    "description": "Mock narrative: direct video analysis performed.",
                }
            ],
            "key_observations": ["Mock analysis — direct video API (no real model called)"],
            "open_questions": [],
        }


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
        raw_response: Optional[str] = None,
    ):
        super().__init__(message)
        self.code = code
        self.finish_reason = finish_reason
        self.content_type = content_type
        self.excerpt = excerpt
        # P0.1: exact failing model text for forensic persistence (capped by
        # the caller). Never logged wholesale — persisted to frame_failures.
        self.raw_response = raw_response


# Finish reasons that mean the output was cut short (OpenAI-compatible API).
TRUNCATED_FINISH_REASONS = frozenset({"length"})
# Upper bound for the one-shot truncation retry's token budget.
TRUNCATED_RETRY_MAX_TOKENS = 16000
# Same bound for text-agent calls (one vision-sized call at most).
TEXT_TRUNCATED_RETRY_MAX_TOKENS = 8000
# Cap for persisted raw failure text (forensics, not full dumps).
MAX_RAW_FAILURE_CHARS = 8192


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
    """Short credential-free excerpt around a parse error (or the head).

    P0.1: the window is cut from the ORIGINAL string first and collapsed
    after. Collapsing before indexing shifted coordinates and produced
    empty excerpts for late errors in pretty-printed JSON.
    """
    s = str(text)
    if pos is None or pos < 0:
        return " ".join(s.split())[:160]
    start = max(0, pos - width)
    window = s[start:pos + width]
    return " ".join(window.split())[:160]


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
        # Legacy prose tolerance: accept ONLY a complete, valid object.
        matched = None
        second_exc = None
        if fenced is None:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                matched = match.group()
                try:
                    parsed = json.loads(matched)
                except json.JSONDecodeError as e2:
                    second_exc = e2
                else:
                    return _require_json_object(parsed, finish_reason)
        # P0.1: finish_reason=length wins over brace-shape analysis — a cut-off
        # response is truncated even when it contains braces, and only the
        # truncated code unlocks the larger-budget retry.
        if truncated:
            raise ModelResponseError(
                "truncated",
                f"Model response was truncated (finish_reason=length): "
                f"{_excerpt(candidate, first_exc.pos)}",
                finish_reason=finish_reason,
                excerpt=_excerpt(candidate, first_exc.pos),
            ) from first_exc
        if matched is not None and second_exc is not None:
            raise ModelResponseError(
                "malformed_json",
                f"Model returned malformed JSON ({second_exc}): "
                f"{_excerpt(matched, second_exc.pos)}",
                finish_reason=finish_reason,
                excerpt=_excerpt(matched, second_exc.pos),
            ) from second_exc
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


def validate_frame_analysis(parsed) -> dict:
    """Validate a parsed analysis against the minimal frame schema.

    Shared by live responses and cached entries: a dict with an "objects"
    list and an optional string "scene_summary". Per-entry leniency stays in
    the observation parser; this guards the top-level shape only.
    Returns parsed unchanged. Raises ModelResponseError(schema_violation).
    """
    if not isinstance(parsed, dict):
        raise ModelResponseError(
            "schema_violation", "Model response is not a JSON object"
        )
    if not isinstance(parsed.get("objects"), list):
        raise ModelResponseError(
            "schema_violation", "Model response missing required 'objects' list"
        )
    summary = parsed.get("scene_summary")
    if summary is not None and not isinstance(summary, str):
        raise ModelResponseError(
            "schema_violation", "Model response 'scene_summary' must be a string"
        )
    return parsed


def validate_clip_analysis(parsed) -> dict:
    """Validate a parsed clip analysis against the temporal schema.

    Requires tracks, observable_actions, and action_sequence lists plus
    confirmed/uncertain lists. Returns parsed unchanged.
    Raises ModelResponseError(schema_violation).
    """
    if not isinstance(parsed, dict):
        raise ModelResponseError(
            "schema_violation", "Clip response is not a JSON object")
    for key in ("tracks", "observable_actions", "action_sequence",
                "confirmed", "uncertain"):
        if not isinstance(parsed.get(key), list):
            raise ModelResponseError(
                "schema_violation",
                f"Clip response missing required '{key}' list")
    for action in parsed["observable_actions"]:
        if not isinstance(action, dict) or "action" not in action:
            raise ModelResponseError(
                "schema_violation",
                "Clip observable_actions entries must be objects with 'action'")
    return parsed


def _parse_json_response(content: str) -> dict:
    """Backwards-compatible wrapper around parse_model_json (no finish reason)."""
    return parse_model_json(content)


def validate_upload_understanding(parsed) -> dict:
    """Validate a parsed upload understanding response (lenient — all fields optional except top-level dict).

    The UPLOAD_UNDERSTANDING_PROMPT asks for entities/actions/changes/interactions
    but partial responses are valid — a static scene may have no actions.
    Raises ModelResponseError(schema_violation) only for hard failures.
    """
    if not isinstance(parsed, dict):
        raise ModelResponseError(
            "schema_violation", "Upload understanding response is not a JSON object"
        )
    for key in ("entities", "actions", "changes"):
        if key in parsed and not isinstance(parsed[key], list):
            raise ModelResponseError(
                "schema_violation",
                f"Upload understanding '{key}' must be a list if present"
            )
    return parsed


def validate_whole_recording_narrative(parsed) -> dict:
    """Validate a parsed whole-recording narrative response.

    Requires narrative_steps (list) and scene (str). key_observations is
    optional but must be a list if present. Raises ModelResponseError on failure.
    """
    if not isinstance(parsed, dict):
        raise ModelResponseError(
            "schema_violation", "Narrative response is not a JSON object"
        )
    if "scene" not in parsed or not isinstance(parsed.get("scene"), str):
        raise ModelResponseError(
            "schema_violation", "Narrative response missing 'scene' string"
        )
    steps = parsed.get("narrative_steps")
    if steps is None or not isinstance(steps, list):
        raise ModelResponseError(
            "schema_violation", "Narrative response missing 'narrative_steps' list"
        )
    obs = parsed.get("key_observations")
    if obs is not None and not isinstance(obs, list):
        raise ModelResponseError(
            "schema_violation", "Narrative 'key_observations' must be a list if present"
        )
    return parsed


def validate_video_analysis(parsed) -> dict:
    """Validate a parsed direct video analysis response (lenient).

    Requires a top-level dict. entities/actions/changes/narrative_steps must be
    lists if present. Raises ModelResponseError(schema_violation) on hard failures.
    """
    if not isinstance(parsed, dict):
        raise ModelResponseError(
            "schema_violation", "Video analysis response is not a JSON object"
        )
    for key in ("entities", "actions", "changes", "narrative_steps",
                "interactions", "unusual_observations", "uncertain_observations"):
        if key in parsed and not isinstance(parsed[key], list):
            raise ModelResponseError(
                "schema_violation",
                f"Video analysis '{key}' must be a list if present"
            )
    return parsed


GUARDIAN_HAZARD_PROMPT = """You are a hazard detection specialist reviewing a CCTV video segment. The frames below are evenly-sampled from one continuous segment, each labeled with its video timestamp in seconds.

Your ONLY task is to identify LIFE-SAFETY and SECURITY HAZARDS visible in this segment. Focus exclusively on:

1. FIRE / SMOKE (fire_smoke) — visible flames, smoke, smoldering, or heat distortion indicating fire. Even small or beginning fires must be reported.
2. PERSON FALL / INJURY (person_fall_injury) — a person visibly falling, collapsed on the ground, or in a posture indicating injury (not sitting normally). Exclude people who lie down intentionally.
3. VEHICLE COLLISION (vehicle_collision) — vehicles making contact, a vehicle striking a person or structure, or immediate pre-collision.
4. DANGEROUS ANIMAL (dangerous_animal) — a wild or aggressive animal visibly present in a non-containment area (not a leashed pet in an expected location).
5. WEAPON VISIBLE (weapon_visible) — a firearm, knife, or other weapon visible in a person's hand or conspicuously displayed (not secured in a holster or display case).
6. RESTRICTED AREA ENTRY (restricted_area_entry) — a person crossing a clearly marked restricted boundary (fence with warning signs, secured door forced open, barrier bypassed).
7. OTHER HAZARD (other_hazard) — any other directly visible life-safety hazard not in the above categories (e.g., flooding near electrical equipment, structural collapse, exposed high-voltage wiring).

Rules:
- Report ONLY what is DIRECTLY VISIBLE in these frames. Do not infer; describe observed evidence.
- If a category has no evidence in these frames, omit it from hazards entirely.
- If confidence is below 0.3, omit the hazard entirely.
- SEVERITY: critical = immediate life-threatening, high = serious risk requiring immediate attention, medium = potential hazard needing investigation, low = possible/borderline.
- timestamp_seconds: the earliest frame timestamp in seconds where the hazard is first clearly visible.
- If the scene is normal with no hazards visible, return an empty hazards list.

Return ONLY valid JSON — no prose, no markdown fences:
{
  "hazards": [
    {
      "hazard_category": "fire_smoke|person_fall_injury|vehicle_collision|dangerous_animal|weapon_visible|restricted_area_entry|other_hazard",
      "severity": "critical|high|medium|low",
      "confidence": 0.85,
      "timestamp_seconds": 3.0,
      "description": "Visible flames on the right side of the corridor at 3.0s, smoke accumulating near ceiling",
      "needs_review": false
    }
  ],
  "scene_clear": true
}"""

GUARDIAN_PROMPT_VERSION = "g1"

VALID_HAZARD_CATEGORIES = frozenset({
    "fire_smoke", "person_fall_injury", "vehicle_collision",
    "dangerous_animal", "weapon_visible", "restricted_area_entry", "other_hazard",
})
VALID_SEVERITY_LEVELS = frozenset({"critical", "high", "medium", "low"})


def validate_guardian_hazard_response(parsed) -> dict:
    """Validate a parsed Guardian hazard detection response.

    Raises ModelResponseError(schema_violation) on hard failures.
    Returns parsed unchanged on success.
    """
    if not isinstance(parsed, dict):
        raise ModelResponseError(
            "schema_violation", "Guardian hazard response is not a JSON object"
        )
    hazards = parsed.get("hazards")
    if hazards is not None and not isinstance(hazards, list):
        raise ModelResponseError(
            "schema_violation", "Guardian hazard response 'hazards' must be a list"
        )
    for h in (hazards or []):
        if not isinstance(h, dict):
            raise ModelResponseError(
                "schema_violation", "Each hazard item must be a JSON object"
            )
        if "hazard_category" not in h:
            raise ModelResponseError(
                "schema_violation", "Hazard item missing required 'hazard_category'"
            )
        if "description" not in h:
            raise ModelResponseError(
                "schema_violation", "Hazard item missing required 'description'"
            )
        conf = h.get("confidence")
        if conf is not None and not (isinstance(conf, (int, float)) and 0.0 <= float(conf) <= 1.0):
            raise ModelResponseError(
                "schema_violation",
                f"Hazard confidence must be a float in [0, 1], got {conf!r}"
            )
    return parsed


def get_meta_client(
    mock: bool = False,
    scenario_map: Optional[dict] = None,
    provider: Optional[str] = None,
) -> FrameAnalyzer:
    """
    Factory. Resolves (provider, base_url, key, model) from settings:
      meta (default): MODEL_API_KEY > META_API_KEY, META_API_BASE_URL, VISION_MODEL
      baseten: BASETEN_API_KEY, BASETEN_BASE_URL, BASETEN_MODEL
    Falls back to MockMetaClient when the selected provider has no key set.
    """
    if mock:
        return MockMetaClient(scenario_map=scenario_map)

    from .provider_config import resolve_vision_spec
    try:
        from .config import get_settings
        settings = get_settings()
    except Exception:
        settings = None
    spec = resolve_vision_spec(settings, provider=provider) if settings else None
    if spec is None:
        api_key = (
            os.environ.get("MODEL_API_KEY")
            or os.environ.get("META_API_KEY")
            or ""
        )
        base_url = os.environ.get("META_API_BASE_URL", API_BASE_URL)
        provider_name, model = "meta", MUSE_SPARK_MODEL
    else:
        api_key, base_url = spec.api_key, spec.base_url
        provider_name, model = spec.provider, spec.model

    if not api_key:
        logger.warning(
            "No API key for vision provider %r; "
            "falling back to mock client — no real AI analysis will occur",
            provider_name,
        )
        return MockMetaClient()

    return MuseSparkClient(
        api_key=api_key, base_url=base_url, model=model, provider=provider_name
    )
