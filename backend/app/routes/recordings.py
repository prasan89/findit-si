"""Recordings router: upload, list, status, delete."""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status

from ..config import get_settings
from ..database import create_tables, get_db
from ..models import ProcessingStatus, RecordingStatus, RecordingUploadResponse
from ..processor import process_recording
from ..storage import LocalStorageBackend, validate_video_extension

logger = logging.getLogger(__name__)

router = APIRouter()


def get_storage():
    settings = get_settings()
    return LocalStorageBackend(Path(settings.data_dir))


@router.post("/upload", response_model=RecordingUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_recording(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    recording_datetime: Optional[str] = Form(None),
    recording_timezone: Optional[str] = Form(None),
):
    """Upload a video for indexing."""
    settings = get_settings()
    storage = get_storage()

    # Validate extension
    try:
        validate_video_extension(file.filename or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Read and check size
    data = await file.read()
    if len(data) > settings.max_video_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max {settings.findit_max_video_size_mb}MB."
        )
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty file uploaded.")

    recording_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Ensure storage dirs and db exist
    await storage.ensure_dirs()
    await create_tables(settings.db_path)

    # Save video to disk
    video_rel_path = await storage.save_video(recording_id, file.filename or "upload.mp4", data)

    # Insert recording row
    async with get_db(settings.db_path) as db:
        await db.execute(
            """INSERT INTO recordings(
                recording_id, original_filename, upload_timestamp,
                recording_datetime, recording_timezone, video_path,
                processing_status, frame_count, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                recording_id, file.filename, now,
                recording_datetime, recording_timezone, video_rel_path,
                ProcessingStatus.PENDING.value, 0, now, now,
            ),
        )
        await db.commit()

    # Start background indexing
    from ..meta_client import get_meta_client
    meta_client = get_meta_client()
    background_tasks.add_task(
        process_recording,
        recording_id=recording_id,
        db_path=settings.db_path,
        storage=storage,
        meta_client=meta_client,
        fps=settings.findit_frame_extraction_fps,
        max_frames=settings.findit_max_frames_per_video,
        max_api_calls=settings.findit_max_api_calls_per_video,
    )

    return RecordingUploadResponse(
        recording_id=recording_id,
        original_filename=file.filename or "upload",
        upload_timestamp=now,
        processing_status=ProcessingStatus.PENDING.value,
        message="Recording accepted. Indexing started in the background.",
    )


@router.get("/", response_model=list[RecordingStatus])
async def list_recordings():
    """List all recordings and their processing status."""
    settings = get_settings()
    await create_tables(settings.db_path)

    async with get_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT recording_id, original_filename, upload_timestamp,
                      recording_datetime, recording_timezone,
                      processing_status, frame_count, video_duration_seconds,
                      thumbnail_path, processing_errors, indexing_status_detail
               FROM recordings ORDER BY upload_timestamp DESC"""
        )).fetchall()

    result = []
    for r in rows:
        errors = json.loads(r['processing_errors']) if r['processing_errors'] else None
        result.append(RecordingStatus(
            recording_id=r['recording_id'],
            original_filename=r['original_filename'],
            upload_timestamp=r['upload_timestamp'],
            recording_datetime=r['recording_datetime'],
            recording_timezone=r['recording_timezone'],
            processing_status=r['processing_status'],
            frame_count=r['frame_count'] or 0,
            video_duration_seconds=r['video_duration_seconds'],
            thumbnail_path=r['thumbnail_path'],
            processing_errors=errors,
            indexing_status_detail=r['indexing_status_detail'],
        ))
    return result


@router.get("/{recording_id}", response_model=RecordingStatus)
async def get_recording(recording_id: str):
    """Get status and metadata for a single recording."""
    settings = get_settings()
    await create_tables(settings.db_path)

    async with get_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT recording_id, original_filename, upload_timestamp,
                      recording_datetime, recording_timezone,
                      processing_status, frame_count, video_duration_seconds,
                      thumbnail_path, processing_errors, indexing_status_detail
               FROM recordings WHERE recording_id = ?""",
            (recording_id,)
        )).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Recording not found.")

    errors = json.loads(row['processing_errors']) if row['processing_errors'] else None
    return RecordingStatus(
        recording_id=row['recording_id'],
        original_filename=row['original_filename'],
        upload_timestamp=row['upload_timestamp'],
        recording_datetime=row['recording_datetime'],
        recording_timezone=row['recording_timezone'],
        processing_status=row['processing_status'],
        frame_count=row['frame_count'] or 0,
        video_duration_seconds=row['video_duration_seconds'],
        thumbnail_path=row['thumbnail_path'],
        processing_errors=errors,
        indexing_status_detail=row['indexing_status_detail'],
    )


@router.delete("/{recording_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recording(recording_id: str):
    """Delete a recording, its frames, thumbnail, and observations."""
    settings = get_settings()
    storage = get_storage()
    await create_tables(settings.db_path)

    async with get_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT video_path, thumbnail_path FROM recordings WHERE recording_id = ?",
            (recording_id,)
        )).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Recording not found.")

    # Delete files
    if row['video_path']:
        await storage.delete_video(row['video_path'])
    if row['thumbnail_path']:
        await storage.delete_thumbnail(row['thumbnail_path'])
    await storage.delete_frames_for_recording(recording_id)

    # Delete DB rows (observations cascade)
    async with get_db(settings.db_path) as db:
        await db.execute("DELETE FROM recordings WHERE recording_id = ?", (recording_id,))
        await db.commit()
