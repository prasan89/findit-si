"""Video streaming endpoint with HTTP Range request support."""
import re
import logging
from pathlib import Path

import aiofiles
import aiofiles.os
import aiosqlite
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from ..config import get_settings
from ..database import get_db
from ..storage import LocalStorageBackend

logger = logging.getLogger(__name__)

router = APIRouter()

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

_MIME_TYPES = {
    '.mp4': 'video/mp4',
    '.mov': 'video/quicktime',
    '.avi': 'video/x-msvideo',
    '.mkv': 'video/x-matroska',
    '.webm': 'video/webm',
}

_CHUNK = 1024 * 256  # 256 KiB per chunk


@router.get('/{recording_id}/video')
async def stream_video(recording_id: str, request: Request):
    """Stream a recording's video file with HTTP Range support."""
    if not _UUID_RE.match(recording_id):
        raise HTTPException(status_code=400, detail="Invalid recording_id")

    settings = get_settings()
    storage = LocalStorageBackend(Path(settings.data_dir))

    async with get_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT video_path FROM recordings WHERE recording_id = ?",
            (recording_id,),
        )).fetchone()

    if row is None or not row["video_path"]:
        raise HTTPException(status_code=404, detail="Recording not found")

    try:
        abs_path = await storage.get_video_path(row["video_path"])
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid video path")

    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")

    mime = _MIME_TYPES.get(abs_path.suffix.lower(), 'application/octet-stream')
    file_size = abs_path.stat().st_size

    range_header = request.headers.get('range')
    if not range_header:
        return FileResponse(
            path=str(abs_path),
            media_type=mime,
            headers={'Accept-Ranges': 'bytes'},
        )

    # Parse Range header
    m = re.match(r'bytes=(\d+)-(\d*)', range_header)
    if not m:
        raise HTTPException(status_code=416, detail="Invalid Range header")

    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else file_size - 1

    if start > end or start >= file_size:
        raise HTTPException(
            status_code=416,
            detail="Range Not Satisfiable",
            headers={'Content-Range': f'bytes */{file_size}'},
        )

    end = min(end, file_size - 1)
    length = end - start + 1

    async def _iter_range():
        async with aiofiles.open(abs_path, 'rb') as f:
            await f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = await f.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        _iter_range(),
        status_code=206,
        media_type=mime,
        headers={
            'Content-Range': f'bytes {start}-{end}/{file_size}',
            'Accept-Ranges': 'bytes',
            'Content-Length': str(length),
        },
    )
