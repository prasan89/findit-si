"""Tests for GET /api/recordings/{id}/video streaming endpoint."""
import uuid
import pytest
from pathlib import Path

from app.database import get_db, create_tables


FAKE_VIDEO = b"\x00\x00\x00\x18ftypisom" + b"\xAB" * 2048

VALID_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


async def _insert_recording_with_video(app_db_path: str, data_dir: Path, recording_id: str, ext: str = ".mp4") -> str:
    videos_dir = data_dir / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    (videos_dir / f"{recording_id}{ext}").write_bytes(FAKE_VIDEO)

    rel_path = f"videos/{recording_id}{ext}"
    now = "2026-01-01T00:00:00+00:00"

    await create_tables(app_db_path)
    async with get_db(app_db_path) as db:
        await db.execute(
            """INSERT INTO recordings(
                recording_id, original_filename, upload_timestamp,
                video_path, processing_status, frame_count,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)""",
            (recording_id, f"test{ext}", now, rel_path, "INDEXED", 0, now, now),
        )
        await db.commit()

    return rel_path


@pytest.mark.asyncio
class TestVideoStreaming:
    async def test_full_video_response(self, client, tmp_data_dir):
        """GET without Range -> 200, Accept-Ranges header, correct MIME."""
        app_db = str(tmp_data_dir / "findit.db")
        await _insert_recording_with_video(app_db, tmp_data_dir, VALID_UUID)

        resp = await client.get(f"/api/recordings/{VALID_UUID}/video")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("video/mp4")
        assert resp.headers.get("accept-ranges") == "bytes"
        assert resp.content == FAKE_VIDEO

    async def test_range_request_returns_206(self, client, tmp_data_dir):
        """GET with Range: bytes=0-1023 -> 206, correct slice, Content-Range header."""
        app_db = str(tmp_data_dir / "findit.db")
        await _insert_recording_with_video(app_db, tmp_data_dir, VALID_UUID)

        resp = await client.get(
            f"/api/recordings/{VALID_UUID}/video",
            headers={"Range": "bytes=0-1023"},
        )
        assert resp.status_code == 206
        assert resp.content == FAKE_VIDEO[:1024]
        cr = resp.headers["content-range"]
        assert cr.startswith("bytes 0-1023/")
        assert resp.headers["accept-ranges"] == "bytes"
        assert resp.headers["content-length"] == "1024"

    async def test_range_open_end(self, client, tmp_data_dir):
        """GET with Range: bytes=100- -> 206, bytes from 100 to end."""
        app_db = str(tmp_data_dir / "findit.db")
        await _insert_recording_with_video(app_db, tmp_data_dir, VALID_UUID)

        total = len(FAKE_VIDEO)
        resp = await client.get(
            f"/api/recordings/{VALID_UUID}/video",
            headers={"Range": "bytes=100-"},
        )
        assert resp.status_code == 206
        assert resp.content == FAKE_VIDEO[100:]
        expected_len = total - 100
        assert resp.headers["content-length"] == str(expected_len)
        assert resp.headers["content-range"] == f"bytes 100-{total-1}/{total}"

    async def test_missing_file_returns_404(self, client, tmp_data_dir):
        """Recording in DB but file deleted -> 404."""
        app_db = str(tmp_data_dir / "findit.db")
        await _insert_recording_with_video(app_db, tmp_data_dir, VALID_UUID)
        (tmp_data_dir / "videos" / f"{VALID_UUID}.mp4").unlink()

        resp = await client.get(f"/api/recordings/{VALID_UUID}/video")
        assert resp.status_code == 404

    async def test_recording_not_found_returns_404(self, client, tmp_data_dir):
        """Random UUID not in DB -> 404."""
        app_db = str(tmp_data_dir / "findit.db")
        await create_tables(app_db)
        random_id = str(uuid.uuid4())
        resp = await client.get(f"/api/recordings/{random_id}/video")
        assert resp.status_code == 404

    async def test_invalid_uuid_returns_400(self, client, tmp_data_dir):
        """Non-UUID string -> 400."""
        resp = await client.get("/api/recordings/not-a-uuid/video")
        assert resp.status_code == 400

    async def test_path_traversal_blocked(self, client, tmp_data_dir):
        """recording_id with path traversal chars -> 400 (UUID regex rejects it)."""
        resp = await client.get("/api/recordings/../../../etc/passwd/video")
        assert resp.status_code in (400, 404)
