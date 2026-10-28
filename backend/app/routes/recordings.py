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
from ..guards import content_sha256, require_api_token
from ..models import ProcessingStatus, RecordingStatus, RecordingUploadResponse
from ..processor import process_recording
from ..storage import LocalStorageBackend, sniff_video_container, validate_video_extension

logger = logging.getLogger(__name__)

router = APIRouter()


def get_storage():
    settings = get_settings()
    return LocalStorageBackend(Path(settings.data_dir))


def _validate_upload_form(
    recording_datetime: Optional[str],
    recording_timezone: Optional[str],
    camera_name: Optional[str],
    camera_location: Optional[str],
    max_chars: int,
) -> None:
    """Phase 6: bound form fields and validate the recording datetime format."""
    for label, value in (
        ("recording_datetime", recording_datetime),
        ("recording_timezone", recording_timezone),
        ("camera_name", camera_name),
        ("camera_location", camera_location),
    ):
        if value is not None and len(value) > max_chars:
            raise HTTPException(
                status_code=400,
                detail=f"Form field {label!r} exceeds {max_chars} characters.")
    if recording_datetime:
        try:
            datetime.fromisoformat(recording_datetime)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail="recording_datetime must be ISO-8601 (e.g. 2024-01-15T10:00:00).")


@router.post("/upload", response_model=RecordingUploadResponse, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_api_token)])
async def upload_recording(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    recording_datetime: Optional[str] = Form(None),
    recording_timezone: Optional[str] = Form(None),
    camera_name: Optional[str] = Form(None),
    camera_location: Optional[str] = Form(None),
):
    """Upload a video for indexing."""
    settings = get_settings()
    storage = get_storage()

    # Validate extension
    try:
        validate_video_extension(file.filename or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _validate_upload_form(recording_datetime, recording_timezone,
                          camera_name, camera_location,
                          settings.max_form_field_chars)

    # Ensure storage dirs and db exist
    await storage.ensure_dirs()
    await create_tables(settings.db_path)

    # Phase 6: optional upload quota (count-based).
    if settings.upload_max_recordings > 0:
        async with get_db(settings.db_path) as db:
            row = await (await db.execute(
                "SELECT COUNT(*) FROM recordings")).fetchone()
            if row and row[0] >= settings.upload_max_recordings:
                raise HTTPException(
                    status_code=429,
                    detail="Recording quota reached. Delete old recordings first.",
                    headers={"Retry-After": "60"},
                )

    # Phase 6: stream the upload in chunks so an oversized file is rejected
    # without buffering the whole body in memory first.
    chunks: list[bytes] = []
    total = 0
    chunk_size = max(64 * 1024, settings.upload_chunk_bytes)
    while True:
        piece = await file.read(chunk_size)
        if not piece:
            break
        total += len(piece)
        if total > settings.max_video_size_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Max {settings.findit_max_video_size_mb}MB."
            )
        chunks.append(piece)
    data = b"".join(chunks)
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty file uploaded.")

    # Phase 6: magic-bytes check rejects clearly mislabeled content early.
    try:
        sniff_video_container(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    recording_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    file_hash = content_sha256(data)

    # Save video to disk
    video_rel_path = await storage.save_video(recording_id, file.filename or "upload.mp4", data)

    # Insert recording row
    async with get_db(settings.db_path) as db:
        try:
            await db.execute(
                """INSERT INTO recordings(
                    recording_id, original_filename, upload_timestamp,
                    recording_datetime, recording_timezone, video_path,
                    processing_status, frame_count, camera_name, camera_location,
                    created_at, updated_at, file_size_bytes, content_hash
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    recording_id, file.filename, now,
                    recording_datetime, recording_timezone, video_rel_path,
                    ProcessingStatus.PENDING.value, 0,
                    camera_name, camera_location,
                    now, now, len(data), file_hash,
                ),
            )
        except Exception:
            # Older database without the Phase 6 column.
            await db.execute(
                """INSERT INTO recordings(
                    recording_id, original_filename, upload_timestamp,
                    recording_datetime, recording_timezone, video_path,
                    processing_status, frame_count, camera_name, camera_location,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    recording_id, file.filename, now,
                    recording_datetime, recording_timezone, video_rel_path,
                    ProcessingStatus.PENDING.value, 0,
                    camera_name, camera_location,
                    now, now,
                ),
            )
        await db.commit()

    # Start background indexing
    from ..meta_client import get_meta_client
    from ..provider import build_accounted_client
    raw_client = get_meta_client()
    meta_client = build_accounted_client(
        delegate=raw_client,
        db_path=settings.db_path,
        provider=settings.vision_provider,
        budget_limit_usd=settings.budget_limit_usd,
        budget_reserve_usd=settings.budget_reserve_usd,
    )
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
                      camera_name, camera_location,
                      processing_status, frame_count, video_duration_seconds,
                      video_path, thumbnail_path, processing_errors, indexing_status_detail
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
            camera_name=r['camera_name'],
            camera_location=r['camera_location'],
            processing_status=r['processing_status'],
            frame_count=r['frame_count'] or 0,
            video_duration_seconds=r['video_duration_seconds'],
            video_path=r['video_path'],
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
                      camera_name, camera_location,
                      processing_status, frame_count, video_duration_seconds,
                      video_path, thumbnail_path, processing_errors, indexing_status_detail
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
        camera_name=row['camera_name'],
        camera_location=row['camera_location'],
        processing_status=row['processing_status'],
        frame_count=row['frame_count'] or 0,
        video_duration_seconds=row['video_duration_seconds'],
        video_path=row['video_path'],
        thumbnail_path=row['thumbnail_path'],
        processing_errors=errors,
        indexing_status_detail=row['indexing_status_detail'],
    )


@router.delete("/{recording_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_api_token)])
async def delete_recording(recording_id: str):
    """Delete a recording, its frames, thumbnail, and observations.

    Content-hash-keyed model analyses in frame_cache are retained per the
    frame-cache retention policy (see prune_frame_cache).
    """
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
