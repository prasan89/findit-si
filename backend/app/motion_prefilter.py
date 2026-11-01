"""Stage-0 motion prefilter: free candidate-window detection (zero LLM cost).

Runs ffmpeg scene detection over the container and groups motion spikes
into padded candidate windows. All timestamps derive from the container
(pts_time) or caller-supplied bounds — never hardcoded.
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


@dataclass
class CandidateWindow:
    start_seconds: float
    end_seconds: float
    reason: str = "motion_spike"
    score: float = 0.5


def spikes_to_windows(
    spike_times: list[float],
    pad_before: float,
    pad_after: float,
    duration: Optional[float],
    min_gap: float,
    reason: str = "motion_spike",
) -> list[CandidateWindow]:
    """Group sorted spike times into padded, clamped, merged windows."""
    spikes = sorted(t for t in spike_times if t >= 0)
    if duration is not None:
        spikes = [t for t in spikes if t <= duration]
    if not spikes:
        return []
    groups: list[list[float]] = [[spikes[0]]]
    for t in spikes[1:]:
        if t - groups[-1][-1] <= min_gap:
            groups[-1].append(t)
        else:
            groups.append([t])
    windows = []
    for g in groups:
        start = max(0.0, g[0] - pad_before)
        end = g[-1] + pad_after
        if duration is not None:
            end = min(end, duration)
        windows.append(CandidateWindow(
            start_seconds=start, end_seconds=end, reason=reason,
            score=min(1.0, 0.4 + 0.1 * len(g))))
    return windows


def _ffmpeg_timeout_s() -> float:
    try:
        from .config import get_settings
        return min(get_settings().ffmpeg_timeout_s, 120.0)
    except Exception:
        return 120.0


async def detect_candidate_windows(
    video_path: Path,
    duration: Optional[float] = None,
    scene_threshold: float = 0.3,
    pad_before: float = 5.0,
    pad_after: float = 5.0,
    min_gap: float = 2.0,
    start: Optional[float] = None,
    end: Optional[float] = None,
) -> list[CandidateWindow]:
    """Detect motion-spike windows via ffmpeg scene detection.

    Returns [] when ffmpeg is unavailable or fails — the pipeline then
    falls back to coarse-only coverage (never a fatal error).
    """
    base = ["ffmpeg"]
    if start is not None and start > 0:
        base += ["-ss", str(max(0.0, start))]
    base += ["-i", str(video_path)]
    if end is not None and (start is None or end > start):
        base += ["-t", str(end - (start or 0.0))]
    # Passthrough frame timing is required so CFR duplication cannot invent
    # false spikes. The flag was renamed in ffmpeg 5 (-vsync → -fps_mode);
    # try the modern spelling first, then the legacy one.
    variants = [
        base + ["-vf", f"select='gt(scene,{scene_threshold})',showinfo",
                "-fps_mode", "passthrough", "-f", "null", "-"],
        base + ["-vf", f"select='gt(scene,{scene_threshold})',showinfo",
                "-vsync", "0", "-f", "null", "-"],
    ]
    stderr = b""
    try:
        for cmd in variants:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=_ffmpeg_timeout_s())
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                logger.warning("motion prefilter timed out for %s", video_path)
                return []
            if proc.returncode == 0:
                break
            if b"Unrecognized option" not in (stderr or b""):
                logger.warning("motion prefilter failed for %s: %s",
                               video_path, stderr.decode()[:200])
                return []
        else:
            return []
    except FileNotFoundError:
        logger.warning("ffmpeg not found; motion prefilter disabled")
        return []
    except Exception as e:
        logger.warning("motion prefilter failed for %s: %s", video_path, e)
        return []
    offset = start or 0.0
    spikes = [float(m.group(1)) + offset
              for m in _PTS_RE.finditer(stderr.decode(errors="replace"))]
    span_duration = (end if end is not None else duration)
    return spikes_to_windows(spikes, pad_before, pad_after, span_duration,
                             min_gap)
