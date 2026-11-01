"""Temporal event-detection routes: evidence, frame, clip, refine."""
import json
import logging
import re
from pathlib import Path
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..adaptive_sampler import get_container_frame_pts, snap_to_pts
from ..clip_service import extract_clip
from ..config import get_settings
from ..database import create_tables, get_db
from ..guards import require_api_token
from ..models import RefineRequest
from ..storage import LocalStorageBackend
from ..temporal_pipeline import get_temporal_events, run_temporal_detection

logger = logging.getLogger(__name__)

router = APIRouter()

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _check_recording_id(recording_id: str) -> None:
    if not _UUID_RE.match(recording_id):
        raise HTTPException(status_code=400, detail="Invalid recording_id")


async def _require_recording(recording_id: str) -> dict:
    settings = get_settings()
    await create_tables(settings.db_path)
    async with get_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT recording_id, video_path, video_duration_seconds,"
            " recording_datetime FROM recordings WHERE recording_id = ?",
            (recording_id,))).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return dict(row)


@router.get("/{recording_id}/temporal-events")
async def list_temporal_events(
    recording_id: str,
    start: Optional[float] = Query(None, ge=0.0),
    end: Optional[float] = Query(None, ge=0.0),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0),
    include_single_frame: bool = Query(True),
):
    """Per-event evidence JSON with span-overlap time filtering."""
    _check_recording_id(recording_id)
    await _require_recording(recording_id)
    if start is not None and end is not None and end < start:
        raise HTTPException(
            status_code=400, detail="end must be >= start")
    settings = get_settings()
    events = await get_temporal_events(
        recording_id, settings.db_path, start=start, end=end,
        min_confidence=min_confidence,
        include_single_frame=include_single_frame)
    sources = {e.get("timestamp_source", "coarse_grid") for e in events}
    return {
        "recording_id": recording_id,
        "events": events,
        "count": len(events),
        "timestamp_source": (
            sources.pop() if len(sources) == 1 else "mixed"
        ) if events else "coarse_grid",
        "filters": {"start": start, "end": end,
                    "min_confidence": min_confidence,
                    "include_single_frame": include_single_frame},
    }


@router.get("/{recording_id}/frame")
async def get_frame_at(recording_id: str, t: float = Query(..., ge=0.0)):
    """Nearest indexed frame to t plus exact container PTS when available."""
    _check_recording_id(recording_id)
    recording = await _require_recording(recording_id)
    settings = get_settings()
    storage = LocalStorageBackend(Path(settings.data_dir))
    async with get_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT source_frame_path, video_timestamp_seconds
               FROM observations WHERE recording_id = ?
               ORDER BY ABS(video_timestamp_seconds - ?) LIMIT 1""",
            (recording_id, t))).fetchone()
    pts: Optional[float] = None
    timestamp_source = "coarse_grid"
    if recording.get("video_path"):
        try:
            video_path = await storage.get_video_path(recording["video_path"])
            if video_path.exists():
                container_pts = await get_container_frame_pts(video_path)
                if container_pts:
                    timestamp_source = "container_pts"
                    pts = snap_to_pts(
                        float(row["video_timestamp_seconds"]) if row else t,
                        container_pts)
        except ValueError:
            pass
    if row is None:
        raise HTTPException(
            status_code=404, detail="No indexed frames for this recording")
    return {
        "recording_id": recording_id,
        "requested_t": t,
        "frame_path": row["source_frame_path"],
        "frame_url": f"/{row['source_frame_path']}",
        "timestamp_seconds": row["video_timestamp_seconds"],
        "pts_seconds": pts,
        "timestamp_source": timestamp_source,
    }


@router.get("/{recording_id}/clip")
async def get_event_clip(
    recording_id: str,
    start: float = Query(..., ge=0.0),
    end: float = Query(..., ge=0.0),
):
    """Event-centered mp4 clip for [start, end] (server-side extraction)."""
    _check_recording_id(recording_id)
    recording = await _require_recording(recording_id)
    if end <= start:
        raise HTTPException(status_code=400, detail="end must be > start")
    settings = get_settings()
    if end - start > settings.temporal_max_clip_seconds:
        raise HTTPException(
            status_code=400,
            detail=f"clip longer than {settings.temporal_max_clip_seconds:.0f}s; "
                   "narrow the bounds")
    duration = recording.get("video_duration_seconds")
    if duration is not None and start >= duration:
        raise HTTPException(
            status_code=400, detail="start is beyond the video duration")
    if not recording.get("video_path"):
        raise HTTPException(status_code=404, detail="Video file not found")
    storage = LocalStorageBackend(Path(settings.data_dir))
    try:
        video_path = await storage.get_video_path(recording["video_path"])
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid video path")
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    clips_dir = Path(settings.data_dir) / "tmp" / "clips" / recording_id
    out = clips_dir / f"clip_{start:.3f}_{end:.3f}.mp4"
    if not out.exists():
        try:
            await extract_clip(video_path, start,
                               min(end, duration or end), out)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e)[:300])
    container_pts = await get_container_frame_pts(video_path)
    provenance = {
        "timestamp_source": "container_pts" if container_pts else "coarse_grid",
        "start_seconds": start, "end_seconds": end,
        "recording_id": recording_id,
    }
    return FileResponse(
        path=str(out), media_type="video/mp4",
        headers={"Accept-Ranges": "bytes",
                 "X-Clip-Provenance": json.dumps(provenance)})


@router.post("/{recording_id}/refine",
             dependencies=[Depends(require_api_token)])
async def refine_window(recording_id: str, body: RefineRequest):
    """Bounded dense re-analysis of one window (coarse-to-fine stage 2)."""
    _check_recording_id(recording_id)
    await _require_recording(recording_id)
    if body.end <= body.start:
        raise HTTPException(status_code=400, detail="end must be > start")
    settings = get_settings()
    if body.end - body.start > settings.temporal_max_refine_seconds_per_query:
        raise HTTPException(
            status_code=400,
            detail="window exceeds the per-query refine cap; narrow the bounds")
    from ..meta_client import get_meta_client
    from ..provider import build_accounted_client
    storage = LocalStorageBackend(Path(settings.data_dir))
    raw_client = get_meta_client()
    meta_client = build_accounted_client(
        delegate=raw_client, db_path=settings.db_path,
        provider=settings.vision_provider,
        budget_limit_usd=settings.budget_limit_usd,
        budget_reserve_usd=settings.budget_reserve_usd,
    ).with_recording_id(recording_id)
    try:
        result = await run_temporal_detection(
            recording_id=recording_id, db_path=settings.db_path,
            storage=storage, meta_client=meta_client,
            start=body.start, end=body.end, reason=body.reason)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"recording_id": recording_id,
            "refine": {"start": body.start, "end": body.end,
                       "reason": body.reason},
            **result}
