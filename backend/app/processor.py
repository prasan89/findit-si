"""Video processing pipeline: extract frames → analyze with AI → store observations."""
import asyncio
import hashlib
import json
import logging
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiofiles
import aiofiles.os
import aiosqlite

from .database import get_db
from .models import ProcessingStatus
from .storage import LocalStorageBackend

logger = logging.getLogger(__name__)

# Frame extraction
async def extract_frames(
    video_path: Path,
    output_dir: Path,
    fps: float = 1.0,
    max_frames: int = 300,
) -> list[tuple[float, Path]]:
    """
    Extract frames from video using ffmpeg.
    Returns list of (timestamp_seconds, frame_path).
    Raises RuntimeError if ffmpeg fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / 'frame_%06d.jpg')

    cmd = [
        'ffmpeg', '-i', str(video_path),
        '-vf', f'fps={fps},scale=1280:-1',
        '-frames:v', str(max_frames),
        '-q:v', '3',
        '-y', pattern,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode()[:500]}")

    frames = sorted(output_dir.glob('frame_*.jpg'))
    result = []
    for i, f in enumerate(frames):
        ts = i / fps
        result.append((ts, f))
    return result[:max_frames]


def compute_content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def get_video_duration(video_path: Path) -> Optional[float]:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_format', str(video_path)
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, _ = await proc.communicate()
        info = json.loads(out)
        return float(info['format']['duration'])
    except Exception:
        return None


async def process_recording(
    recording_id: str,
    db_path: str,
    storage: LocalStorageBackend,
    meta_client,
    fps: float = 1.0,
    max_frames: int = 300,
    max_api_calls: int = 300,
):
    """
    Full processing pipeline for one recording.
    Updates recording status throughout.
    Does NOT raise — all errors are captured in processing_errors.
    """
    errors = []
    frame_count = 0

    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT video_path, recording_datetime, recording_timezone FROM recordings WHERE recording_id = ?",
            (recording_id,)
        )).fetchone()
        if not row:
            return
        video_rel_path, recording_datetime, recording_timezone = row

        await db.execute(
            "UPDATE recordings SET processing_status=?, updated_at=? WHERE recording_id=?",
            (ProcessingStatus.PROCESSING.value, _now(), recording_id)
        )
        await db.commit()

    try:
        video_path = await storage.get_video_path(video_rel_path)

        # Get duration
        duration = await get_video_duration(video_path)
        async with get_db(db_path) as db:
            await db.execute(
                "UPDATE recordings SET video_duration_seconds=?, updated_at=? WHERE recording_id=?",
                (duration, _now(), recording_id)
            )
            await db.commit()

        # Extract frames to tmp dir
        tmp_dir = storage.tmp_dir / recording_id
        frames = await extract_frames(video_path, tmp_dir, fps=fps, max_frames=max_frames)

        api_calls = 0
        for (ts, frame_path) in frames:
            async with aiofiles.open(frame_path, 'rb') as f:
                frame_data = await f.read()

            content_hash = compute_content_hash(frame_data)
            model_version = meta_client.model_version

            # Check cache
            cached_analysis = None
            async with get_db(db_path) as db:
                cache_row = await (await db.execute(
                    "SELECT analysis_result FROM frame_cache WHERE content_hash=? AND model_version=?",
                    (content_hash, model_version)
                )).fetchone()
                if cache_row:
                    cached_analysis = json.loads(cache_row[0])

            if cached_analysis is None and api_calls < max_api_calls:
                try:
                    cached_analysis = await meta_client.analyze_frame(frame_data)
                    api_calls += 1
                    # Store in cache
                    async with get_db(db_path) as db:
                        await db.execute(
                            "INSERT OR REPLACE INTO frame_cache(content_hash, model_version, analysis_result, created_at) VALUES(?,?,?,?)",
                            (content_hash, model_version, json.dumps(cached_analysis), _now())
                        )
                        await db.commit()
                except Exception as e:
                    errors.append(f"Frame {ts:.1f}s: model error: {str(e)[:200]}")
                    continue
            elif cached_analysis is None:
                errors.append(f"Frame {ts:.1f}s: skipped (API call limit reached)")
                continue

            # Save frame to storage
            frame_id = f"{recording_id}_{ts:.3f}"
            async with aiofiles.open(frame_path, 'rb') as f:
                fd = await f.read()
            frame_rel_path = await storage.save_frame(recording_id, frame_id, fd)

            # Parse observations from model response
            observations = _parse_observations(
                analysis=cached_analysis,
                recording_id=recording_id,
                frame_id=frame_id,
                video_timestamp_seconds=ts,
                recording_datetime=recording_datetime,
                recording_timezone=recording_timezone,
                source_frame_path=frame_rel_path,
                content_hash=content_hash,
                model_version=model_version,
            )

            # Store observations
            async with get_db(db_path) as db:
                for obs in observations:
                    await db.execute(
                        """INSERT OR IGNORE INTO observations(
                            observation_id, recording_id, frame_id,
                            video_timestamp_seconds, frame_capture_timestamp,
                            recording_datetime, recording_timezone,
                            object_label, object_attributes, scene_description,
                            source_frame_path, detection_score, model_version,
                            index_version, content_hash, raw_model_response, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            obs['observation_id'], obs['recording_id'], obs['frame_id'],
                            obs['video_timestamp_seconds'], obs.get('frame_capture_timestamp'),
                            obs.get('recording_datetime'), obs.get('recording_timezone'),
                            obs['object_label'], json.dumps(obs['object_attributes']),
                            obs.get('scene_description'), obs['source_frame_path'],
                            obs['detection_score'], obs.get('model_version'),
                            obs.get('index_version', '1'), obs.get('content_hash'),
                            json.dumps(cached_analysis), _now(),
                        )
                    )
                await db.commit()

            frame_count += 1

        # Cleanup tmp frames
        import shutil
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)

        # Generate thumbnail from first frame (if any frames exist)
        await _generate_thumbnail(recording_id, storage, db_path)

        status = ProcessingStatus.INDEXED if not errors else ProcessingStatus.PARTIAL
        async with get_db(db_path) as db:
            await db.execute(
                """UPDATE recordings SET
                    processing_status=?, frame_count=?, processing_errors=?,
                    indexing_status_detail=?, updated_at=?
                   WHERE recording_id=?""",
                (
                    status.value, frame_count, json.dumps(errors),
                    f"Indexed {frame_count} frames, {api_calls} API calls",
                    _now(), recording_id
                )
            )
            await db.commit()

        # Extract structured events from the indexed observations
        try:
            from .event_extractor import extract_events_for_recording
            n_events = await extract_events_for_recording(recording_id, db_path)
            logger.info("Extracted %d events for recording %s", n_events, recording_id)
        except Exception as e:
            logger.warning("Event extraction failed for %s: %s", recording_id, e)

        # Run cross-camera correlation for all recordings
        try:
            from .correlator import correlate_across_cameras
            await correlate_across_cameras(db_path)
        except Exception as e:
            logger.warning("Correlation failed after processing %s: %s", recording_id, e)

    except Exception as e:
        errors.append(f"Fatal processing error: {str(e)[:500]}")
        logger.exception(f"Processing failed for recording {recording_id}")
        async with get_db(db_path) as db:
            await db.execute(
                """UPDATE recordings SET
                    processing_status=?, processing_errors=?, frame_count=?,
                    indexing_status_detail=?, updated_at=?
                   WHERE recording_id=?""",
                (
                    ProcessingStatus.FAILED.value, json.dumps(errors), frame_count,
                    "Processing failed", _now(), recording_id
                )
            )
            await db.commit()


def _parse_observations(
    analysis: dict,
    recording_id: str,
    frame_id: str,
    video_timestamp_seconds: float,
    recording_datetime: Optional[str],
    recording_timezone: Optional[str],
    source_frame_path: str,
    content_hash: str,
    model_version: str,
) -> list[dict]:
    """
    Parse AI model response into observation dicts.
    Model is expected to return:
    {
      "objects": [
        {
          "label": "notebook",
          "attributes": {"color": "blue", "shape": "rectangular", ...},
          "scene_description": "A blue notebook on a wooden desk",
          "confidence": 0.92
        }
      ],
      "scene_summary": "Kitchen with wooden furniture"
    }
    """
    objects = analysis.get('objects', [])
    result = []
    for obj in objects:
        attrs = obj.get('attributes', {})
        result.append({
            'observation_id': str(uuid.uuid4()),
            'recording_id': recording_id,
            'frame_id': frame_id,
            'video_timestamp_seconds': video_timestamp_seconds,
            'recording_datetime': recording_datetime,
            'recording_timezone': recording_timezone,
            'object_label': obj.get('label', 'unknown'),
            'object_attributes': attrs if isinstance(attrs, dict) else {},
            'scene_description': obj.get('scene_description') or analysis.get('scene_summary'),
            'source_frame_path': source_frame_path,
            'detection_score': float(obj.get('confidence', 0.5)),
            'model_version': model_version,
            'index_version': '1',
            'content_hash': content_hash,
        })
    return result


async def _generate_thumbnail(recording_id: str, storage: LocalStorageBackend, db_path: str):
    """Extract first frame as thumbnail."""
    frame_dir = storage.frames_dir / recording_id
    if not frame_dir.exists():
        return
    frames = sorted(frame_dir.glob('*.jpg'))
    if not frames:
        return
    async with aiofiles.open(frames[0], 'rb') as f:
        data = await f.read()
    thumb_path = await storage.save_thumbnail(recording_id, data)
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE recordings SET thumbnail_path=?, updated_at=? WHERE recording_id=?",
            (thumb_path, _now(), recording_id)
        )
        await db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
