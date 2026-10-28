"""
P0 regression tests: vision-model response parsing and indexing resilience.

Covers the reported failure modes — malformed JSON ("Expecting ',' delimiter"),
null model content ("'NoneType' object has no attribute 'strip'"), PARTIAL jobs
with zero indexed frames, and per-frame warning spam — plus counter accuracy,
status semantics, retry idempotency, and end-to-end processing of a real
10-second video.

All model I/O is mocked except the explicitly skipped-if-absent ffmpeg binary
used to generate the synthetic 10s fixture (no network, no API keys).
"""
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app import processor
from app.database import get_db
from app.meta_client import (
    MUSE_SPARK_MODEL,
    ModelResponseError,
    MockMetaClient,
    MuseSparkClient,
    TRUNCATED_RETRY_MAX_TOKENS,
    extract_message_content,
    parse_model_json,
)
from app.models import ProcessingStatus
from app.provider import is_transient_error
from tests.fixtures import RECORDING_ID_1


VALID_ANALYSIS = {"objects": [], "scene_summary": "empty room"}


def _envelope(content, finish_reason="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


# ── 1–6. Shared response normalization ────────────────────────────────────────

def test_valid_envelope_and_json_parse():
    content, finish = extract_message_content(_envelope(json.dumps(VALID_ANALYSIS)))
    assert finish == "stop"
    assert parse_model_json(content, finish_reason=finish) == VALID_ANALYSIS


def test_malformed_json_missing_comma_has_structured_code():
    content = '{"objects": [], "scene_summary": "a" "b"}'
    with pytest.raises(ModelResponseError) as exc_info:
        parse_model_json(content)
    assert exc_info.value.code == "malformed_json"
    # The raw decoder error must never leak to callers.
    assert not isinstance(exc_info.value, json.JSONDecodeError)
    assert "Expecting ',' delimiter" in str(exc_info.value)


def test_malformed_json_prose_embedded_has_structured_code():
    content = 'Here: {"objects": [{"label": "x"} "oops"]}'
    with pytest.raises(ModelResponseError) as exc_info:
        parse_model_json(content)
    assert exc_info.value.code == "malformed_json"
    assert not isinstance(exc_info.value, json.JSONDecodeError)


def test_markdown_fence_still_supported():
    content = '```json\n{"objects": [], "scene_summary": "fenced"}\n```'
    assert parse_model_json(content)["scene_summary"] == "fenced"


def test_non_json_has_structured_code_and_legacy_message():
    with pytest.raises(ModelResponseError) as exc_info:
        parse_model_json("This is not JSON at all.")
    assert exc_info.value.code == "malformed_json"
    assert "non-JSON" in str(exc_info.value)


def test_non_object_json_rejected():
    with pytest.raises(ModelResponseError) as exc_info:
        parse_model_json("[1, 2, 3]")
    assert exc_info.value.code == "schema_violation"


@pytest.mark.parametrize("content", [None, "", "   "])
def test_null_and_empty_content_never_raise_attribute_error(content):
    data = _envelope(content)
    text, _ = extract_message_content(data)
    with pytest.raises(ModelResponseError) as exc_info:
        parse_model_json(text)
    assert exc_info.value.code == "empty_content"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": None}]},
        {"choices": "nope"},
        {"choices": [{"message": {"content": 42}}]},
    ],
)
def test_unexpected_envelope_shapes_raise_structured_errors(data):
    with pytest.raises(ModelResponseError) as exc_info:
        extract_message_content(data)
    assert exc_info.value.code in (
        "missing_content", "invalid_content_type", "invalid_envelope",
    )


def test_non_dict_envelope_rejected():
    with pytest.raises(ModelResponseError) as exc_info:
        extract_message_content(["not", "a", "dict"])
    assert exc_info.value.code == "invalid_envelope"


def test_provider_error_object_surfaced():
    data = {"error": {"message": "model overloaded, try later"}}
    with pytest.raises(ModelResponseError) as exc_info:
        extract_message_content(data)
    assert exc_info.value.code == "provider_error"
    assert "overloaded" in str(exc_info.value)


def test_content_blocks_joined():
    data = _envelope([{"type": "text", "text": '{"objects": [], '},
                      {"type": "text", "text": '"scene_summary": "blocks"}'},
                      {"type": "image", "x": 1}])
    text, _ = extract_message_content(data)
    assert parse_model_json(text)["scene_summary"] == "blocks"


def test_truncated_output_detected():
    partial = '{"objects": [{"label": "person", "confidence": 0.9'
    with pytest.raises(ModelResponseError) as exc_info:
        parse_model_json(partial, finish_reason="length")
    assert exc_info.value.code == "truncated"
    assert exc_info.value.finish_reason == "length"


def test_structured_failures_are_non_retryable():
    assert not is_transient_error(ModelResponseError("malformed_json", "bad"))


# ── 6b. Truncation retry with a changed request ───────────────────────────────

def _mock_http_client(posts, responses):
    """Patch httpx.AsyncClient; record payloads; replay canned responses."""
    async def fake_post(url, headers, json, **kwargs):
        posts.append(dict(json))
        body = responses[min(len(posts) - 1, len(responses) - 1)]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = body
        return mock_resp

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(side_effect=fake_post)
    return patch("httpx.AsyncClient", return_value=mock_http)


@pytest.mark.asyncio
async def test_truncated_frame_retried_once_with_more_tokens():
    posts = []
    truncated = _envelope('{"objects": [{"label": "x"', finish_reason="length")
    recovered = _envelope(json.dumps(VALID_ANALYSIS), finish_reason="stop")
    client = MuseSparkClient(api_key="k", max_output_tokens=1000)
    with _mock_http_client(posts, [truncated, recovered]):
        result = await client.analyze_frame(b"frame")
    assert result["scene_summary"] == "empty room"
    assert len(posts) == 2
    assert posts[0]["max_completion_tokens"] == 1000
    assert posts[1]["max_completion_tokens"] == 2000


@pytest.mark.asyncio
async def test_persistent_truncation_raises_after_one_retry():
    posts = []
    truncated = _envelope('{"objects": [', finish_reason="length")
    client = MuseSparkClient(api_key="k", max_output_tokens=1000)
    with _mock_http_client(posts, [truncated]):
        with pytest.raises(ModelResponseError) as exc_info:
            await client.analyze_frame(b"frame")
    assert exc_info.value.code == "truncated"
    assert len(posts) == 2


@pytest.mark.asyncio
async def test_no_truncation_retry_once_at_token_cap():
    posts = []
    truncated = _envelope('{"objects": [', finish_reason="length")
    client = MuseSparkClient(api_key="k", max_output_tokens=TRUNCATED_RETRY_MAX_TOKENS)
    with _mock_http_client(posts, [truncated]):
        with pytest.raises(ModelResponseError):
            await client.analyze_frame(b"frame")
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_malformed_frame_response_not_retried():
    posts = []
    bad = _envelope('{"objects": [], "a" "b"}', finish_reason="stop")
    client = MuseSparkClient(api_key="k")
    with _mock_http_client(posts, [bad]):
        with pytest.raises(ModelResponseError) as exc_info:
            await client.analyze_frame(b"frame")
    assert exc_info.value.code == "malformed_json"
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_null_frame_content_raises_structured_error():
    posts = []
    null_content = _envelope(None, finish_reason="stop")
    client = MuseSparkClient(api_key="k")
    with _mock_http_client(posts, [null_content]):
        with pytest.raises(ModelResponseError) as exc_info:
            await client.analyze_frame(b"frame")
    assert exc_info.value.code == "empty_content"


# ── 8–11. process_recording: isolation, counters, states, idempotency ─────────

class ScriptedClient(MockMetaClient):
    """Mock client that fails on chosen call indices with structured errors."""

    def __init__(self, fail_on=(), response=None):
        super().__init__()
        self.fail_on = set(fail_on)
        self.response = response or {
            "objects": [{"label": "test object", "confidence": 0.9}],
            "scene_summary": "test scene",
        }

    async def analyze_frame(self, frame_data: bytes) -> dict:
        idx = self._call_count
        self._call_count += 1
        if idx in self.fail_on:
            raise ModelResponseError(
                "malformed_json", "Model returned malformed JSON (test)",
                excerpt="test",
            )
        return dict(self.response)


async def _seed_recording(db_path, recording_id=None):
    recording_id = recording_id or str(uuid.uuid4())
    async with get_db(db_path) as db:
        await db.execute(
            """INSERT INTO recordings(
                recording_id, original_filename, upload_timestamp,
                video_path, processing_status, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (recording_id, "test.mp4", "2026-01-01T00:00:00+00:00",
             "videos/test.mp4", ProcessingStatus.PENDING.value,
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        await db.commit()
    return recording_id


def _patch_extraction(monkeypatch, n_frames):
    """Replace ffmpeg extraction with N deterministic tiny frames."""
    async def fake_duration(video_path):
        return float(n_frames)

    async def fake_extract(video_path, output_dir, fps=1.0, max_frames=300):
        output_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for i in range(min(n_frames, max_frames)):
            p = output_dir / f"frame_{i:06d}.jpg"
            p.write_bytes(b"\xff\xd8\xff" + f"frame-{i}".encode())
            out.append((float(i) / fps, p))
        return out

    monkeypatch.setattr(processor, "get_video_duration", fake_duration)
    monkeypatch.setattr(processor, "extract_frames", fake_extract)


async def _recording_row(db_path, recording_id):
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT processing_status, frame_count, processing_errors,"
            " indexing_status_detail FROM recordings WHERE recording_id=?",
            (recording_id,))).fetchone()
    return {
        "status": row[0], "frame_count": row[1],
        "errors": json.loads(row[2]) if row[2] else [],
        "detail": row[3],
    }


async def _observation_count(db_path, recording_id):
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM observations WHERE recording_id=?",
            (recording_id,))).fetchone()
    return row[0]


@pytest.mark.asyncio
async def test_one_failed_frame_does_not_block_others(db_path, storage, monkeypatch):
    _patch_extraction(monkeypatch, 3)
    recording_id = await _seed_recording(db_path, RECORDING_ID_1)
    await processor.process_recording(
        recording_id, db_path, storage, ScriptedClient(fail_on={1}))

    row = await _recording_row(db_path, recording_id)
    assert row["status"] == ProcessingStatus.PARTIAL.value
    assert row["frame_count"] == 2
    assert row["detail"] == (
        "Indexed 2/3 frames, 2 API calls, 0 cache hits, 1 failed, 0 skipped")
    assert len(row["errors"]) == 1
    assert "[malformed_json]" in row["errors"][0]
    assert "Frame 1.0s" in row["errors"][0]
    # Only the two successful frames persisted observations.
    assert await _observation_count(db_path, recording_id) == 2


@pytest.mark.asyncio
async def test_all_frames_indexed_status_and_counters(db_path, storage, monkeypatch):
    _patch_extraction(monkeypatch, 3)
    recording_id = await _seed_recording(db_path)
    await processor.process_recording(
        recording_id, db_path, storage, ScriptedClient())

    row = await _recording_row(db_path, recording_id)
    assert row["status"] == ProcessingStatus.INDEXED.value
    assert row["detail"] == (
        "Indexed 3/3 frames, 3 API calls, 0 cache hits, 0 failed, 0 skipped")
    assert row["errors"] == []


@pytest.mark.asyncio
async def test_all_frames_failed_is_failed_not_partial(db_path, storage, monkeypatch):
    _patch_extraction(monkeypatch, 3)
    recording_id = await _seed_recording(db_path)
    await processor.process_recording(
        recording_id, db_path, storage, ScriptedClient(fail_on={0, 1, 2}))

    row = await _recording_row(db_path, recording_id)
    assert row["status"] == ProcessingStatus.FAILED.value
    assert row["frame_count"] == 0
    assert row["detail"] == (
        "Indexed 0/3 frames, 0 API calls, 0 cache hits, 3 failed, 0 skipped")
    assert len(row["errors"]) == 3
    assert await _observation_count(db_path, recording_id) == 0


@pytest.mark.asyncio
async def test_api_limit_skips_counted_separately(db_path, storage, monkeypatch):
    _patch_extraction(monkeypatch, 3)
    recording_id = await _seed_recording(db_path)
    await processor.process_recording(
        recording_id, db_path, storage, ScriptedClient(), max_api_calls=1)

    row = await _recording_row(db_path, recording_id)
    assert row["status"] == ProcessingStatus.PARTIAL.value
    assert row["detail"] == (
        "Indexed 1/3 frames, 1 API calls, 0 cache hits, 0 failed, 2 skipped")


@pytest.mark.asyncio
async def test_reprocessing_is_idempotent(db_path, storage, monkeypatch):
    _patch_extraction(monkeypatch, 3)
    recording_id = await _seed_recording(db_path)
    client = ScriptedClient()
    await processor.process_recording(recording_id, db_path, storage, client)
    first = await _observation_count(db_path, recording_id)
    assert first == 3

    # Second run: all cache hits, zero new rows.
    await processor.process_recording(recording_id, db_path, storage, client)
    assert await _observation_count(db_path, recording_id) == first
    row = await _recording_row(db_path, recording_id)
    assert row["status"] == ProcessingStatus.INDEXED.value
    assert row["detail"] == (
        "Indexed 3/3 frames, 0 API calls, 3 cache hits, 0 failed, 0 skipped")


@pytest.mark.asyncio
async def test_transient_retry_does_not_duplicate_observations(
        db_path, storage, monkeypatch):
    """A frame that succeeds after a transient failure is stored exactly once."""
    import httpx
    from app.provider import AccountedFrameAnalyzer

    _patch_extraction(monkeypatch, 1)
    monkeypatch.setattr("app.provider.asyncio.sleep", AsyncMock())
    recording_id = await _seed_recording(db_path)

    attempts = {"n": 0}

    class Flaky(MockMetaClient):
        async def analyze_frame(self, frame_data: bytes) -> dict:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ConnectError("boom")
            return {"objects": [{"label": "x", "confidence": 0.5}],
                    "scene_summary": "recovered"}

    client = AccountedFrameAnalyzer(Flaky(), db_path, max_attempts=3,
                                    base_backoff=0, max_backoff=0)
    await processor.process_recording(recording_id, db_path, storage, client)

    assert attempts["n"] == 2
    assert await _observation_count(db_path, recording_id) == 1
    row = await _recording_row(db_path, recording_id)
    assert row["status"] == ProcessingStatus.INDEXED.value


# ── 12. End-to-end: real 10s video at 1 fps ───────────────────────────────────

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg binary not available")


@needs_ffmpeg
@pytest.mark.asyncio
async def test_ten_second_video_end_to_end(db_path, storage, tmp_path):
    video = tmp_path / "ten_second.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=10:size=320x240:rate=10",
         "-pix_fmt", "yuv420p", str(video)],
        check=True, capture_output=True, timeout=120,
    )
    rel = await storage.save_video(str(uuid.uuid4()), "ten_second.mp4", video.read_bytes())
    recording_id = str(uuid.uuid4())
    async with get_db(db_path) as db:
        await db.execute(
            """INSERT INTO recordings(
                recording_id, original_filename, upload_timestamp,
                video_path, processing_status, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (recording_id, "ten_second.mp4", "2026-01-01T00:00:00+00:00",
             rel, ProcessingStatus.PENDING.value,
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        await db.commit()

    await processor.process_recording(
        recording_id, db_path, storage, ScriptedClient(), fps=1.0)

    row = await _recording_row(db_path, recording_id)
    assert row["status"] == ProcessingStatus.INDEXED.value
    assert row["frame_count"] == 10, row
    assert row["detail"] == (
        "Indexed 10/10 frames, 10 API calls, 0 cache hits, 0 failed, 0 skipped")

    # Per-frame results verified: timestamps 0..9 each persisted exactly once.
    async with get_db(db_path) as db:
        stamps = await (await db.execute(
            "SELECT DISTINCT video_timestamp_seconds FROM observations"
            " WHERE recording_id=? ORDER BY video_timestamp_seconds",
            (recording_id,))).fetchall()
    assert [r[0] for r in stamps] == [float(i) for i in range(10)]


@needs_ffmpeg
@pytest.mark.asyncio
async def test_undecodable_video_is_failed(db_path, storage):
    rel = await storage.save_video(
        str(uuid.uuid4()), "garbage.mp4", b"not a video at all" * 100)
    recording_id = str(uuid.uuid4())
    async with get_db(db_path) as db:
        await db.execute(
            """INSERT INTO recordings(
                recording_id, original_filename, upload_timestamp,
                video_path, processing_status, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (recording_id, "garbage.mp4", "2026-01-01T00:00:00+00:00",
             rel, ProcessingStatus.PENDING.value,
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        await db.commit()

    await processor.process_recording(
        recording_id, db_path, storage, MockMetaClient())

    row = await _recording_row(db_path, recording_id)
    assert row["status"] == ProcessingStatus.FAILED.value
    assert row["frame_count"] == 0
