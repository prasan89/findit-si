"""API integration tests for FindIt recordings and search endpoints."""
import json
import pytest
import pytest_asyncio

from tests.fixtures import (
    RECORDING_ID_1, RECORDING_ID_2,
    FAKE_VIDEO_BYTES,
    insert_recording, insert_observation,
)


@pytest.mark.asyncio
class TestRecordingsAPI:
    async def test_list_recordings_empty(self, client):
        resp = await client.get("/api/recordings/")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_upload_rejects_wrong_extension(self, client):
        from io import BytesIO
        resp = await client.post(
            "/api/recordings/upload",
            files={"file": ("test.txt", BytesIO(b"hello"), "text/plain")},
        )
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"].lower()

    async def test_upload_rejects_empty_file(self, client):
        from io import BytesIO
        resp = await client.post(
            "/api/recordings/upload",
            files={"file": ("test.mp4", BytesIO(b""), "video/mp4")},
        )
        assert resp.status_code == 400

    async def test_upload_accepts_valid_video(self, client):
        from io import BytesIO
        resp = await client.post(
            "/api/recordings/upload",
            files={"file": ("demo.mp4", BytesIO(FAKE_VIDEO_BYTES), "video/mp4")},
        )
        # 202 Accepted
        assert resp.status_code == 202
        data = resp.json()
        assert "recording_id" in data
        assert data["processing_status"] == "PENDING"

    async def test_upload_with_recording_datetime(self, client):
        from io import BytesIO
        resp = await client.post(
            "/api/recordings/upload",
            files={"file": ("demo.mp4", BytesIO(FAKE_VIDEO_BYTES), "video/mp4")},
            data={
                "recording_datetime": "2024-06-01T12:00:00+00:00",
                "recording_timezone": "UTC",
            },
        )
        assert resp.status_code == 202

    async def test_get_recording_not_found(self, client):
        resp = await client.get("/api/recordings/nonexistent-id")
        assert resp.status_code == 404

    async def test_get_recording_after_upload(self, client):
        from io import BytesIO
        upload = await client.post(
            "/api/recordings/upload",
            files={"file": ("clip.mp4", BytesIO(FAKE_VIDEO_BYTES), "video/mp4")},
        )
        recording_id = upload.json()["recording_id"]
        resp = await client.get(f"/api/recordings/{recording_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["recording_id"] == recording_id
        assert data["original_filename"] == "clip.mp4"

    async def test_list_includes_uploaded(self, client):
        from io import BytesIO
        await client.post(
            "/api/recordings/upload",
            files={"file": ("a.mp4", BytesIO(FAKE_VIDEO_BYTES), "video/mp4")},
        )
        await client.post(
            "/api/recordings/upload",
            files={"file": ("b.mp4", BytesIO(FAKE_VIDEO_BYTES), "video/mp4")},
        )
        resp = await client.get("/api/recordings/")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_delete_recording(self, client):
        from io import BytesIO
        upload = await client.post(
            "/api/recordings/upload",
            files={"file": ("to_delete.mp4", BytesIO(FAKE_VIDEO_BYTES), "video/mp4")},
        )
        recording_id = upload.json()["recording_id"]

        del_resp = await client.delete(f"/api/recordings/{recording_id}")
        assert del_resp.status_code == 204

        get_resp = await client.get(f"/api/recordings/{recording_id}")
        assert get_resp.status_code == 404

    async def test_delete_not_found(self, client):
        resp = await client.delete("/api/recordings/no-such-id")
        assert resp.status_code == 404

    async def test_delete_removes_from_list(self, client):
        from io import BytesIO
        upload = await client.post(
            "/api/recordings/upload",
            files={"file": ("gone.mp4", BytesIO(FAKE_VIDEO_BYTES), "video/mp4")},
        )
        recording_id = upload.json()["recording_id"]
        await client.delete(f"/api/recordings/{recording_id}")

        resp = await client.get("/api/recordings/")
        ids = [r["recording_id"] for r in resp.json()]
        assert recording_id not in ids


@pytest.mark.asyncio
class TestSearchAPI:
    async def test_search_empty_db(self, client):
        resp = await client.get("/api/search/", params={"q": "blue notebook"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["best_sighting"] is None
        assert data["searched_recordings"] == 0

    async def test_search_missing_query(self, client):
        resp = await client.get("/api/search/")
        assert resp.status_code == 422  # validation error

    async def test_search_returns_sighting(self, client):
        """Upload a recording, inject observations directly via the API db, then search."""
        from app.config import get_settings
        from app.database import create_tables, get_db

        settings = get_settings()
        await create_tables(settings.db_path)

        # Insert indexed recording and observation directly
        async with get_db(settings.db_path) as db:
            await insert_recording(
                db, RECORDING_ID_1,
                recording_datetime="2024-06-01T10:00:00+00:00",
            )
            await insert_observation(
                db, RECORDING_ID_1,
                object_label="blue notebook",
                object_attributes={"color": "blue"},
                recording_datetime="2024-06-01T10:00:00+00:00",
                source_frame_path="frames/test/frame_001.jpg",
            )

        resp = await client.get("/api/search/", params={"q": "blue notebook"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["best_sighting"] is not None
        assert "notebook" in data["best_sighting"]["object_label"].lower()
        assert data["best_sighting"]["recording_id"] == RECORDING_ID_1

    async def test_search_no_match(self, client):
        from app.config import get_settings
        from app.database import create_tables, get_db

        settings = get_settings()
        await create_tables(settings.db_path)
        async with get_db(settings.db_path) as db:
            await insert_recording(db, RECORDING_ID_2)
            await insert_observation(
                db, RECORDING_ID_2,
                object_label="red car",
                object_attributes={"color": "red"},
            )

        resp = await client.get("/api/search/", params={"q": "bicycle"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["best_sighting"] is None
        assert data["searched_recordings"] == 1


@pytest.mark.asyncio
class TestIndexingFailure:
    async def test_failed_indexing_not_returned_in_search(self, client):
        """A FAILED recording should not appear in search results."""
        from app.config import get_settings
        from app.database import create_tables, get_db

        settings = get_settings()
        await create_tables(settings.db_path)
        async with get_db(settings.db_path) as db:
            await insert_recording(
                db, RECORDING_ID_1,
                processing_status="FAILED",
            )
            await insert_observation(
                db, RECORDING_ID_1,
                object_label="blue notebook",
            )

        resp = await client.get("/api/search/", params={"q": "blue notebook"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["best_sighting"] is None
        assert data["searched_recordings"] == 0
