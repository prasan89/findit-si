"""
Event extraction pipeline: groups observations into structured events.

Strategy:
1. Load all observations for a recording, sorted by timestamp.
2. Group observations within EVENT_WINDOW_SECONDS into candidate events.
3. For each group, build an Event with:
   - observed_facts (things explicitly stated by the model)
   - inferred_claims (contextual deductions — clearly labeled)
   - uncertainty_notes (low-confidence detections, partial data, etc.)
   - evidence_frames: before / during / after frames
4. Persist events to the DB.

This module does NOT call the vision model directly; it works from stored
observations so it can be re-run without extra API calls.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from .database import get_db

logger = logging.getLogger(__name__)

EVENT_WINDOW_SECONDS = 8.0
BEFORE_AFTER_OFFSET = 5.0  # seconds before/after the event to pull context frames
MIN_CONFIDENCE = 0.2
# Phase 4: an event must be confirmed by observations from at least this many
# distinct frame timestamps. Single-frame groups are dropped — one frame
# resembling a scene is not sufficient evidence that an event occurred.
MIN_CONFIRMING_FRAMES = 2

# Subject classes shared by category inference and query relevance.
# Person words are strong, person-denoting terms only. Weak words like
# "individual"/"figure" also describe animals and objects (the model wrote
# "multiple individuals" about ants), and substring matching on them labeled
# a bird event `person_detected`.
PERSON_SUBJECTS = frozenset({
    "person", "persons", "people", "human", "humans",
    "man", "men", "woman", "women",
})
VEHICLE_SUBJECTS = frozenset({
    "car", "cars", "vehicle", "vehicles", "truck", "trucks",
    "van", "vans", "motorcycle", "motorcycles", "bike", "bikes",
})
OBJECT_SUBJECTS = frozenset({
    "bag", "bags", "backpack", "backpacks", "suitcase", "suitcases",
    "luggage", "package", "packages",
})
ENTRY_SUBJECTS = frozenset({
    "door", "doors", "gate", "gates", "entrance", "entrances",
    "exit", "exits",
})

_PATTERN_CACHE: dict = {}


def contains_word(text: str, words: frozenset) -> bool:
    """Word-boundary match of any word in `words` against `text`.

    Unlike substring search, "individuals" does not match "individual",
    and "man" does not match "mannequin", "performance", or "woman".
    """
    if not text:
        return False
    pat = _PATTERN_CACHE.get(words)
    if pat is None:
        pat = re.compile(r"\b(?:" + "|".join(sorted(words)) + r")\b")
        _PATTERN_CACHE[words] = pat
    return pat.search(text.lower()) is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def extract_events_for_recording(recording_id: str, db_path: str) -> int:
    """
    Extract and persist events for a recording. Returns the number of events stored.
    Idempotent: deletes existing events for the recording before re-extracting.
    """
    async with get_db(db_path) as db:
        # Load recording metadata
        import aiosqlite
        db.row_factory = aiosqlite.Row
        rec_row = await (await db.execute(
            """SELECT recording_id, original_filename, recording_datetime,
                      recording_timezone, camera_name, camera_location
               FROM recordings WHERE recording_id = ?""",
            (recording_id,),
        )).fetchone()
        if not rec_row:
            logger.warning("extract_events: recording %s not found", recording_id)
            return 0

        # Load all observations ordered by time
        obs_rows = await (await db.execute(
            """SELECT observation_id, video_timestamp_seconds, object_label,
                      object_attributes, scene_description, source_frame_path,
                      detection_score, recording_datetime, recording_timezone
               FROM observations
               WHERE recording_id = ?
               ORDER BY video_timestamp_seconds""",
            (recording_id,),
        )).fetchall()

        if not obs_rows:
            return 0

        observations = [dict(r) for r in obs_rows]

        # Delete stale events for this recording
        await db.execute("DELETE FROM events WHERE recording_id = ?", (recording_id,))
        await db.commit()

    # Group observations into temporal windows
    groups = _group_observations_into_windows(observations)

    # Build all frame paths for context lookup
    all_frame_paths_by_ts: dict[float, str] = {
        obs["video_timestamp_seconds"]: obs["source_frame_path"]
        for obs in observations
    }
    sorted_timestamps = sorted(all_frame_paths_by_ts.keys())

    events = []
    for group in groups:
        event = _build_event(
            group=group,
            recording_id=recording_id,
            recording_filename=rec_row["original_filename"],
            recording_datetime=rec_row["recording_datetime"],
            recording_timezone=rec_row["recording_timezone"],
            camera_name=rec_row["camera_name"],
            camera_location=rec_row["camera_location"],
            all_frame_paths_by_ts=all_frame_paths_by_ts,
            sorted_timestamps=sorted_timestamps,
        )
        if event:
            events.append(event)

    # Persist events (start/end_seconds columns added by the Phase 4
    # migration; fall back to the legacy column set on older databases).
    async with get_db(db_path) as db:
        for ev in events:
            try:
                await db.execute(
                    """INSERT OR REPLACE INTO events(
                        event_id, recording_id, timestamp_seconds, timestamp_absolute,
                        has_reliable_timestamp, event_type, actor_description,
                        object_descriptions, action_description, scene_description,
                        evidence_frames, confidence, observed_facts, inferred_claims,
                        uncertainty_notes, source_observation_ids, created_at,
                        start_seconds, end_seconds
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        ev["event_id"],
                        ev["recording_id"],
                        ev["timestamp_seconds"],
                        ev.get("timestamp_absolute"),
                        1 if ev.get("has_reliable_timestamp") else 0,
                        ev["event_type"],
                        ev.get("actor_description"),
                        json.dumps(ev["object_descriptions"]),
                        ev["action_description"],
                        ev.get("scene_description"),
                        json.dumps(ev["evidence_frames"]),
                        ev["confidence"],
                        json.dumps(ev["observed_facts"]),
                        json.dumps(ev["inferred_claims"]),
                        json.dumps(ev["uncertainty_notes"]),
                        json.dumps(ev["source_observation_ids"]),
                        _now(),
                        ev.get("start_seconds"),
                        ev.get("end_seconds"),
                    ),
                )
            except Exception:
                await db.execute(
                    """INSERT OR REPLACE INTO events(
                        event_id, recording_id, timestamp_seconds, timestamp_absolute,
                        has_reliable_timestamp, event_type, actor_description,
                        object_descriptions, action_description, scene_description,
                        evidence_frames, confidence, observed_facts, inferred_claims,
                        uncertainty_notes, source_observation_ids, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        ev["event_id"],
                        ev["recording_id"],
                        ev["timestamp_seconds"],
                        ev.get("timestamp_absolute"),
                        1 if ev.get("has_reliable_timestamp") else 0,
                        ev["event_type"],
                        ev.get("actor_description"),
                        json.dumps(ev["object_descriptions"]),
                        ev["action_description"],
                        ev.get("scene_description"),
                        json.dumps(ev["evidence_frames"]),
                        ev["confidence"],
                        json.dumps(ev["observed_facts"]),
                        json.dumps(ev["inferred_claims"]),
                        json.dumps(ev["uncertainty_notes"]),
                        json.dumps(ev["source_observation_ids"]),
                        _now(),
                    ),
                )
        await db.commit()

    logger.info(
        "Extracted %d events for recording %s from %d observations",
        len(events), recording_id, len(observations),
    )
    return len(events)


def _group_observations_into_windows(
    observations: list[dict],
) -> list[list[dict]]:
    """Group consecutive observations within EVENT_WINDOW_SECONDS into windows."""
    if not observations:
        return []

    groups: list[list[dict]] = []
    current: list[dict] = [observations[0]]

    for obs in observations[1:]:
        gap = obs["video_timestamp_seconds"] - current[-1]["video_timestamp_seconds"]
        if gap <= EVENT_WINDOW_SECONDS:
            current.append(obs)
        else:
            groups.append(current)
            current = [obs]
    groups.append(current)
    return groups


def _build_event(
    group: list[dict],
    recording_id: str,
    recording_filename: str,
    recording_datetime: Optional[str],
    recording_timezone: Optional[str],
    camera_name: Optional[str],
    camera_location: Optional[str],
    all_frame_paths_by_ts: dict[float, str],
    sorted_timestamps: list[float],
) -> Optional[dict]:
    """Convert a group of observations into a single Event dict."""
    if not group:
        return None

    # Phase 4: multi-frame confirmation. Distinct frame timestamps (not raw
    # observation rows — one frame can yield several objects).
    distinct_frames = {o["video_timestamp_seconds"] for o in group}
    if len(distinct_frames) < MIN_CONFIRMING_FRAMES:
        logger.debug(
            "Dropping single-frame group at t=%.1fs (%d obs): unconfirmed",
            group[0]["video_timestamp_seconds"], len(group),
        )
        return None

    # Representative timestamp = middle of group; span = group bounds.
    # Phase 4: the span is persisted (start/end_seconds) instead of discarded.
    start_ts = group[0]["video_timestamp_seconds"]
    end_ts = group[-1]["video_timestamp_seconds"]
    mid_ts = (start_ts + end_ts) / 2.0

    # Collect unique objects
    object_labels: list[str] = []
    seen_labels: set[str] = set()
    scene_parts: list[str] = []
    total_confidence = 0.0

    for obs in group:
        label = obs["object_label"]
        if label and label not in seen_labels:
            seen_labels.add(label)
            attrs = {}
            if obs.get("object_attributes"):
                try:
                    attrs = json.loads(obs["object_attributes"])
                except Exception:
                    pass
            desc = label
            if attrs.get("color"):
                desc = f"{attrs['color']} {desc}"
            if attrs.get("distinctive_details"):
                desc += f" ({attrs['distinctive_details']})"
            object_labels.append(desc)

        if obs.get("scene_description") and obs["scene_description"] not in scene_parts:
            scene_parts.append(obs["scene_description"])

        total_confidence += obs.get("detection_score", 0.5)

    avg_confidence = total_confidence / len(group) if group else 0.0

    if avg_confidence < MIN_CONFIDENCE:
        return None

    # Determine event_type heuristically
    event_type = _infer_event_type(object_labels, scene_parts)

    # Build observed facts — only what the model actually said
    observed_facts: list[str] = []
    for label in object_labels:
        observed_facts.append(f"Observed: {label}")
    if scene_parts:
        observed_facts.append(f"Scene: {scene_parts[0][:200]}")

    # Build inferred claims — labeled as inferences, not facts
    inferred_claims: list[str] = []
    if len(object_labels) > 1:
        inferred_claims.append(
            f"Multiple objects present in proximity: {', '.join(object_labels[:3])}"
        )

    # Uncertainty notes
    uncertainty_notes: list[str] = []
    low_conf_obs = [o for o in group if o.get("detection_score", 0.5) < 0.5]
    if low_conf_obs:
        uncertainty_notes.append(
            f"{len(low_conf_obs)} detection(s) have confidence below 0.5 — verify visually"
        )
    if not recording_datetime:
        uncertainty_notes.append("No absolute recording timestamp — relative timestamps only")

    # Action description: prefer model's scene_summary (contains activity context from CCTV prompt)
    # Fall back to template only when the model returned no scene description
    action_description = scene_parts[0][:300] if scene_parts else _build_action_description(object_labels, event_type)

    # Timestamp absolute
    timestamp_absolute = None
    has_reliable_timestamp = False
    if recording_datetime:
        try:
            dt = datetime.fromisoformat(recording_datetime)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            abs_dt = datetime.fromtimestamp(dt.timestamp() + mid_ts, tz=timezone.utc)
            timestamp_absolute = abs_dt.isoformat()
            has_reliable_timestamp = True
        except Exception:
            uncertainty_notes.append("Could not compute absolute timestamp from recording_datetime")

    # Build evidence frames: before / during / after
    evidence_frames = _select_evidence_frames(
        group=group,
        mid_ts=mid_ts,
        all_frame_paths_by_ts=all_frame_paths_by_ts,
        sorted_timestamps=sorted_timestamps,
    )

    return {
        "event_id": str(uuid.uuid4()),
        "recording_id": recording_id,
        "recording_filename": recording_filename,
        "camera_name": camera_name,
        "camera_location": camera_location,
        "timestamp_seconds": mid_ts,
        "start_seconds": start_ts,
        "end_seconds": end_ts,
        "n_confirming_frames": len(distinct_frames),
        "timestamp_absolute": timestamp_absolute,
        "has_reliable_timestamp": has_reliable_timestamp,
        "event_type": event_type,
        "actor_description": None,
        "object_descriptions": object_labels,
        "action_description": action_description,
        "scene_description": scene_parts[0] if scene_parts else None,
        "evidence_frames": evidence_frames,
        "confidence": round(avg_confidence, 3),
        "observed_facts": observed_facts,
        "inferred_claims": inferred_claims,
        "uncertainty_notes": uncertainty_notes,
        "source_observation_ids": [o["observation_id"] for o in group],
    }


def _infer_event_type(object_labels: list[str], scene_parts: list[str]) -> str:
    """Heuristic event type from labels and scene context.

    Word-boundary matching only: unknown or ambiguous subjects fall through
    to `scene_change` rather than being forced into a person/vehicle claim.
    """
    combined = " ".join(object_labels + scene_parts)

    if contains_word(combined, PERSON_SUBJECTS):
        return "person_detected"
    if contains_word(combined, VEHICLE_SUBJECTS):
        return "vehicle_detected"
    if contains_word(combined, OBJECT_SUBJECTS):
        return "unattended_object"
    if contains_word(combined, ENTRY_SUBJECTS):
        return "entry_exit_detected"
    return "scene_change"


def _build_action_description(object_labels: list[str], event_type: str) -> str:
    """Build a concise, non-speculative action description."""
    if not object_labels:
        return "Scene activity detected"
    objects_str = ", ".join(object_labels[:3])
    if event_type == "person_detected":
        return f"Person-like figure detected: {objects_str}"
    if event_type == "vehicle_detected":
        return f"Vehicle detected: {objects_str}"
    if event_type == "unattended_object":
        return f"Object detected: {objects_str}"
    if event_type == "entry_exit_detected":
        return f"Entry/exit area activity: {objects_str}"
    return f"Detected: {objects_str}"


def _select_evidence_frames(
    group: list[dict],
    mid_ts: float,
    all_frame_paths_by_ts: dict[float, str],
    sorted_timestamps: list[float],
) -> list[dict]:
    """Select before, during, and after evidence frames."""
    frames = []

    # "During": best frame within the group (highest detection_score)
    best_during = max(group, key=lambda o: o.get("detection_score", 0))
    frames.append({
        "frame_path": best_during["source_frame_path"],
        "timestamp_seconds": best_during["video_timestamp_seconds"],
        "role": "during",
    })

    group_ts = {o["video_timestamp_seconds"] for o in group}
    start_ts = group[0]["video_timestamp_seconds"]
    end_ts = group[-1]["video_timestamp_seconds"]

    # "Before": nearest frame before start_ts - BEFORE_AFTER_OFFSET
    before_target = start_ts - BEFORE_AFTER_OFFSET
    before_candidates = [t for t in sorted_timestamps if t < start_ts and t not in group_ts]
    if before_candidates:
        best_before_ts = min(before_candidates, key=lambda t: abs(t - before_target))
        frames.append({
            "frame_path": all_frame_paths_by_ts[best_before_ts],
            "timestamp_seconds": best_before_ts,
            "role": "before",
        })

    # "After": nearest frame after end_ts + BEFORE_AFTER_OFFSET
    after_target = end_ts + BEFORE_AFTER_OFFSET
    after_candidates = [t for t in sorted_timestamps if t > end_ts and t not in group_ts]
    if after_candidates:
        best_after_ts = min(after_candidates, key=lambda t: abs(t - after_target))
        frames.append({
            "frame_path": all_frame_paths_by_ts[best_after_ts],
            "timestamp_seconds": best_after_ts,
            "role": "after",
        })

    return frames
