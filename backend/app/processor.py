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
from .provider import AccountedFrameAnalyzer, BudgetExceededError

logger = logging.getLogger(__name__)

# Frame extraction
def _ffmpeg_timeout_s() -> float:
    try:
        from .config import get_settings
        return get_settings().ffmpeg_timeout_s
    except Exception:
        return 300.0


async def _communicate_with_timeout(proc, timeout_s: float, what: str):
    """Wait for a decoder subprocess with a kill-on-timeout guard (Phase 6)."""
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise RuntimeError(f"{what} timed out after {timeout_s:.0f}s")


async def extract_frames(
    video_path: Path,
    output_dir: Path,
    fps: float = 1.0,
    max_frames: int = 300,
) -> list[tuple[float, Path]]:
    """
    Extract frames from video using ffmpeg.
    Returns list of (timestamp_seconds, frame_path).

    Timestamps are sourced from actual container PTS via ffprobe when
    available (PTS-accurate). Falls back to synthetic index/fps for
    containers where ffprobe cannot enumerate frames.

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
    _, stderr = await _communicate_with_timeout(
        proc, _ffmpeg_timeout_s(), "ffmpeg frame extraction")

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode()[:500]}")

    frames = sorted(output_dir.glob('frame_*.jpg'))

    # Attempt PTS-accurate timestamps: ffprobe lists all container frames;
    # we sample that list at the same nominal interval as ffmpeg's fps filter.
    pts_map = await _build_frame_pts_map(video_path, fps, len(frames))

    result = []
    for i, f in enumerate(frames):
        ts = pts_map[i] if i < len(pts_map) else i / fps
        result.append((ts, f))
    return result[:max_frames]


async def _build_frame_pts_map(
    video_path: Path,
    fps: float,
    n_frames: int,
) -> list[float]:
    """Build a list of n_frames PTS-accurate timestamps for extracted frames.

    Uses get_container_frame_pts() from adaptive_sampler to get all
    container frame times, then picks the subset corresponding to ffmpeg's
    fps filter (one frame every 1/fps seconds, starting at 0).

    Returns [] on any error — callers fall back to synthetic i/fps.
    """
    if n_frames == 0:
        return []
    try:
        from .adaptive_sampler import get_container_frame_pts, snap_to_pts
        container_pts = await get_container_frame_pts(video_path)
        if not container_pts:
            return []
        step = 1.0 / fps if fps > 0 else 1.0
        pts_map: list[float] = []
        for i in range(n_frames):
            nominal = i * step
            pts_map.append(snap_to_pts(nominal, container_pts))
        return pts_map
    except Exception as exc:
        logger.debug("PTS map build failed, using synthetic timestamps: %s", exc)
        return []


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
        out, _ = await asyncio.wait_for(
            proc.communicate(), timeout=min(_ffmpeg_timeout_s(), 60.0))
        info = json.loads(out)
        return float(info['format']['duration'])
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except (ProcessLookupError, NameError):
            pass
        return None
    except Exception:
        return None


# Phase 6: cache keys are (content_hash, model_version, prompt_version) so
# cached analyses can never be reused across incompatible prompts/models.
# Bumped to "2" for the Phase 4 prompt rewrite; v1 rows simply never hit.
# "h1" is the hazard/animal second-pass prompt version.
PROMPT_VERSION = "2"
LEGACY_PROMPT_VERSION = "1"
HAZARD_PROMPT_VERSION = "h1"
# Upload understanding pass: segment understanding for action/event indexing.
# Versioned separately so cached per-frame analyses are unaffected.
UPLOAD_UNDERSTANDING_PROMPT_VERSION = "u1"
# Frames per upload segment window (keeps prompt + images within token budget)
UPLOAD_SEGMENT_FRAMES = 8
# Stride between consecutive segment windows (half the window = 50% overlap)
UPLOAD_SEGMENT_STRIDE = 4


async def get_cached_analysis(
    db,
    content_hash: str,
    model_version: str,
    prompt_version: str = PROMPT_VERSION,
) -> Optional[dict]:
    """Fetch a cached analysis scoped to content+model+prompt. None on miss.

    Tolerates pre-Phase-6 databases without the prompt_version column.
    """
    try:
        row = await (await db.execute(
            "SELECT analysis_result FROM frame_cache "
            "WHERE content_hash=? AND model_version=? AND prompt_version=?",
            (content_hash, model_version, prompt_version)
        )).fetchone()
    except Exception:
        row = await (await db.execute(
            "SELECT analysis_result FROM frame_cache "
            "WHERE content_hash=? AND model_version=?",
            (content_hash, model_version)
        )).fetchone()
    if row:
        try:
            cached = json.loads(row[0])
        except Exception:
            return None
        # P0.1: cached results are validated before being treated as
        # successful. A poisoned entry is dropped (self-heal) and the frame
        # is re-analyzed instead of failing or corrupting the index.
        try:
            from .meta_client import validate_frame_analysis
            return validate_frame_analysis(cached)
        except Exception:
            try:
                await db.execute(
                    "DELETE FROM frame_cache WHERE content_hash=? AND model_version=?",
                    (content_hash, model_version),
                )
                await db.commit()
            except Exception:
                pass
            return None
    return None


async def record_frame_failure(
    db_path: str,
    recording_id: str,
    video_timestamp_seconds: float,
    exc: BaseException,
    model_version: str,
) -> None:
    """Persist a structured failed-frame record for forensics and reprocessing.

    Captures the exact failing model text (capped) so the next malformed
    response is diagnosable instead of lost. Never raises.
    """
    try:
        from .meta_client import MAX_RAW_FAILURE_CHARS
        code = getattr(exc, "code", None) or type(exc).__name__
        finish_reason = getattr(exc, "finish_reason", None)
        raw = getattr(exc, "raw_response", None)
        if raw is not None:
            raw = str(raw)[:MAX_RAW_FAILURE_CHARS]
        async with get_db(db_path) as db:
            await db.execute(
                """INSERT INTO frame_failures(
                    failure_id, recording_id, video_timestamp_seconds,
                    error_code, finish_reason, model_version, raw_response,
                    created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), recording_id, video_timestamp_seconds,
                    str(code),
                    str(finish_reason) if finish_reason is not None else None,
                    model_version, raw, _now(),
                ),
            )
            await db.commit()
    except Exception:
        pass


async def store_cached_analysis(
    db,
    content_hash: str,
    model_version: str,
    analysis: dict,
    prompt_version: str = PROMPT_VERSION,
) -> None:
    """Store an analysis under the versioned cache key (with legacy fallback)."""
    try:
        await db.execute(
            "INSERT OR REPLACE INTO frame_cache("
            "content_hash, model_version, prompt_version, analysis_result, created_at"
            ") VALUES(?,?,?,?,?)",
            (content_hash, model_version, prompt_version,
             json.dumps(analysis), _now())
        )
    except Exception:
        await db.execute(
            "INSERT OR REPLACE INTO frame_cache("
            "content_hash, model_version, analysis_result, created_at"
            ") VALUES(?,?,?,?)",
            (content_hash, model_version, json.dumps(analysis), _now())
        )


async def prune_frame_cache(db_path: str, retention_days: int) -> int:
    """Delete cache rows older than retention_days. Returns rows deleted.

    retention_days <= 0 keeps everything. Analyses contain model output only
    (no credentials); callers decide policy via frame_cache_retention_days.
    """
    if retention_days <= 0:
        return 0
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    async with get_db(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM frame_cache WHERE created_at < ?", (cutoff,))
        await db.commit()
        return cursor.rowcount if cursor.rowcount is not None else 0


async def _get_video_codec(video_path: Path) -> Optional[str]:
    """Return the video stream codec name (e.g. 'h264', 'hevc') via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-select_streams", "v:0", "-show_entries", "stream=codec_name",
        str(video_path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        info = json.loads(out)
        streams = info.get("streams", [])
        if streams:
            return streams[0].get("codec_name")
    except Exception:
        pass
    return None


async def _ensure_h264(
    video_path: Path,
    recording_id: str,
    storage: LocalStorageBackend,
    db_path: str,
) -> Path:
    """Transcode to H.264 when the source codec is not browser-compatible.

    Browser-safe codecs (h264 / avc1 / avc) are passed through unchanged.
    All others (h265/hevc, vp9, av1, mjpeg, …) are transcoded to H.264 with
    CRF 23 / fast preset and faststart moov for progressive streaming.

    Returns the (possibly new) Path to use for all subsequent processing.
    The recording row's video_path is updated in-place if transcoding runs.
    """
    BROWSER_SAFE = frozenset({"h264", "avc", "avc1"})
    codec = await _get_video_codec(video_path)
    if codec and codec.lower() in BROWSER_SAFE:
        return video_path  # already compatible — no-op

    if codec:
        logger.info(
            "Transcoding recording %s from codec %s → H.264 for browser playback",
            recording_id[:8], codec)
    else:
        logger.warning(
            "Could not detect codec for recording %s; attempting H.264 transcode",
            recording_id[:8])

    # Output alongside original with _h264 suffix to avoid overwriting source.
    h264_path = video_path.with_name(video_path.stem + "_h264.mp4")
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac", "-movflags", "+faststart",
        "-y", str(h264_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await _communicate_with_timeout(
            proc, _ffmpeg_timeout_s(), "H.264 transcode")
    except RuntimeError as exc:
        logger.warning("H.264 transcode failed for %s: %s; using original",
                       recording_id[:8], exc)
        return video_path

    if proc.returncode != 0 or not h264_path.exists():
        logger.warning("H.264 transcode failed for %s (rc=%s); using original",
                       recording_id[:8], proc.returncode)
        return video_path

    # Persist new path in the recordings row
    new_rel_path = storage.videos_dir.name + "/" + h264_path.name
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE recordings SET video_path = ?, updated_at = ? WHERE recording_id = ?",
            (new_rel_path, _now(), recording_id))
        await db.commit()

    logger.info("Transcoded %s → %s", video_path.name, h264_path.name)
    return h264_path


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

    Metric definitions (each updated at exactly one lifecycle stage):
    - extracted: frames produced by ffmpeg (len(frames)).
    - submitted: frames sent to the model (api_calls + failed_frames).
    - api_calls: successful model responses (incremented only after
      analyze_frame returns; a local parsing failure is NOT an API call).
    - indexed (frame_count): frames fully persisted with observations.
    - failed_frames: model/parsing failures; the frame is skipped, never
      persisted, and never blocks other frames.
    - cache_hits: frames served from frame_cache without a model call.
    - skipped_frames: frames not attempted (API-call limit, budget stop).
    Invariant: extracted == indexed + failed + skipped.
    """
    errors = []
    frame_count = 0
    failed_frames = 0
    skipped_frames = 0

    # Bind recording_id to the accounted client if it supports it.
    if isinstance(meta_client, AccountedFrameAnalyzer):
        meta_client = meta_client.with_recording_id(recording_id)

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

        # Transcode non-browser-compatible codecs (H.265/HEVC, MJPEG, etc.)
        # to H.264 so video preview works in all major browsers.
        video_path = await _ensure_h264(video_path, recording_id, storage, db_path)

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
        cache_hits = 0
        # Indexed frame paths (timestamp, frame_rel_path) for upload understanding pass
        indexed_frame_paths: list[tuple[float, str]] = []
        for (ts, frame_path) in frames:
            async with aiofiles.open(frame_path, 'rb') as f:
                frame_data = await f.read()

            content_hash = compute_content_hash(frame_data)
            model_version = meta_client.model_version

            # ── General pass ──────────────────────────────────────────────
            # Check cache (content + model + prompt scoped)
            cached_analysis = None
            async with get_db(db_path) as db:
                cached_analysis = await get_cached_analysis(
                    db, content_hash, model_version)
            if cached_analysis is not None:
                cache_hits += 1

            if cached_analysis is None and api_calls < max_api_calls:
                try:
                    cached_analysis = await meta_client.analyze_frame(frame_data)
                    api_calls += 1
                    # Store in cache
                    async with get_db(db_path) as db:
                        await store_cached_analysis(
                            db, content_hash, model_version, cached_analysis)
                        await db.commit()
                except BudgetExceededError as e:
                    errors.append(f"Budget limit reached at frame {ts:.1f}s: {str(e)[:200]}")
                    logger.warning("Budget exceeded during processing of %s: %s", recording_id, e)
                    break
                except Exception as e:
                    # One failed frame never aborts the rest. Structured
                    # failures carry a machine-readable [code] for triage.
                    failed_frames += 1
                    code = getattr(e, "code", None)
                    tag = f"[{code}] " if code else ""
                    errors.append(f"Frame {ts:.1f}s: model error {tag}{str(e)[:200]}")
                    await record_frame_failure(
                        db_path, recording_id, ts, e, model_version)
                    logger.warning(
                        "Frame %.1fs of %s failed (%s); continuing with next frame",
                        ts, recording_id, type(e).__name__,
                    )
                    continue
            elif cached_analysis is None:
                skipped_frames += 1
                errors.append(f"Frame {ts:.1f}s: skipped (API call limit reached)")
                continue

            # Save frame to storage
            frame_id = f"{recording_id}_{ts:.3f}"
            async with aiofiles.open(frame_path, 'rb') as f:
                fd = await f.read()
            frame_rel_path = await storage.save_frame(recording_id, frame_id, fd)
            # Track for upload understanding pass
            indexed_frame_paths.append((ts, frame_rel_path))

            # Parse observations from general model response
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
                analysis_pass="general",
            )

            # ── Hazard/animal second pass ──────────────────────────────────
            # Run a second analysis focused on animals, hazards, and unusual
            # floor-level objects — items the general prompt omits. Uses a
            # separate cache key (HAZARD_PROMPT_VERSION) so cached general
            # results are never confused with hazard results.
            hazard_analysis = None
            if api_calls < max_api_calls:
                async with get_db(db_path) as db:
                    hazard_analysis = await get_cached_analysis(
                        db, content_hash, model_version,
                        prompt_version=HAZARD_PROMPT_VERSION)
                if hazard_analysis is not None:
                    cache_hits += 1
                else:
                    try:
                        from .meta_client import HAZARD_ANALYSIS_PROMPT
                        hazard_analysis = await meta_client.analyze_frame(
                            frame_data, prompt=HAZARD_ANALYSIS_PROMPT)
                        api_calls += 1
                        async with get_db(db_path) as db:
                            await store_cached_analysis(
                                db, content_hash, model_version, hazard_analysis,
                                prompt_version=HAZARD_PROMPT_VERSION)
                            await db.commit()
                    except BudgetExceededError:
                        pass  # Hazard pass is best-effort; don't stop main loop
                    except Exception as e:
                        logger.warning(
                            "Hazard pass failed for frame %.1fs of %s: %s",
                            ts, recording_id, e)

            if hazard_analysis:
                hazard_obs = _parse_observations(
                    analysis=hazard_analysis,
                    recording_id=recording_id,
                    frame_id=frame_id,
                    video_timestamp_seconds=ts,
                    recording_datetime=recording_datetime,
                    recording_timezone=recording_timezone,
                    source_frame_path=frame_rel_path,
                    content_hash=content_hash,
                    model_version=model_version,
                    analysis_pass="hazard",
                )
                # Only keep hazard observations not already covered by general
                # pass (deduplicate by normalized label to avoid "snake on floor"
                # appearing twice if the general pass caught it in scene_summary).
                general_labels_lower = {
                    o["object_label"].lower() for o in observations}
                for hobs in hazard_obs:
                    if hobs["object_label"].lower() not in general_labels_lower:
                        observations.append(hobs)

            # Store observations. Idempotent per frame: prior rows for this
            # frame are removed first, so reprocessing (manual or retry)
            # cannot duplicate index records.
            async with get_db(db_path) as db:
                await db.execute(
                    "DELETE FROM observations WHERE recording_id=? AND frame_id=?",
                    (recording_id, frame_id),
                )
                for obs in observations:
                    try:
                        await db.execute(
                            """INSERT OR IGNORE INTO observations(
                                observation_id, recording_id, frame_id,
                                video_timestamp_seconds, frame_capture_timestamp,
                                recording_datetime, recording_timezone,
                                object_label, object_attributes, scene_description,
                                source_frame_path, detection_score, model_version,
                                index_version, content_hash, raw_model_response,
                                analysis_pass, created_at
                            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (
                                obs['observation_id'], obs['recording_id'], obs['frame_id'],
                                obs['video_timestamp_seconds'], obs.get('frame_capture_timestamp'),
                                obs.get('recording_datetime'), obs.get('recording_timezone'),
                                obs['object_label'], json.dumps(obs['object_attributes']),
                                obs.get('scene_description'), obs['source_frame_path'],
                                obs['detection_score'], obs.get('model_version'),
                                obs.get('index_version', '1'), obs.get('content_hash'),
                                json.dumps(cached_analysis),
                                obs.get('analysis_pass', 'general'), _now(),
                            )
                        )
                    except Exception:
                        # Fallback: insert without analysis_pass for older schemas
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

        # ── Upload understanding pass ──────────────────────────────────────────
        # After all per-frame observations are collected, run a segment-level
        # understanding pass using the general UPLOAD_UNDERSTANDING_PROMPT.
        # This produces: entities, actions, interactions, changes, and unusual
        # observations — independent of any future user query.
        #
        # Strategy: split indexed frames into overlapping windows of
        # UPLOAD_SEGMENT_FRAMES frames each, stride by half that size so
        # transitions between segments are covered. Cap to max_api_calls.
        if indexed_frame_paths and api_calls < max_api_calls:
            upload_understanding_results = await _run_upload_understanding(
                recording_id=recording_id,
                db_path=db_path,
                storage=storage,
                meta_client=meta_client,
                indexed_frame_paths=indexed_frame_paths,
                api_calls=api_calls,
                max_api_calls=max_api_calls,
                errors=errors,
            )
            understanding_calls = upload_understanding_results["api_calls_used"]
        else:
            understanding_calls = 0

        # Cleanup tmp frames
        import shutil
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)

        # Generate thumbnail from first frame (if any frames exist)
        await _generate_thumbnail(recording_id, storage, db_path)

        # Frames never attempted after a budget break count as skipped so the
        # extracted == indexed + failed + skipped invariant holds.
        skipped_frames += len(frames) - frame_count - failed_frames - skipped_frames

        if frame_count == 0 and len(frames) > 0:
            status = ProcessingStatus.FAILED
        elif errors:
            status = ProcessingStatus.PARTIAL
        else:
            status = ProcessingStatus.INDEXED
        detail = (
            f"Indexed {frame_count}/{len(frames)} frames, {api_calls} API calls, "
            f"{cache_hits} cache hits, {failed_frames} failed, "
            f"{skipped_frames} skipped"
            + (f", {understanding_calls} understanding calls" if understanding_calls else "")
        )
        async with get_db(db_path) as db:
            await db.execute(
                """UPDATE recordings SET
                    processing_status=?, frame_count=?, processing_errors=?,
                    indexing_status_detail=?, updated_at=?
                   WHERE recording_id=?""",
                (
                    status.value, frame_count, json.dumps(errors),
                    detail,
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
    analysis_pass: str = "general",
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
    if not isinstance(analysis, dict):
        return []
    objects = analysis.get('objects', [])
    if not isinstance(objects, list):
        return []
    result = []
    for obj in objects:
        # Phase 4: harden against malformed model responses — skip unusable
        # entries, coerce and clamp the rest. Never crash the pipeline.
        if not isinstance(obj, dict):
            continue
        attrs = obj.get('attributes', {})
        if not isinstance(attrs, dict):
            attrs = {}
        try:
            confidence = float(obj.get('confidence', 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        label = obj.get('label', 'unknown')
        label = str(label)[:500] if label is not None else 'unknown'
        scene = obj.get('scene_description') or analysis.get('scene_summary')
        scene = str(scene)[:2000] if scene is not None else None
        result.append({
            'observation_id': str(uuid.uuid4()),
            'recording_id': recording_id,
            'frame_id': frame_id,
            'video_timestamp_seconds': video_timestamp_seconds,
            'recording_datetime': recording_datetime,
            'recording_timezone': recording_timezone,
            'object_label': label or 'unknown',
            'object_attributes': attrs,
            'scene_description': scene,
            'source_frame_path': source_frame_path,
            'detection_score': confidence,
            'model_version': model_version,
            'index_version': '1',
            'content_hash': content_hash,
            'analysis_pass': analysis_pass,
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


async def _run_upload_understanding(
    recording_id: str,
    db_path: str,
    storage: LocalStorageBackend,
    meta_client,
    indexed_frame_paths: list[tuple[float, str]],
    api_calls: int,
    max_api_calls: int,
    errors: list,
) -> dict:
    """Run the upload understanding pass on indexed frames.

    Splits the video into overlapping windows of UPLOAD_SEGMENT_FRAMES frames,
    calls analyze_upload_segment() on each, and stores the structured results
    in the upload_understandings table for use by extract_events_for_recording.

    Returns {"api_calls_used": int}.
    """
    if not indexed_frame_paths:
        return {"api_calls_used": 0}

    # Skip if client doesn't support analyze_upload_segment
    if not hasattr(meta_client, "analyze_upload_segment"):
        logger.debug("meta_client has no analyze_upload_segment; skipping upload pass")
        return {"api_calls_used": 0}

    frames_n = len(indexed_frame_paths)
    calls_used = 0

    # Build overlapping windows
    windows: list[list[tuple[float, str]]] = []
    i = 0
    while i < frames_n:
        window = indexed_frame_paths[i:i + UPLOAD_SEGMENT_FRAMES]
        windows.append(window)
        i += UPLOAD_SEGMENT_STRIDE

    logger.info(
        "Upload understanding: %d windows for recording %s (%d frames)",
        len(windows), recording_id[:8], frames_n,
    )

    segments_stored = 0
    for window in windows:
        if api_calls + calls_used >= max_api_calls:
            logger.info(
                "Upload understanding: stopping at %d/%d windows (API limit)",
                segments_stored, len(windows),
            )
            break

        # Build a combined content hash for this window (sorted frame hashes)
        # so the result can be cached independently per segment.
        ts_list = [ts for ts, _ in window]
        segment_key = ",".join(f"{ts:.3f}" for ts in ts_list)
        segment_hash = hashlib.sha256(
            f"{recording_id}:{segment_key}".encode()
        ).hexdigest()

        # Check if already stored for this recording+segment
        async with get_db(db_path) as db:
            try:
                existing = await (await db.execute(
                    """SELECT segment_hash FROM upload_understandings
                       WHERE recording_id = ? AND segment_hash = ?""",
                    (recording_id, segment_hash),
                )).fetchone()
            except Exception:
                existing = None  # table may not exist yet

        if existing:
            logger.debug("Upload understanding cached for segment %s", segment_key[:20])
            continue

        # Read frame bytes
        frame_bytes: list[bytes] = []
        frame_timestamps: list[float] = []
        for ts, frame_rel_path in window:
            try:
                frame_abs = storage.data_dir / frame_rel_path
                async with aiofiles.open(frame_abs, "rb") as f:
                    frame_bytes.append(await f.read())
                frame_timestamps.append(ts)
            except Exception as exc:
                logger.warning(
                    "Upload understanding: could not read frame %.1fs: %s", ts, exc)

        if not frame_bytes:
            continue

        # Call the understanding API
        try:
            result = await meta_client.analyze_upload_segment(
                frame_bytes, frame_timestamps
            )
            calls_used += 1
        except BudgetExceededError as exc:
            errors.append(f"Budget exceeded during upload understanding: {str(exc)[:200]}")
            logger.warning("Budget exceeded in upload understanding for %s", recording_id)
            break
        except Exception as exc:
            logger.warning(
                "Upload understanding failed for segment [%.1f-%.1f] of %s: %s",
                ts_list[0], ts_list[-1], recording_id[:8], exc,
            )
            continue

        # Store the result
        try:
            await _store_upload_understanding(
                db_path=db_path,
                recording_id=recording_id,
                segment_hash=segment_hash,
                start_seconds=ts_list[0],
                end_seconds=ts_list[-1],
                result=result,
            )
            segments_stored += 1
        except Exception as exc:
            logger.warning(
                "Failed to store upload understanding for %s: %s",
                recording_id[:8], exc,
            )

    logger.info(
        "Upload understanding: stored %d segments for recording %s (%d API calls)",
        segments_stored, recording_id[:8], calls_used,
    )
    return {"api_calls_used": calls_used}


async def _store_upload_understanding(
    db_path: str,
    recording_id: str,
    segment_hash: str,
    start_seconds: float,
    end_seconds: float,
    result: dict,
) -> None:
    """Persist one upload understanding segment result."""
    async with get_db(db_path) as db:
        await db.execute(
            """INSERT OR REPLACE INTO upload_understandings(
                segment_id, recording_id, segment_hash,
                start_seconds, end_seconds,
                scene_context, entities_json, actions_json,
                interactions_json, changes_json,
                unusual_observations_json, uncertain_observations_json,
                static_scene, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                recording_id,
                segment_hash,
                start_seconds,
                end_seconds,
                result.get("scene_context", ""),
                json.dumps(result.get("entities", [])),
                json.dumps(result.get("actions", [])),
                json.dumps(result.get("interactions", [])),
                json.dumps(result.get("changes", [])),
                json.dumps(result.get("unusual_observations", [])),
                json.dumps(result.get("uncertain_observations", [])),
                1 if result.get("static_scene") else 0,
                _now(),
            ),
        )
        await db.commit()
