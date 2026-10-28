"""
Cross-camera correlation engine for RealityTrace.

Strategy:
- Conservative: never claim identity or continuity without evidence.
- Uses temporal proximity, appearance descriptions, and event context.
- All matches are labeled "candidate" unless independently confirmed.
- Returns candidate links with explicit explanations.

Designed as a standalone module so it can be replaced by dedicated
vision/tracking models without changing the rest of the pipeline.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from .database import get_db

logger = logging.getLogger(__name__)

MAX_TEMPORAL_GAP_SECONDS = 300.0  # 5 minutes
MIN_CORRELATION_SCORE = 0.3
CONFIRMED_THRESHOLD = 0.85


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def correlate_across_cameras(db_path: str, recording_ids: Optional[list[str]] = None) -> int:
    """
    Find candidate cross-camera links between events and persist them.
    Returns the number of links created.

    Idempotent: deletes existing links for the affected events before re-correlating.
    """
    import aiosqlite

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Load events and recording metadata
        if recording_ids:
            placeholders = ",".join("?" * len(recording_ids))
            event_rows = await (await db.execute(
                f"""SELECT e.*, r.recording_datetime, r.recording_timezone,
                           r.camera_name, r.camera_location
                    FROM events e
                    JOIN recordings r ON e.recording_id = r.recording_id
                    WHERE e.recording_id IN ({placeholders})
                    ORDER BY e.timestamp_seconds""",
                recording_ids,
            )).fetchall()
        else:
            event_rows = await (await db.execute(
                """SELECT e.*, r.recording_datetime, r.recording_timezone,
                          r.camera_name, r.camera_location
                   FROM events e
                   JOIN recordings r ON e.recording_id = r.recording_id
                   ORDER BY e.timestamp_seconds""",
            )).fetchall()

        events = [dict(r) for r in event_rows]

    if len(events) < 2:
        return 0

    # Parse JSON fields
    for ev in events:
        ev["object_descriptions"] = _load_json(ev.get("object_descriptions", "[]"), [])
        ev["observed_facts"] = _load_json(ev.get("observed_facts", "[]"), [])

    # Compute absolute timestamps
    for ev in events:
        ev["abs_ts"] = _abs_timestamp(ev)

    links: list[dict] = []

    for i, ev_a in enumerate(events):
        for ev_b in events[i + 1:]:
            # Skip same recording
            if ev_a["recording_id"] == ev_b["recording_id"]:
                continue

            score, explanation = _correlation_score(ev_a, ev_b)
            if score >= MIN_CORRELATION_SCORE:
                link_type = "confirmed" if score >= CONFIRMED_THRESHOLD else "candidate"
                links.append({
                    "link_id": str(uuid.uuid4()),
                    "source_event_id": ev_a["event_id"],
                    "target_event_id": ev_b["event_id"],
                    "confidence": round(score, 3),
                    "link_type": link_type,
                    "explanation": explanation,
                })
                # Also create the reverse link
                links.append({
                    "link_id": str(uuid.uuid4()),
                    "source_event_id": ev_b["event_id"],
                    "target_event_id": ev_a["event_id"],
                    "confidence": round(score, 3),
                    "link_type": link_type,
                    "explanation": explanation,
                })

    if not links:
        return 0

    # Persist, clearing old links for involved events first
    source_ids = {lnk["source_event_id"] for lnk in links}

    async with get_db(db_path) as db:
        if source_ids:
            placeholders = ",".join("?" * len(source_ids))
            await db.execute(
                f"DELETE FROM event_links WHERE source_event_id IN ({placeholders})",
                list(source_ids),
            )
        for lnk in links:
            await db.execute(
                """INSERT OR REPLACE INTO event_links(
                    link_id, source_event_id, target_event_id,
                    confidence, link_type, explanation, created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    lnk["link_id"],
                    lnk["source_event_id"],
                    lnk["target_event_id"],
                    lnk["confidence"],
                    lnk["link_type"],
                    lnk["explanation"],
                    _now(),
                ),
            )
        await db.commit()

    logger.info("Created %d cross-camera links across %d events", len(links), len(events))
    return len(links)


def _load_json(value, default):
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _abs_timestamp(ev: dict) -> Optional[float]:
    """Return Unix timestamp of the event, or None if unavailable."""
    rec_dt = ev.get("recording_datetime")
    if rec_dt:
        try:
            dt = datetime.fromisoformat(rec_dt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp() + ev["timestamp_seconds"]
        except Exception:
            pass
    return None


def _temporal_score(ev_a: dict, ev_b: dict) -> tuple[float, str]:
    """Score based on temporal proximity between the two events."""
    abs_a = ev_a.get("abs_ts")
    abs_b = ev_b.get("abs_ts")

    if abs_a is not None and abs_b is not None:
        gap = abs(abs_a - abs_b)
        if gap > MAX_TEMPORAL_GAP_SECONDS:
            return 0.0, f"Too far apart in time ({gap:.0f}s gap)"
        score = max(0.0, 1.0 - gap / MAX_TEMPORAL_GAP_SECONDS)
        return score * 0.5, f"Temporal gap: {gap:.0f}s"

    # If one or both timestamps unavailable, can't determine temporal proximity
    return 0.1, "Temporal proximity unknown (recording timestamps unavailable)"


def _appearance_score(ev_a: dict, ev_b: dict) -> tuple[float, str]:
    """Score based on shared object description tokens."""
    descs_a = set(_tokenize_descriptions(ev_a.get("object_descriptions", [])))
    descs_b = set(_tokenize_descriptions(ev_b.get("object_descriptions", [])))

    if not descs_a or not descs_b:
        return 0.0, "No object descriptions to compare"

    intersection = descs_a & descs_b
    union = descs_a | descs_b
    if not union:
        return 0.0, "Empty description sets"

    jaccard = len(intersection) / len(union)
    shared_tokens = ", ".join(sorted(intersection)[:5])
    explanation = f"Shared appearance tokens: {shared_tokens}" if intersection else "No shared appearance tokens"
    return jaccard * 0.4, explanation


def _event_type_score(ev_a: dict, ev_b: dict) -> tuple[float, str]:
    """Small bonus for matching event types."""
    if ev_a.get("event_type") == ev_b.get("event_type"):
        return 0.1, f"Same event type: {ev_a['event_type']}"
    return 0.0, ""


def _correlation_score(ev_a: dict, ev_b: dict) -> tuple[float, str]:
    """
    Composite score. Important constraints:
    - Similar clothing/appearance does NOT prove same individual.
    - Score < CONFIRMED_THRESHOLD → "candidate" link, not confirmed.
    """
    t_score, t_expl = _temporal_score(ev_a, ev_b)
    if t_score == 0.0 and "Too far apart" in t_expl:
        return 0.0, t_expl

    a_score, a_expl = _appearance_score(ev_a, ev_b)
    et_score, et_expl = _event_type_score(ev_a, ev_b)

    total = t_score + a_score + et_score

    explanations = [p for p in [t_expl, a_expl, et_expl] if p]
    # Always add uncertainty notice — matching on appearance alone is not confirmation
    if a_score > 0.2:
        explanations.append(
            "Note: similar appearance does not confirm these are the same object/person"
        )

    return min(total, 1.0), "; ".join(explanations)


def _tokenize_descriptions(descriptions: list[str]) -> list[str]:
    """Return meaningful tokens from description strings."""
    tokens = []
    for desc in descriptions:
        for word in desc.lower().split():
            word = word.strip(".,;:()")
            if len(word) > 2:
                tokens.append(word)
    return tokens
