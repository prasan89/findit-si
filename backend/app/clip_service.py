"""Event-centered clip service: context spans + frame-accurate extraction.

Expanded event spans (before/after context) that overlap are merged into a
single continuous clip — an event plus its context is never split across
chunk boundaries. Every clip carries timestamp + frame provenance.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EventClip:
    start_seconds: float
    end_seconds: float
    event_spans: list[tuple[float, float]] = field(default_factory=list)
    timestamp_source: str = "coarse_grid"
    provenance: dict = field(default_factory=dict)
    clip_path: Optional[str] = None


def expand_span(start: float, end: float, before: float, after: float,
                duration: Optional[float]) -> tuple[float, float]:
    s = max(0.0, start - before)
    e = end + after
    if duration is not None:
        e = min(e, duration)
    return (s, max(s, e))


def merge_overlapping_spans(
        spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping/touching spans into continuous ranges."""
    ordered = sorted((min(a, b), max(a, b)) for a, b in spans)
    merged: list[list[float]] = []
    for s, e in ordered:
        if merged and s <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def build_event_clips(
    event_spans: list[tuple[float, float]],
    before: float,
    after: float,
    duration: Optional[float],
    timestamp_source: str = "coarse_grid",
    frame_pts: Optional[list[float]] = None,
) -> list[EventClip]:
    """Expand each event span with context and merge overlaps into clips."""
    if not event_spans:
        return []
    expanded = [expand_span(s, e, before, after, duration)
                for s, e in event_spans]
    merged = merge_overlapping_spans(expanded)
    clips = []
    for ms, me in merged:
        covered = [(s, e) for s, e in event_spans
                   if s <= me + 1e-9 and e >= ms - 1e-9]
        provenance = {
            "timestamp_source": timestamp_source,
            "event_start": covered[0][0] if len(covered) == 1 else None,
            "event_end": covered[0][1] if len(covered) == 1 else None,
            "merged_event_count": len(covered),
            "context_before_s": before,
            "context_after_s": after,
        }
        if frame_pts:
            provenance["clip_frame_pts"] = [
                p for p in frame_pts if ms - 1e-9 <= p <= me + 1e-9]
        clips.append(EventClip(
            start_seconds=ms, end_seconds=me, event_spans=covered,
            timestamp_source=timestamp_source, provenance=provenance))
    return clips


def _ffmpeg_timeout_s() -> float:
    try:
        from .config import get_settings
        return get_settings().ffmpeg_timeout_s
    except Exception:
        return 300.0


async def extract_clip(video_path: Path, start: float, end: float,
                       output_path: Path) -> Path:
    """Extract [start, end] as mp4 (re-encode for frame accuracy).

    Raises RuntimeError with a short ffmpeg excerpt on failure.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    start = max(0.0, start)
    dur = max(0.1, end - start)
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(video_path),
           "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", "-an", str(output_path)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_ffmpeg_timeout_s())
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise RuntimeError(f"clip extraction timed out after {_ffmpeg_timeout_s():.0f}s")
    if proc.returncode != 0 or not output_path.exists():
        raise RuntimeError(
            f"clip extraction failed: {stderr.decode()[:300] if stderr else ''}")
    return output_path


async def extract_frame_at(video_path: Path, t: float,
                           output_path: Path) -> Path:
    """Extract the frame nearest to t as JPEG."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(video_path),
           "-frames:v", "1", "-q:v", "3", str(output_path)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=min(_ffmpeg_timeout_s(), 60.0))
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise RuntimeError("frame extraction timed out")
    if proc.returncode != 0 or not output_path.exists():
        raise RuntimeError(
            f"frame extraction failed: {stderr.decode()[:300] if stderr else ''}")
    return output_path
