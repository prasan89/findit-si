"""
Provider tests for the Meta Model API client.

Routine tests use MockMetaClient — no real API calls.
Live smoke test: set MODEL_API_KEY and run with -m live_api.
"""
import hashlib
import json
import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.meta_client import (
    MuseSparkClient,
    MockMetaClient,
    get_meta_client,
    _parse_json_response,
    MUSE_SPARK_MODEL,
    API_BASE_URL,
)


# ── JSON parser unit tests ────────────────────────────────────────────────────

def test_parse_clean_json():
    data = json.dumps({"objects": [], "scene_summary": "empty room"})
    result = _parse_json_response(data)
    assert result["scene_summary"] == "empty room"


def test_parse_json_with_markdown_fence():
    data = '```json\n{"objects": [], "scene_summary": "test"}\n```'
    result = _parse_json_response(data)
    assert result["objects"] == []


def test_parse_json_embedded_in_prose():
    data = 'Here is the analysis: {"objects": [{"label": "book"}], "scene_summary": "desk"}'
    result = _parse_json_response(data)
    assert result["objects"][0]["label"] == "book"


def test_parse_raises_on_invalid_json():
    with pytest.raises(ValueError, match="non-JSON"):
        _parse_json_response("This is not JSON at all.")


# ── MockMetaClient ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mock_returns_empty_by_default():
    client = MockMetaClient()
    result = await client.analyze_frame(b"fake frame data")
    assert result == {"objects": [], "scene_summary": "No identifiable objects"}
    assert client._call_count == 1


@pytest.mark.asyncio
async def test_mock_returns_scenario_by_hash():
    frame = b"specific frame bytes"
    h = hashlib.sha256(frame).hexdigest()
    scenario = {h: {"objects": [{"label": "blue notebook", "confidence": 0.9}], "scene_summary": "desk"}}
    client = MockMetaClient(scenario_map=scenario)
    result = await client.analyze_frame(frame)
    assert result["objects"][0]["label"] == "blue notebook"


@pytest.mark.asyncio
async def test_mock_increments_call_count():
    client = MockMetaClient()
    await client.analyze_frame(b"a")
    await client.analyze_frame(b"b")
    assert client._call_count == 2


def test_mock_model_version():
    assert MockMetaClient().model_version == "mock-v1"


# ── get_meta_client factory ───────────────────────────────────────────────────

def test_factory_returns_mock_when_requested(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "real-key")
    client = get_meta_client(mock=True)
    assert isinstance(client, MockMetaClient)


def test_factory_returns_mock_when_no_key(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("META_API_KEY", raising=False)
    client = get_meta_client()
    assert isinstance(client, MockMetaClient)


def test_factory_prefers_model_api_key_over_legacy(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "new-key")
    monkeypatch.setenv("META_API_KEY", "old-key")
    client = get_meta_client()
    assert isinstance(client, MuseSparkClient)
    assert client.api_key == "new-key"


def test_factory_falls_back_to_legacy_key(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setenv("META_API_KEY", "legacy-key")
    client = get_meta_client()
    assert isinstance(client, MuseSparkClient)
    assert client.api_key == "legacy-key"


def test_factory_respects_custom_base_url(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "key")
    monkeypatch.setenv("META_API_BASE_URL", "https://custom.example.com/v1")
    client = get_meta_client()
    assert isinstance(client, MuseSparkClient)
    assert "custom.example.com" in client.base_url


# ── MuseSparkClient unit tests (mocked HTTP) ──────────────────────────────────

@pytest.mark.asyncio
async def test_muse_spark_sends_correct_model():
    """Verify the request payload uses muse-spark-1.3."""
    captured = {}

    async def fake_post(url, headers, json, **kwargs):
        captured["payload"] = json
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"objects":[],"scene_summary":"test"}'}}]
        }
        return mock_resp

    client = MuseSparkClient(api_key="test-key")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_http

        await client.analyze_frame(b"frame bytes")

    assert captured["payload"]["model"] == MUSE_SPARK_MODEL


@pytest.mark.asyncio
async def test_muse_spark_sends_base64_image():
    """Image must be sent as a base64 data URI in image_url content part."""
    import base64
    frame = b"test frame"
    expected_b64 = base64.b64encode(frame).decode()
    captured = {}

    async def fake_post(url, headers, json, **kwargs):
        captured["payload"] = json
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"objects":[],"scene_summary":"x"}'}}]
        }
        return mock_resp

    client = MuseSparkClient(api_key="test-key")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_http

        await client.analyze_frame(frame)

    content_parts = captured["payload"]["messages"][0]["content"]
    image_part = next(p for p in content_parts if p.get("type") == "image_url")
    assert expected_b64 in image_part["image_url"]["url"]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_muse_spark_uses_bearer_auth():
    """Authorization header must use Bearer scheme."""
    captured = {}

    async def fake_post(url, headers, json, **kwargs):
        captured["headers"] = headers
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"objects":[],"scene_summary":"x"}'}}]
        }
        return mock_resp

    client = MuseSparkClient(api_key="my-secret-key")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_http

        await client.analyze_frame(b"frame")

    assert captured["headers"]["Authorization"] == "Bearer my-secret-key"


@pytest.mark.asyncio
async def test_muse_spark_key_not_in_response_log(caplog):
    """API key must never appear in logs."""
    import logging
    secret = "super-secret-key-xyz"
    client = MuseSparkClient(api_key=secret)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP error")
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_http

        with caplog.at_level(logging.DEBUG):
            try:
                await client.analyze_frame(b"frame")
            except Exception:
                pass

    for record in caplog.records:
        assert secret not in record.getMessage()


def test_muse_spark_requires_api_key():
    with pytest.raises(ValueError, match="MODEL_API_KEY"):
        MuseSparkClient(api_key="")


def test_muse_spark_model_version():
    client = MuseSparkClient(api_key="key")
    assert client.model_version == MUSE_SPARK_MODEL


# ── Live API smoke test (opt-in, requires real key) ───────────────────────────

@pytest.mark.live_api
@pytest.mark.asyncio
async def test_live_muse_spark_smoke():
    """
    Opt-in live test. Run with:
        pytest -m live_api tests/test_provider.py
    Requires META_API_KEY or MODEL_API_KEY set in the environment.
    """
    api_key = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY")
    if not api_key:
        pytest.skip("MODEL_API_KEY / META_API_KEY not set")

    # Generate a valid JPEG using Pillow
    from PIL import Image
    import io
    img = Image.new("RGB", (64, 64), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    client = MuseSparkClient(api_key=api_key)
    result = await client.analyze_frame(jpeg_bytes)
    assert "objects" in result, f"Unexpected response shape: {result}"
    assert "scene_summary" in result
