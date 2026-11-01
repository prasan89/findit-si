"""Tests for thumbnail URL correctness in VideoAnalysisSummary.

Verifies:
- thumbnail_path from DB is forwarded into VideoAnalysisSummary
- The path follows the "thumbnails/<uuid>.jpg" convention
- Each recording gets its own thumbnail (no cross-contamination)
- Missing/null thumbnail handled gracefully
"""
import uuid
import pytest

from app.investigate import investigate, _build_video_summaries
from app.models import MatchClassification
from tests.fixtures import RECORDING_ID_1, RECORDING_ID_2, insert_recording, now_iso


async def _set_thumbnail(db, recording_id: str, path: str):
    """Write a thumbnail_path into the recordings row."""
    await db.execute(
        "UPDATE recordings SET thumbnail_path=? WHERE recording_id=?",
        (path, recording_id),
    )
    await db.commit()


class TestBuildVideoSummariesThumbnails:
    def test_thumbnail_path_forwarded_from_meta(self):
        rid = str(uuid.uuid4())
        thumb = f"thumbnails/{rid}.jpg"
        rec_meta = {rid: {"original_filename": "a.mp4", "processing_status": "INDEXED",
                          "thumbnail_path": thumb, "video_duration_seconds": None,
                          "camera_name": None, "camera_location": None}}
        summaries = _build_video_summaries([], [rid], rec_meta, [])
        assert summaries[0].thumbnail_path == thumb

    def test_null_thumbnail_handled(self):
        rid = str(uuid.uuid4())
        rec_meta = {rid: {"original_filename": "a.mp4", "processing_status": "INDEXED",
                          "thumbnail_path": None, "video_duration_seconds": None,
                          "camera_name": None, "camera_location": None}}
        summaries = _build_video_summaries([], [rid], rec_meta, [])
        assert summaries[0].thumbnail_path is None

    def test_each_recording_has_its_own_thumbnail(self):
        rid1, rid2 = str(uuid.uuid4()), str(uuid.uuid4())
        rec_meta = {
            rid1: {"original_filename": "a.mp4", "processing_status": "INDEXED",
                   "thumbnail_path": f"thumbnails/{rid1}.jpg", "video_duration_seconds": None,
                   "camera_name": None, "camera_location": None},
            rid2: {"original_filename": "b.mp4", "processing_status": "INDEXED",
                   "thumbnail_path": f"thumbnails/{rid2}.jpg", "video_duration_seconds": None,
                   "camera_name": None, "camera_location": None},
        }
        summaries = _build_video_summaries([], [rid1, rid2], rec_meta, [])
        thumb_map = {s.recording_id: s.thumbnail_path for s in summaries}
        assert thumb_map[rid1] == f"thumbnails/{rid1}.jpg"
        assert thumb_map[rid2] == f"thumbnails/{rid2}.jpg"
        assert thumb_map[rid1] != thumb_map[rid2]


@pytest.mark.anyio
async def test_thumbnail_path_returned_in_investigate_result(db_path, test_db):
    """investigate() must return each recording's thumbnail_path in video_summaries."""
    thumb1 = f"thumbnails/{RECORDING_ID_1}.jpg"
    await insert_recording(test_db, RECORDING_ID_1, filename="cam01.mp4")
    await _set_thumbnail(test_db, RECORDING_ID_1, thumb1)

    result = await investigate("chain snatch", db_path)

    summary = next(
        (s for s in result.video_summaries if s.recording_id == RECORDING_ID_1),
        None,
    )
    assert summary is not None
    assert summary.thumbnail_path == thumb1, (
        f"Expected thumbnail_path={thumb1!r}, got {summary.thumbnail_path!r}"
    )


@pytest.mark.anyio
async def test_thumbnails_are_per_recording_not_shared(db_path, test_db):
    """Each recording in video_summaries must reference its own thumbnail."""
    thumb1 = f"thumbnails/{RECORDING_ID_1}.jpg"
    thumb2 = f"thumbnails/{RECORDING_ID_2}.jpg"
    await insert_recording(test_db, RECORDING_ID_1, filename="cam01.mp4")
    await insert_recording(test_db, RECORDING_ID_2, filename="cam02.mp4")
    await _set_thumbnail(test_db, RECORDING_ID_1, thumb1)
    await _set_thumbnail(test_db, RECORDING_ID_2, thumb2)

    result = await investigate("chain snatch", db_path)

    thumbs = {s.recording_id: s.thumbnail_path for s in result.video_summaries}
    assert thumbs[RECORDING_ID_1] == thumb1
    assert thumbs[RECORDING_ID_2] == thumb2
    assert thumbs[RECORDING_ID_1] != thumbs[RECORDING_ID_2]


@pytest.mark.anyio
async def test_missing_thumbnail_does_not_crash_investigate(db_path, test_db):
    """Recordings without a thumbnail_path should appear normally with thumbnail=None."""
    await insert_recording(test_db, RECORDING_ID_1, filename="nothumb.mp4")
    # Leave thumbnail_path as NULL (default)

    result = await investigate("chain snatch", db_path)

    summary = next(
        (s for s in result.video_summaries if s.recording_id == RECORDING_ID_1),
        None,
    )
    assert summary is not None
    assert summary.thumbnail_path is None
