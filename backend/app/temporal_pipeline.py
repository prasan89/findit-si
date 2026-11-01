"""Temporal detection pipeline: prefilter → sample → detect → clip → correlate.

Coarse-to-fine with explicit cost bounds:
- stage 0: ffmpeg motion prefilter (free, no LLM)
- stage 1: coarse candidates from indexed observations (no new LLM calls)
- stage 2: dense sampling + vision inside candidate windows, capped by
  per-recording and per-query dense-second budgets; overruns surface as
  PARTIAL with explicit uncovered spans (never silent truncation)
- stage 3: event-centered clips analyzed via the provider abstraction,
  correlated into observation-only sequences.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .adaptive_sampler import (build_sample_plan, extract_frames_at_plan,
                               get_container_frame_pts)
from .candidate_detector import Candidate, detect_candidates
from .clip_service import build_event_clips
from .database import get_db
from .motion_prefilter import CandidateWindow, detect_candidate_windows
from .temporal_correlator import correlate_sequences

logger = logging.getLogger(__name__)

MAX_CLIP_FRAMES = 8
MAX_CLIPS_ANALYZED = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def spans_overlap(a_start: float, a_end: float,
                  b_start: float, b_end: float) -> bool:
    """Inclusive span overlap (touching boundaries count as overlap)."""
    return max(a_start, b_start) <= min(a_end, b_end) + 1e-9


def _settings_snapshot() -> dict:
    try:
        from .config import get_settings
        s = get_settings()
        return {
            "coarse_fps": s.temporal_coarse_fps,
            "dense_fps": s.temporal_dense_fps,
            "motion_threshold": s.temporal_motion_threshold,
            "pre_context": s.temporal_pre_context_s,
            "post_context": s.temporal_post_context_s,
            "max_dense_per_recording": s.temporal_max_dense_seconds_per_recording,
            "max_refine_per_query": s.temporal_max_refine_seconds_per_query,
            "max_clip_seconds": s.temporal_max_clip_seconds,
            "min_candidate_gap": s.temporal_min_candidate_gap_s,
            "sequence_gap": s.temporal_sequence_gap_s,
        }
    except Exception:
        return {
            "coarse_fps": 1.0, "dense_fps": 4.0, "motion_threshold": 0.3,
            "pre_context": 5.0, "post_context": 5.0,
            "max_dense_per_recording": 60.0, "max_refine_per_query": 30.0,
            "max_clip_seconds": 60.0, "min_candidate_gap": 2.0,
            "sequence_gap": 8.0,
        }


async def _recording_dense_spend(db_path: str, recording_id: str) -> float:
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT COALESCE(SUM(dense_seconds), 0.0) FROM temporal_refine_log "
            "WHERE recording_id = ?", (recording_id,))).fetchone()
        return float(row[0]) if row else 0.0


async def _load_observations(db_path: str, recording_id: str,
                             start: Optional[float],
                             end: Optional[float]) -> list[dict]:
    import aiosqlite
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        query = ("SELECT observation_id, video_timestamp_seconds, object_label,"
                 " object_attributes, scene_description, source_frame_path,"
                 " detection_score FROM observations WHERE recording_id = ?")
        params: list = [recording_id]
        if start is not None:
            query += " AND video_timestamp_seconds >= ?"
            params.append(start)
        if end is not None:
            query += " AND video_timestamp_seconds <= ?"
            params.append(end)
        query += " ORDER BY video_timestamp_seconds"
        rows = await (await db.execute(query, tuple(params))).fetchall()
        return [dict(r) for r in rows]


async def _dense_analyze_and_store(
    recording_id: str, db_path: str, storage, meta_client,
    video_path: Path, plan, model_version: str,
) -> tuple[list[dict], list[tuple[float, bytes]]]:
    """Analyze dense samples via provider (cache-first); persist observations.

    Returns (new_observations, clip_frame_bytes as (ts, bytes)).
    """
    import aiofiles
    from .processor import (compute_content_hash, get_cached_analysis,
                            store_cached_analysis, _parse_observations)
    tmp_dir = storage.tmp_dir / f"{recording_id}_temporal"
    extracted = await extract_frames_at_plan(video_path, tmp_dir, plan)
    new_obs: list[dict] = []
    clip_frames: list[tuple[float, bytes]] = []
    try:
        for sample, frame_path in extracted:
            if sample.density != "dense":
                continue
            async with aiofiles.open(frame_path, "rb") as f:
                frame_data = await f.read()
            clip_frames.append((sample.timestamp_seconds, frame_data))
            content_hash = compute_content_hash(frame_data)
            cached = None
            async with get_db(db_path) as db:
                cached = await get_cached_analysis(
                    db, content_hash, model_version)
            if cached is None:
                try:
                    cached = await meta_client.analyze_frame(frame_data)
                except Exception as e:
                    logger.warning("dense frame t=%.2f failed: %s",
                                   sample.timestamp_seconds, e)
                    continue
                async with get_db(db_path) as db:
                    await store_cached_analysis(
                        db, content_hash, model_version, cached)
                    await db.commit()
            frame_id = f"{recording_id}_{sample.timestamp_seconds:.3f}_dense"
            frame_rel = await storage.save_frame(
                recording_id, frame_id, frame_data)
            parsed = _parse_observations(
                analysis=cached, recording_id=recording_id, frame_id=frame_id,
                video_timestamp_seconds=sample.timestamp_seconds,
                recording_datetime=None, recording_timezone=None,
                source_frame_path=frame_rel, content_hash=content_hash,
                model_version=model_version)
            async with get_db(db_path) as db:
                await db.execute(
                    "DELETE FROM observations WHERE recording_id=? AND frame_id=?",
                    (recording_id, frame_id))
                for obs in parsed:
                    await db.execute(
                        """INSERT OR IGNORE INTO observations(
                            observation_id, recording_id, frame_id,
                            video_timestamp_seconds, recording_datetime,
                            recording_timezone, object_label, object_attributes,
                            scene_description, source_frame_path, detection_score,
                            model_version, index_version, content_hash,
                            raw_model_response, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (obs["observation_id"], obs["recording_id"],
                         obs["frame_id"], obs["video_timestamp_seconds"],
                         obs.get("recording_datetime"),
                         obs.get("recording_timezone"), obs["object_label"],
                         json.dumps(obs["object_attributes"]),
                         obs.get("scene_description"), obs["source_frame_path"],
                         obs["detection_score"], obs.get("model_version"),
                         "1", obs.get("content_hash"), json.dumps(cached),
                         _now()))
                await db.commit()
            for obs in parsed:
                new_obs.append({
                    "observation_id": obs["observation_id"],
                    "video_timestamp_seconds": obs["video_timestamp_seconds"],
                    "object_label": obs["object_label"],
                    "scene_description": obs.get("scene_description"),
                    "detection_score": obs["detection_score"],
                    "source_frame_path": obs["source_frame_path"],
                })
    finally:
        import shutil
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
    return new_obs, clip_frames


def _pick_clip_frames(clip_frames: list[tuple[float, bytes]],
                      start: float, end: float) -> tuple[list[bytes], list[float]]:
    inside = sorted((ts, b) for ts, b in clip_frames if start <= ts <= end)
    if len(inside) > MAX_CLIP_FRAMES:
        step = len(inside) / MAX_CLIP_FRAMES
        inside = [inside[int(i * step)] for i in range(MAX_CLIP_FRAMES)]
    return [b for _, b in inside], [ts for ts, _ in inside]


async def run_temporal_detection(
    recording_id: str,
    db_path: str,
    storage,
    meta_client,
    start: Optional[float] = None,
    end: Optional[float] = None,
    dense_budget_s: Optional[float] = None,
    reason: str = "",
) -> dict:
    """Run the coarse-to-fine temporal pipeline over [start, end].

    dense_budget_s clamps this run's dense seconds (in addition to the
    configured per-query and per-recording caps). Always returns explicit
    uncovered spans + partial flag; never truncates silently.
    """
    cfg = _settings_snapshot()
    import aiosqlite
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rec = await (await db.execute(
            "SELECT video_path, video_duration_seconds, recording_datetime,"
            " recording_timezone FROM recordings WHERE recording_id = ?",
            (recording_id,))).fetchone()
        if rec is None:
            raise ValueError(f"Recording {recording_id} not found")
        recording = dict(rec)

    duration = recording.get("video_duration_seconds")
    video_path: Optional[Path] = None
    if recording.get("video_path"):
        try:
            candidate = await storage.get_video_path(recording["video_path"])
            if candidate.exists():
                video_path = candidate
        except ValueError:
            video_path = None
    if duration is None and video_path is not None:
        from .processor import get_video_duration
        duration = await get_video_duration(video_path)

    req_start = max(0.0, start or 0.0)
    req_end = end if end is not None else duration
    if req_end is None:
        req_end = req_start
    req_end = max(req_start, req_end)

    spent = await _recording_dense_spend(db_path, recording_id)
    recording_room = max(0.0, cfg["max_dense_per_recording"] - spent)
    query_cap = cfg["max_refine_per_query"]
    budget = min(recording_room, query_cap)
    if dense_budget_s is not None:
        budget = min(budget, max(0.0, dense_budget_s))

    timestamp_source = "coarse_grid"
    container_pts: Optional[list[float]] = None
    windows: list[CandidateWindow] = []
    if video_path is not None:
        container_pts = await get_container_frame_pts(video_path)
        if container_pts:
            timestamp_source = "container_pts"
        windows = await detect_candidate_windows(
            video_path, duration=duration,
            scene_threshold=cfg["motion_threshold"],
            pad_before=cfg["pre_context"], pad_after=cfg["post_context"],
            min_gap=cfg["min_candidate_gap"], start=req_start, end=req_end)
        windows = [w for w in windows
                   if w.end_seconds >= req_start and w.start_seconds <= req_end]
        if windows:
            from .clip_service import merge_overlapping_spans
            merged_spans = merge_overlapping_spans(
                [(w.start_seconds, w.end_seconds) for w in windows])
            by_start = sorted(windows, key=lambda w: w.start_seconds)
            merged_windows: list[CandidateWindow] = []
            for ms, me in merged_spans:
                donor = next(
                    (w for w in by_start
                     if w.start_seconds <= me and w.end_seconds >= ms),
                    by_start[0])
                merged_windows.append(CandidateWindow(
                    start_seconds=ms, end_seconds=me, reason=donor.reason,
                    score=donor.score))
            windows = merged_windows

    observations = await _load_observations(db_path, recording_id,
                                            req_start, req_end)
    uncovered: list[list[float]] = []
    dense_seconds = 0.0
    clip_frames: list[tuple[float, bytes]] = []
    if video_path is not None and budget > 0 and windows:
        plan = build_sample_plan(
            duration=duration or req_end, windows=windows,
            coarse_fps=0.0, dense_fps=cfg["dense_fps"],
            max_dense_seconds=budget, container_pts=container_pts,
            start=req_start, end=req_end)
        dense_seconds = plan.cost["dense_seconds"]
        uncovered = [list(s) for s in plan.uncovered_spans]
        model_version = getattr(meta_client, "model_version", "unknown")
        new_obs, clip_frames = await _dense_analyze_and_store(
            recording_id, db_path, storage, meta_client, video_path,
            plan, model_version)
        observations = sorted(observations + new_obs,
                              key=lambda o: o["video_timestamp_seconds"])
    elif req_end > req_start and (video_path is None or budget <= 0 or not windows):
        if video_path is None or budget <= 0:
            uncovered = [[round(req_start, 3), round(req_end, 3)]]

    candidates: list[Candidate] = detect_candidates(
        observations, windows, merge_gap=cfg["min_candidate_gap"])
    sequences = correlate_sequences(candidates, gap_seconds=cfg["sequence_gap"])

    clips = build_event_clips(
        [(s.start_seconds, s.end_seconds) for s in sequences],
        before=cfg["pre_context"], after=cfg["post_context"],
        duration=duration, timestamp_source=timestamp_source,
        frame_pts=container_pts)
    for clip in clips:
        if clip.end_seconds - clip.start_seconds > cfg["max_clip_seconds"]:
            clip.end_seconds = clip.start_seconds + cfg["max_clip_seconds"]

    analyze_clip = getattr(meta_client, "analyze_clip", None)
    events: list[dict] = []
    for idx, seq in enumerate(sequences):
        clip = clips[idx] if idx < len(clips) else None
        clip_analysis: dict = {"tracks": [], "observable_actions": [],
                               "action_sequence": [], "confirmed": [],
                               "uncertain": ["clip analysis unavailable"]}
        if clip is not None and analyze_clip is not None and clip_frames:
            payload, ts_list = _pick_clip_frames(
                clip_frames, clip.start_seconds, clip.end_seconds)
            if payload and idx < MAX_CLIPS_ANALYZED:
                try:
                    clip_analysis = await analyze_clip(payload, ts_list)
                except Exception as e:
                    clip_analysis["uncertain"] = [f"clip analysis failed: {e}"[:200]]
            elif idx >= MAX_CLIPS_ANALYZED:
                clip_analysis["uncertain"] = ["clip analysis skipped (run cap)"]
        member_obs = [o for o in observations
                      if seq.start_seconds <= o["video_timestamp_seconds"]
                      <= seq.end_seconds]
        confirming = len({o["video_timestamp_seconds"] for o in member_obs})
        single = all(c.single_frame_candidate for c in candidates
                     if seq.start_seconds <= c.start_seconds
                     and c.end_seconds <= seq.end_seconds) if candidates else True
        evidence_frames = _select_evidence_frames(
            observations, seq.start_seconds, seq.end_seconds, timestamp_source)
        mid = (seq.start_seconds + seq.end_seconds) / 2.0
        clip_url = None
        clip_obj = None
        if clip is not None:
            clip_url = (f"/api/recordings/{recording_id}/clip"
                        f"?start={clip.start_seconds:.3f}&end={clip.end_seconds:.3f}")
            clip_obj = {"start_seconds": round(clip.start_seconds, 3),
                        "end_seconds": round(clip.end_seconds, 3),
                        "timestamp_source": clip.timestamp_source,
                        "provenance": clip.provenance, "clip_url": clip_url}
        observed_facts = [f"Observed sequence span "
                          f"{seq.start_seconds:.1f}s–{seq.end_seconds:.1f}s"]
        for c in candidates:
            if seq.start_seconds <= c.start_seconds and c.end_seconds <= seq.end_seconds:
                observed_facts.append(
                    f"Candidate {c.candidate_type} at "
                    f"{c.start_seconds:.1f}s–{c.end_seconds:.1f}s "
                    f"(confidence {c.confidence:.2f})")
        event = {
            "event_id": str(uuid.uuid4()),
            "recording_id": recording_id,
            "category": seq.category,
            "candidate_types": seq.candidate_types,
            "stages": seq.stages,
            "start_seconds": round(seq.start_seconds, 3),
            "end_seconds": round(seq.end_seconds, 3),
            "timestamp_seconds": round(mid, 3),
            "timestamp_source": timestamp_source,
            "confidence": seq.confidence,
            "confirming_frame_count": confirming,
            "single_frame_candidate": single,
            "tracks": clip_analysis.get("tracks", []),
            "observable_actions": clip_analysis.get("observable_actions", []),
            "action_sequence": clip_analysis.get("action_sequence", []),
            "confirmed": clip_analysis.get("confirmed", []),
            "uncertain": clip_analysis.get("uncertain", []),
            "observed_facts": observed_facts,
            "evidence_frames": evidence_frames,
            "clip": clip_obj,
            "provenance": {
                "timestamp_source": timestamp_source,
                "recording_datetime_present": bool(
                    recording.get("recording_datetime")),
                "container_pts": container_pts is not None,
                "dense_seconds": round(dense_seconds, 3),
                "reason": reason,
            },
        }
        events.append(event)

    partial = bool(uncovered)
    status = "PARTIAL" if partial else "COMPLETED"
    async with get_db(db_path) as db:
        await db.execute(
            "DELETE FROM temporal_events WHERE recording_id = ? "
            "AND NOT (end_seconds < ? OR start_seconds > ?)",
            (recording_id, req_start, req_end))
        for ev in events:
            await db.execute(
                """INSERT OR REPLACE INTO temporal_events(
                    event_id, recording_id, category, start_seconds, end_seconds,
                    timestamp_seconds, timestamp_source, confidence,
                    single_frame_candidate, evidence_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (ev["event_id"], recording_id, ev["category"],
                 ev["start_seconds"], ev["end_seconds"], ev["timestamp_seconds"],
                 ev["timestamp_source"], ev["confidence"],
                 1 if ev["single_frame_candidate"] else 0,
                 json.dumps(ev), _now()))
        await db.execute(
            """INSERT INTO temporal_refine_log(
                refine_id, recording_id, start_seconds, end_seconds, reason,
                dense_seconds, status, uncovered_spans, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), recording_id, req_start, req_end, reason,
             round(dense_seconds, 3), status, json.dumps(uncovered), _now()))
        # Temporal rows feed incident queries: bump updated_at so cached
        # investigation responses invalidate.
        await db.execute(
            "UPDATE recordings SET updated_at = ? WHERE recording_id = ?",
            (_now(), recording_id))
        await db.commit()

    return {
        "events": events,
        "partial": partial,
        "status": status,
        "uncovered_spans": uncovered,
        "timestamp_source": timestamp_source,
        "cost": {
            "dense_seconds": round(dense_seconds, 3),
            "dense_budget_s": budget,
            "recording_dense_spend_s": round(spent + dense_seconds, 3),
            "max_dense_per_recording": cfg["max_dense_per_recording"],
        },
    }


def _select_evidence_frames(observations: list[dict], start: float, end: float,
                            timestamp_source: str) -> list[dict]:
    frames: list[dict] = []
    during = [o for o in observations if start <= o["video_timestamp_seconds"] <= end]
    if during:
        best = max(during, key=lambda o: o.get("detection_score", 0))
        frames.append({"frame_path": best["source_frame_path"],
                       "timestamp_seconds": best["video_timestamp_seconds"],
                       "role": "during", "timestamp_source": timestamp_source,
                       "pts_seconds": None})
    before = [o for o in observations if o["video_timestamp_seconds"] < start]
    if before:
        nearest = max(before, key=lambda o: o["video_timestamp_seconds"])
        frames.append({"frame_path": nearest["source_frame_path"],
                       "timestamp_seconds": nearest["video_timestamp_seconds"],
                       "role": "before", "timestamp_source": timestamp_source,
                       "pts_seconds": None})
    after = [o for o in observations if o["video_timestamp_seconds"] > end]
    if after:
        nearest = min(after, key=lambda o: o["video_timestamp_seconds"])
        frames.append({"frame_path": nearest["source_frame_path"],
                       "timestamp_seconds": nearest["video_timestamp_seconds"],
                       "role": "after", "timestamp_source": timestamp_source,
                       "pts_seconds": None})
    return frames


async def get_temporal_events(
    recording_id: str,
    db_path: str,
    start: Optional[float] = None,
    end: Optional[float] = None,
    min_confidence: Optional[float] = None,
    include_single_frame: bool = True,
) -> list[dict]:
    """Query temporal events with span-overlap time filtering.

    A time bound matches when the event SPAN overlaps [start, end] — the
    representative midpoint is never used for filtering.
    """
    import aiosqlite
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        query = ("SELECT evidence_json, start_seconds, end_seconds, confidence,"
                 " single_frame_candidate FROM temporal_events "
                 "WHERE recording_id = ?")
        params: list = [recording_id]
        if start is not None:
            query += " AND end_seconds >= ?"
            params.append(start)
        if end is not None:
            query += " AND start_seconds <= ?"
            params.append(end)
        if min_confidence is not None:
            query += " AND confidence >= ?"
            params.append(min_confidence)
        if not include_single_frame:
            query += " AND single_frame_candidate = 0"
        query += " ORDER BY start_seconds"
        rows = await (await db.execute(query, tuple(params))).fetchall()
        events = []
        for r in rows:
            try:
                events.append(json.loads(r["evidence_json"]))
            except Exception:
                events.append({
                    "recording_id": recording_id,
                    "start_seconds": r["start_seconds"],
                    "end_seconds": r["end_seconds"],
                    "confidence": r["confidence"],
                })
        return events
