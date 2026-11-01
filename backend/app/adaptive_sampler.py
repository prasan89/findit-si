"""PTS-aware adaptive sampler: coarse context + dense motion windows.

Sample times snap to original container presentation timestamps (PTS)
when ffprobe can list them, so brief events keep their true positions
instead of a synthetic uniform grid.
"""
import asyncio
import json
import logging
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SamplePoint:
    timestamp_seconds: float
    density: str  # "coarse" | "dense"
    timestamp_source: str  # "container_pts" | "coarse_grid"
    pts_seconds: Optional[float] = None


@dataclass
class SamplePlan:
    samples: list[SamplePoint] = field(default_factory=list)
    cost: dict = field(default_factory=dict)
    uncovered_spans: list[list[float]] = field(default_factory=list)


def snap_to_pts(requested: float, container_pts: list[float]) -> float:
    """Return the container PTS nearest to the requested time."""
    if not container_pts:
        return requested
    i = bisect_left(container_pts, requested)
    if i <= 0:
        return container_pts[0]
    if i >= len(container_pts):
        return container_pts[-1]
    before, after = container_pts[i - 1], container_pts[i]
    return before if abs(requested - before) <= abs(after - requested) else after


def build_sample_plan(
    duration: float,
    windows: list,
    coarse_fps: float,
    dense_fps: float,
    max_dense_seconds: float,
    container_pts: Optional[list[float]] = None,
    start: float = 0.0,
    end: Optional[float] = None,
) -> SamplePlan:
    """Build a coarse-everywhere + dense-in-windows sample plan.

    Dense coverage is truncated to max_dense_seconds (in window order);
    truncated spans are reported in uncovered_spans for PARTIAL surfacing.
    """
    end = duration if end is None else min(end, duration)
    start = max(0.0, start)
    source = "container_pts" if container_pts else "coarse_grid"

    def _snap(t: float) -> tuple[float, Optional[float]]:
        if container_pts:
            pts = snap_to_pts(t, container_pts)
            return pts, pts
        return t, None

    dense_budget = max(0.0, max_dense_seconds)
    dense_times: list[float] = []
    uncovered: list[list[float]] = []
    for w in windows:
        ws = max(start, w.start_seconds)
        we = min(end, w.end_seconds)
        if we <= ws:
            continue
        span_len = we - ws
        if dense_budget <= 0:
            uncovered.append([ws, we])
            continue
        cover_end = ws + min(span_len, dense_budget)
        t = ws
        step = 1.0 / dense_fps if dense_fps > 0 else span_len
        while t <= cover_end + 1e-9:
            dense_times.append(t)
            t += step
        dense_budget -= (cover_end - ws)
        if cover_end < we - 1e-9:
            uncovered.append([cover_end, we])

    dense_covered = max_dense_seconds - dense_budget
    coarse_times: list[float] = []
    t = start
    step = 1.0 / coarse_fps if coarse_fps > 0 else (end - start)
    while t <= end + 1e-9:
        coarse_times.append(t)
        t += step

    samples: dict[float, SamplePoint] = {}
    for t in coarse_times:
        snapped, pts = _snap(t)
        key = round(snapped, 6)
        samples.setdefault(key, SamplePoint(
            timestamp_seconds=snapped, density="coarse",
            timestamp_source=source, pts_seconds=pts))
    for t in dense_times:
        snapped, pts = _snap(t)
        key = round(snapped, 6)
        samples[key] = SamplePoint(
            timestamp_seconds=snapped, density="dense",
            timestamp_source=source, pts_seconds=pts)

    ordered = [samples[k] for k in sorted(samples)]
    n_dense = sum(1 for s in ordered if s.density == "dense")
    return SamplePlan(
        samples=ordered,
        cost={
            "coarse_samples": len(ordered) - n_dense,
            "dense_samples": n_dense,
            "dense_seconds": round(dense_covered, 3),
            "max_dense_seconds": max_dense_seconds,
            "capped": bool(uncovered),
        },
        uncovered_spans=[[round(a, 3), round(b, 3)] for a, b in uncovered],
    )


async def get_container_frame_pts(video_path: Path) -> Optional[list[float]]:
    """List original frame PTS via ffprobe; None when unavailable."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-select_streams", "v:0", "-show_entries",
           "frame=pts_time,best_effort_timestamp_time,pkt_pts_time",
           str(video_path)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except Exception:
        return None
    try:
        info = json.loads(out)
        pts = []
        for f in info.get("frames", []):
            raw = (f.get("pts_time")
                   or f.get("best_effort_timestamp_time")
                   or f.get("pkt_pts_time"))
            if raw is not None:
                pts.append(float(raw))
        pts.sort()
        return pts or None
    except Exception:
        return None


async def extract_frames_at_plan(
    video_path: Path,
    output_dir: Path,
    plan: SamplePlan,
    max_extract: int = 300,
) -> list[tuple[SamplePoint, Path]]:
    """Extract one JPEG per sample point (accurate per-sample seek).

    Bounded by max_extract; callers enforce cost caps before invoking.
    """
    from .processor import _communicate_with_timeout, _ffmpeg_timeout_s
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[SamplePoint, Path]] = []
    for i, sample in enumerate(plan.samples[:max_extract]):
        out = output_dir / f"adaptive_{i:06d}.jpg"
        cmd = ["ffmpeg", "-ss", f"{sample.timestamp_seconds:.3f}",
               "-i", str(video_path), "-frames:v", "1",
               "-q:v", "3", "-y", str(out)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            _, stderr = await _communicate_with_timeout(
                proc, _ffmpeg_timeout_s(), "ffmpeg adaptive extraction")
        except Exception as e:
            logger.warning("adaptive extraction failed at t=%.2f: %s",
                           sample.timestamp_seconds, e)
            continue
        if proc.returncode != 0 or not out.exists():
            logger.warning("adaptive extraction failed at t=%.2f: %s",
                           sample.timestamp_seconds,
                           stderr.decode()[:200] if stderr else "")
            continue
        results.append((sample, out))
    return results
