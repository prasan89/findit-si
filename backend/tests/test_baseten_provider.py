"""
Baseten provider integration tests (mocked HTTP — no paid credits).

Covers configuration/resolution, provider selection with Meta backward
compatibility, text + vision request shapes, auth/rate-limit/timeout behavior,
invalid structured outputs, evidence provenance, failure handling, agent
routing, trace fields, and cache-key isolation.

Live smoke tests are opt-in (`-m live_api`, needs BASETEN_API_KEY).
"""
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.agents.base import AgentResult, AgentStatus, InvestigationContext
from app.agents.llm_helper import LLMCallResult, llm_json_call_ex
from app.config import Settings, get_settings
from app.investigation_cache import build_fingerprint, model_config_part
from app.meta_client import (
    MUSE_SPARK_MODEL,
    MockMetaClient,
    ModelResponseError,
    MuseSparkClient,
    get_meta_client,
)
from app.provider_config import (
    PROVIDER_BASETEN,
    PROVIDER_META,
    resolve_text_spec,
    resolve_vision_spec,
    validate_provider_config,
)

BASETEN_URL = "https://inference.baseten.co/v1"
BASETEN_MODEL = "meta-models/muse-spark-open-1026"


@pytest.fixture
def fresh_settings(monkeypatch):
    """Allow env-driven Settings changes via get_settings() cache resets."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _envelope(content, finish_reason="stop"):
    return {"choices": [{"message": {"content": content},
                         "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7}}


def _mock_http(posts, bodies):
    async def fake_post(url, headers=None, json=None, **kwargs):
        posts.append({"url": url, "headers": dict(headers or {}), "json": dict(json)})
        body = bodies[min(len(posts) - 1, len(bodies) - 1)]
        if isinstance(body, Exception):
            raise body
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = body
        return mock_resp

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(side_effect=fake_post)
    return patch("httpx.AsyncClient", return_value=mock_http)


def _status_error(status):
    request = httpx.Request("POST", "https://x.test/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status} error", request=request, response=response)


# ── Configuration & resolution ────────────────────────────────────────────────

def test_defaults_keep_meta_provider():
    settings = Settings()
    assert settings.vision_provider == "meta"
    assert settings.investigation_provider == "meta"
    assert settings.baseten_base_url == BASETEN_URL
    assert settings.baseten_model == BASETEN_MODEL
    vision = resolve_vision_spec(settings)
    assert (vision.provider, vision.model) == ("meta", settings.vision_model)
    text = resolve_text_spec(settings)
    assert (text.provider, text.model) == ("meta", settings.investigation_model)


def test_baseten_resolution_uses_baseten_settings():
    settings = Settings(vision_provider="baseten",
                        investigation_provider="baseten",
                        baseten_api_key="k", baseten_model="custom-model")
    vision = resolve_vision_spec(settings)
    assert vision == vision  # dataclass sanity
    assert (vision.provider, vision.base_url, vision.api_key, vision.model) == (
        "baseten", BASETEN_URL, "k", "custom-model")
    text = resolve_text_spec(settings)
    assert text.model == "custom-model"


def test_unknown_provider_rejected():
    with pytest.raises(ValueError, match="Unknown provider"):
        resolve_vision_spec(Settings(vision_provider="nope"))
    with pytest.raises(ValueError, match="Unknown provider"):
        resolve_text_spec(Settings(investigation_provider="nope"))


def test_explicit_model_wins_on_both_providers():
    settings = Settings(investigation_provider="baseten")
    assert resolve_text_spec(settings, model="explicit").model == "explicit"
    assert resolve_text_spec(Settings(), model="explicit").model == "explicit"


def test_validate_config_reports_missing_baseten_key():
    settings = Settings(vision_provider="baseten", baseten_api_key="")
    problems = validate_provider_config(settings)
    assert any("VISION_PROVIDER=baseten" in p for p in problems)
    # Default meta without keys also reports (warn-only, never raises).
    assert isinstance(validate_provider_config(Settings()), list)


# ── Vision client selection & requests ────────────────────────────────────────

def test_factory_defaults_to_meta(monkeypatch, fresh_settings):
    monkeypatch.setenv("MODEL_API_KEY", "meta-key")
    client = get_meta_client()
    assert isinstance(client, MuseSparkClient)
    assert client.provider == "meta"
    assert client.model == MUSE_SPARK_MODEL


def test_factory_selects_baseten_from_env(monkeypatch, fresh_settings):
    monkeypatch.setenv("VISION_PROVIDER", "baseten")
    monkeypatch.setenv("BASETEN_API_KEY", "bt-key")
    client = get_meta_client()
    assert isinstance(client, MuseSparkClient)
    assert client.provider == "baseten"
    assert client.base_url == BASETEN_URL
    assert client.model == BASETEN_MODEL
    assert client.model_version == BASETEN_MODEL  # cache isolation


def test_factory_baseten_without_key_falls_back_to_mock(monkeypatch, fresh_settings):
    monkeypatch.setenv("VISION_PROVIDER", "baseten")
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    assert isinstance(get_meta_client(), MockMetaClient)


@pytest.mark.asyncio
async def test_baseten_vision_request_shape(monkeypatch, fresh_settings):
    """Image frames use the documented OpenAI-compatible image_url part."""
    import base64
    monkeypatch.setenv("VISION_PROVIDER", "baseten")
    monkeypatch.setenv("BASETEN_API_KEY", "bt-key")
    posts = []
    body = _envelope(json.dumps({"objects": [], "scene_summary": "ok"}))
    client = get_meta_client()
    with _mock_http(posts, [body]):
        result = await client.analyze_frame(b"frame-bytes")
    assert result["scene_summary"] == "ok"
    assert posts[0]["url"] == f"{BASETEN_URL}/chat/completions"
    assert posts[0]["json"]["model"] == BASETEN_MODEL
    assert posts[0]["json"]["response_format"] == {"type": "json_object"}
    parts = posts[0]["json"]["messages"][0]["content"]
    image = next(p for p in parts if p.get("type") == "image_url")
    assert base64.b64encode(b"frame-bytes").decode() in image["image_url"]["url"]
    assert posts[0]["headers"]["Authorization"] == "Bearer bt-key"


@pytest.mark.asyncio
async def test_baseten_key_never_leaks_into_errors(caplog):
    import logging
    secret = "bt-super-secret"
    client = MuseSparkClient(api_key=secret, base_url=BASETEN_URL,
                             model=BASETEN_MODEL, provider="baseten")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=_status_error(401))
        mock_cls.return_value = mock_http
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(httpx.HTTPStatusError):
                await client.analyze_frame(b"frame")
    for record in caplog.records:
        assert secret not in record.getMessage()


# ── Text path routing & resilience ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_text_call_routes_to_baseten(db_path, monkeypatch, fresh_settings):
    monkeypatch.setenv("INVESTIGATION_PROVIDER", "baseten")
    monkeypatch.setenv("BASETEN_API_KEY", "bt-key")
    posts = []
    with _mock_http(posts, [_envelope('{"answer": 1}')]):
        result = await llm_json_call_ex("Q?", db_path, "op")
    assert result.data == {"answer": 1}
    assert result.provider == "baseten"
    assert result.model == BASETEN_MODEL
    assert posts[0]["url"] == f"{BASETEN_URL}/chat/completions"
    assert posts[0]["headers"]["Authorization"] == "Bearer bt-key"


@pytest.mark.asyncio
async def test_text_call_explicit_provider_overrides_settings(
        db_path, monkeypatch, fresh_settings):
    monkeypatch.setenv("BASETEN_API_KEY", "bt-key")
    posts = []
    with _mock_http(posts, [_envelope('{"answer": 1}')]):
        result = await llm_json_call_ex("Q?", db_path, "op", provider="baseten")
    assert result.provider == "baseten"
    assert posts[0]["url"] == f"{BASETEN_URL}/chat/completions"


@pytest.mark.asyncio
async def test_auth_failure_fails_fast_without_retry(db_path, monkeypatch, fresh_settings):
    monkeypatch.setenv("INVESTIGATION_PROVIDER", "baseten")
    monkeypatch.setenv("BASETEN_API_KEY", "bad-key")
    posts = []
    with _mock_http(posts, [_status_error(401)]):
        result = await llm_json_call_ex("Q?", db_path, "op")
    assert result.success is False
    assert result.data == {}  # no fabricated content
    assert result.attempts == 1
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds(db_path, monkeypatch, fresh_settings):
    monkeypatch.setattr("app.agents.llm_helper.asyncio.sleep", AsyncMock())
    monkeypatch.setenv("INVESTIGATION_PROVIDER", "baseten")
    monkeypatch.setenv("BASETEN_API_KEY", "bt-key")
    posts = []
    with _mock_http(posts, [_status_error(429), _envelope('{"ok": true}')]):
        result = await llm_json_call_ex("Q?", db_path, "op")
    assert result.success is True
    assert result.data == {"ok": True}
    assert len(posts) == 2


@pytest.mark.asyncio
async def test_malformed_baseten_output_fails_fast(db_path, monkeypatch, fresh_settings):
    monkeypatch.setenv("INVESTIGATION_PROVIDER", "baseten")
    monkeypatch.setenv("BASETEN_API_KEY", "bt-key")
    posts = []
    with _mock_http(posts, [_envelope('{"broken": ')]):
        result = await llm_json_call_ex("Q?", db_path, "op")
    assert result.success is False
    assert result.data == {}
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_baseten_usage_recorded_with_provider(db_path, monkeypatch, fresh_settings):
    from app.database import get_db
    monkeypatch.setenv("INVESTIGATION_PROVIDER", "baseten")
    monkeypatch.setenv("BASETEN_API_KEY", "bt-key")
    posts = []
    with _mock_http(posts, [_envelope('{"ok": true}')]):
        await llm_json_call_ex("Q?", db_path, "usage_probe")
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT provider, model, tokens_in, tokens_out, success"
            " FROM model_usage WHERE operation='usage_probe'")).fetchone()
    assert tuple(row) == ("baseten", BASETEN_MODEL, 5, 7, 1)


# ── Agent routing & provenance ────────────────────────────────────────────────

def _ctx(db_path):
    return InvestigationContext(
        query="what happened?", db_path=db_path, recording_ids=[],
        budget_usd=2.0,
        events=[{"event_id": "e1", "action_description": "a person walks",
                 "observed_facts": ["person visible"], "object_descriptions": [],
                 "timestamp_seconds": 1.0, "camera_name": "cam",
                 "evidence_frames": []}],
    )


@pytest.mark.asyncio
async def test_agent_result_carries_baseten_provenance(db_path, monkeypatch, fresh_settings):
    from app.agents import video_intelligence
    monkeypatch.setenv("INVESTIGATION_PROVIDER", "baseten")
    monkeypatch.setenv("BASETEN_API_KEY", "bt-key")

    async def fake_call(**kwargs):
        assert kwargs["provider"] == "baseten"
        assert kwargs["model"] == BASETEN_MODEL
        return LLMCallResult(data={"summaries": []}, tokens_in=5, tokens_out=7,
                             cost_usd=0.0, model=BASETEN_MODEL, provider="baseten",
                             mocked=False, success=True, attempts=1, timeouts=0)

    monkeypatch.setattr(video_intelligence, "llm_json_call_ex", fake_call)
    result = await video_intelligence.run(_ctx(db_path))
    assert result.status == AgentStatus.COMPLETED
    assert result.provider_used == "baseten"
    assert result.model_used == BASETEN_MODEL
    assert result.output.model_used == BASETEN_MODEL
    dumped = result.to_dict()
    assert dumped["provider_used"] == "baseten"
    assert dumped["model_used"] == BASETEN_MODEL


@pytest.mark.asyncio
async def test_agent_failure_reports_baseten_without_fabrication(
        db_path, monkeypatch, fresh_settings):
    from app.agents import evidence_auditor
    monkeypatch.setenv("INVESTIGATION_PROVIDER", "baseten")
    monkeypatch.setenv("BASETEN_API_KEY", "bt-key")
    ctx = _ctx(db_path)
    ctx.events[0]["inferred_claims"] = ["a person enters the room"]
    posts = []
    with _mock_http(posts, [_status_error(500), _status_error(500),
                             _status_error(500)]):
        monkeypatch.setattr("app.agents.llm_helper.asyncio.sleep", AsyncMock())
        result = await evidence_auditor.run(ctx)
    # Provider failure after retries: deterministic fallback output, truthful
    # provenance, and no measured usage — never a fabricated audit.
    assert result.provider_used == "baseten"
    assert result.output is not None
    assert result.usage_measured is False
    assert (result.tokens_in, result.tokens_out) == (0, 0)
    assert len(posts) == 3  # bounded retries, then stop


def test_agent_result_trace_field_defaults():
    result = AgentResult(agent_name="X", status=AgentStatus.SKIPPED,
                         output=None, duration_ms=0.0)
    assert result.provider_used is None
    assert result.to_dict()["provider_used"] is None


def test_cache_key_isolates_providers():
    a = model_config_part("m1", "m2", 8, "meta", "meta")
    b = model_config_part("m1", "m2", 8, "baseten", "meta")
    c = model_config_part("m1", "m2", 8, "meta", "baseten")
    assert len({a, b, c}) == 3
    fa = build_fingerprint("multi_agent", "q", "rec", None, None, 1.0, a)
    fb = build_fingerprint("multi_agent", "q", "rec", None, None, 1.0, b)
    assert fa != fb


# ── Live smoke (opt-in; skipped without a key) ────────────────────────────────

def _baseten_key_available() -> bool:
    if os.environ.get("BASETEN_API_KEY"):
        return True
    try:
        return bool(Settings().baseten_api_key)
    except Exception:
        return False


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_live_baseten_text_smoke(db_path):
    """One small text completion against the configured Baseten endpoint."""
    if not _baseten_key_available():
        pytest.skip("BASETEN_API_KEY not configured")
    result = await llm_json_call_ex(
        'Reply with exactly {"smoke": "ok"}.', db_path, "baseten_smoke",
        provider="baseten", max_attempts=1)
    assert result.provider == "baseten"
    assert result.success is True, result.data
    assert result.data.get("smoke") == "ok"


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_live_baseten_vision_smoke():
    """One tiny image request proving multimodal payload support."""
    if not _baseten_key_available():
        pytest.skip("BASETEN_API_KEY not configured")
    from PIL import Image
    import io
    img = Image.new("RGB", (64, 64), color=(200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    client = get_meta_client(provider="baseten")
    assert isinstance(client, MuseSparkClient)
    result = await client.analyze_frame(buf.getvalue())
    assert isinstance(result.get("objects"), list)
    assert "scene_summary" in result
