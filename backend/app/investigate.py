"""
Investigation query engine for RealityTrace.

Retrieves stored events by relevance to a natural-language query,
assembles a chronological timeline, and returns ranked results.

Guarantees:
- Retrieval and timestamps come from stored observations; the LLM is NOT used here.
- The narrative is assembled from retrieved evidence text only.
- No events are fabricated; if data is absent, evidence_gaps is populated.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from .database import get_db
from .models import (
    CrossCameraLink,
    EvidenceFrame,
    Event,
    InvestigationResult,
)

logger = logging.getLogger(__name__)

MIN_EVENT_SCORE = 0.25
MAX_EVENTS_RETURNED = 20


async def investigate(
    query: str,
    db_path: str,
    recording_ids: Optional[list[str]] = None,
    time_start_seconds: Optional[float] = None,
    time_end_seconds: Optional[float] = None,
) -> InvestigationResult:
    """
    Search events for an investigative query.
    Returns a chronological, evidence-backed InvestigationResult.
    """
    investigation_id = str(uuid.uuid4())
    query_terms = _tokenize(query)
    temporal_filter = _parse_temporal_query(query)

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Resolve eligible recordings
        if recording_ids:
            placeholders = ",".join("?" * len(recording_ids))
            rec_rows = await (await db.execute(
                f"""SELECT recording_id, original_filename, recording_datetime,
                           recording_timezone, camera_name, camera_location
                    FROM recordings
                    WHERE recording_id IN ({placeholders})
                      AND processing_status IN ('INDEXED', 'PARTIAL')""",
                recording_ids,
            )).fetchall()
        else:
            rec_rows = await (await db.execute(
                """SELECT recording_id, original_filename, recording_datetime,
                          recording_timezone, camera_name, camera_location
                   FROM recordings
                   WHERE processing_status IN ('INDEXED', 'PARTIAL')
                   ORDER BY upload_timestamp""",
            )).fetchall()

        if not rec_rows:
            return InvestigationResult(
                investigation_id=investigation_id,
                query=query,
                events=[],
                narrative="No indexed recordings available to investigate.",
                timestamp_ordering_reliable=False,
                ordering_explanation="No indexed recordings found.",
                searched_recordings=0,
                total_events_found=0,
                evidence_gaps=["No recordings have been processed yet."],
                uncertainty_summary="",
            )

        rec_meta = {
            r["recording_id"]: dict(r) for r in rec_rows
        }
        eligible_ids = list(rec_meta.keys())
        placeholders = ",".join("?" * len(eligible_ids))

        # Load events
        event_rows = await (await db.execute(
            f"""SELECT e.*, r.original_filename, r.recording_datetime as rec_dt,
                       r.recording_timezone as rec_tz,
                       r.camera_name as cam_name, r.camera_location as cam_loc
                FROM events e
                JOIN recordings r ON e.recording_id = r.recording_id
                WHERE e.recording_id IN ({placeholders})
                ORDER BY e.timestamp_seconds""",
            eligible_ids,
        )).fetchall()

        # Load cross-camera links
        link_rows = await (await db.execute(
            """SELECT l.*, e.recording_id as target_rec_id
               FROM event_links l
               JOIN events e ON l.target_event_id = e.event_id""",
        )).fetchall()

    if not event_rows:
        gaps = [
            f"Recording '{rec_meta[rid]['original_filename']}' has been processed but "
            f"no events were extracted — the video may contain no identifiable objects."
            for rid in eligible_ids
        ]
        return InvestigationResult(
            investigation_id=investigation_id,
            query=query,
            events=[],
            narrative="No events have been extracted from the available recordings.",
            timestamp_ordering_reliable=False,
            ordering_explanation="No events to order.",
            searched_recordings=len(eligible_ids),
            total_events_found=0,
            evidence_gaps=gaps or ["No events found."],
            uncertainty_summary="",
        )

    # Build link index
    links_by_source: dict[str, list[dict]] = {}
    for lnk in link_rows:
        src = lnk["source_event_id"]
        links_by_source.setdefault(src, []).append(dict(lnk))

    # Score and filter events
    scored_events: list[tuple[float, dict]] = []
    for row in event_rows:
        ev_dict = dict(row)
        ev_dict["object_descriptions"] = _load_json(ev_dict.get("object_descriptions", "[]"), [])
        ev_dict["observed_facts"] = _load_json(ev_dict.get("observed_facts", "[]"), [])
        ev_dict["inferred_claims"] = _load_json(ev_dict.get("inferred_claims", "[]"), [])
        ev_dict["uncertainty_notes"] = _load_json(ev_dict.get("uncertainty_notes", "[]"), [])
        ev_dict["evidence_frames"] = _load_json(ev_dict.get("evidence_frames", "[]"), [])

        # Apply time filter
        ts = ev_dict["timestamp_seconds"]
        eff_start = time_start_seconds if time_start_seconds is not None else temporal_filter.get("start")
        eff_end = time_end_seconds if time_end_seconds is not None else temporal_filter.get("end")
        if eff_start is not None and ts < eff_start:
            continue
        if eff_end is not None and ts > eff_end:
            continue

        score = _score_event(query_terms, ev_dict)
        if score >= MIN_EVENT_SCORE:
            scored_events.append((score, ev_dict))

    if not scored_events:
        return InvestigationResult(
            investigation_id=investigation_id,
            query=query,
            events=[],
            narrative=(
                f"No events matching '{query}' were found across "
                f"{len(eligible_ids)} indexed recording(s)."
            ),
            timestamp_ordering_reliable=False,
            ordering_explanation="No matching events.",
            searched_recordings=len(eligible_ids),
            total_events_found=0,
            evidence_gaps=[f"No events matched the query '{query}'."],
            uncertainty_summary="",
        )

    # Sort chronologically — use the aliased rec_dt column
    all_have_ts = all(r.get("rec_dt") is not None for _, r in scored_events)
    any_have_ts = any(r.get("rec_dt") is not None for _, r in scored_events)

    if all_have_ts:
        scored_events.sort(key=lambda x: _sort_key(x[1]))
        ordering_explanation = "Events ordered chronologically by absolute recording timestamp."
        ts_reliable = True
    elif any_have_ts:
        with_ts = [(s, e) for s, e in scored_events if e.get("rec_dt")]
        without_ts = [(s, e) for s, e in scored_events if not e.get("rec_dt")]
        with_ts.sort(key=lambda x: _sort_key(x[1]))
        without_ts.sort(key=lambda x: x[0], reverse=True)
        scored_events = with_ts + without_ts
        ordering_explanation = (
            "Events from recordings with timestamps are ordered chronologically. "
            "Events from recordings without timestamps are ordered by relevance."
        )
        ts_reliable = False
    else:
        scored_events.sort(key=lambda x: x[1]["timestamp_seconds"])
        ordering_explanation = (
            "No absolute timestamps available. Events ordered by relative video position."
        )
        ts_reliable = False

    # Cap results
    total_before_cap = len(scored_events)
    scored_events = scored_events[:MAX_EVENTS_RETURNED]

    # Convert to Event models
    events: list[Event] = []
    all_uncertainty_notes: list[str] = []
    for _score, ev_dict in scored_events:
        event_links = links_by_source.get(ev_dict["event_id"], [])
        cc_links = [
            CrossCameraLink(
                target_event_id=lnk["target_event_id"],
                target_recording_id=lnk["target_rec_id"],
                target_camera_name=rec_meta.get(lnk["target_rec_id"], {}).get("camera_name"),
                confidence=lnk["confidence"],
                link_type=lnk["link_type"],
                explanation=lnk["explanation"],
            )
            for lnk in event_links
        ]

        evidence_frames = [
            EvidenceFrame(
                frame_path=f["frame_path"],
                timestamp_seconds=f["timestamp_seconds"],
                role=f.get("role", "during"),
            )
            for f in ev_dict["evidence_frames"]
        ]

        rec_dt = ev_dict.get("rec_dt") or ev_dict.get("recording_datetime")
        rec_tz = ev_dict.get("rec_tz") or ev_dict.get("recording_timezone")
        cam_name = ev_dict.get("cam_name") or ev_dict.get("camera_name")

        event = Event(
            event_id=ev_dict["event_id"],
            recording_id=ev_dict["recording_id"],
            camera_id=ev_dict.get("recording_id"),
            camera_name=cam_name,
            camera_location=ev_dict.get("cam_loc") or ev_dict.get("camera_location"),
            timestamp_seconds=ev_dict["timestamp_seconds"],
            timestamp_absolute=ev_dict.get("timestamp_absolute"),
            has_reliable_timestamp=bool(ev_dict.get("has_reliable_timestamp")),
            event_type=ev_dict["event_type"],
            actor_description=ev_dict.get("actor_description"),
            object_descriptions=ev_dict["object_descriptions"],
            action_description=ev_dict["action_description"],
            scene_description=ev_dict.get("scene_description"),
            evidence_frames=evidence_frames,
            confidence=ev_dict["confidence"],
            observed_facts=ev_dict["observed_facts"],
            inferred_claims=ev_dict["inferred_claims"],
            uncertainty_notes=ev_dict["uncertainty_notes"],
            recording_filename=ev_dict.get("original_filename", ""),
            cross_camera_links=cc_links,
        )
        events.append(event)
        all_uncertainty_notes.extend(ev_dict["uncertainty_notes"])

    # Identify evidence gaps
    evidence_gaps = _identify_evidence_gaps(events, eligible_ids, rec_meta)

    # Build narrative from retrieved evidence only
    narrative = _build_narrative(query, events, ts_reliable)

    uncertainty_summary = "; ".join(sorted(set(all_uncertainty_notes)))[:500] if all_uncertainty_notes else ""

    return InvestigationResult(
        investigation_id=investigation_id,
        query=query,
        events=events,
        narrative=narrative,
        timestamp_ordering_reliable=ts_reliable,
        ordering_explanation=ordering_explanation,
        searched_recordings=len(eligible_ids),
        total_events_found=total_before_cap,
        evidence_gaps=evidence_gaps,
        uncertainty_summary=uncertainty_summary,
    )


def _load_json(value, default):
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _tokenize(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", query.lower())
    stopwords = {
        "where", "when", "did", "i", "last", "see", "my", "the", "a", "an",
        "find", "was", "is", "seen", "more", "recently", "than", "every",
        "video", "appears", "are", "there", "any", "what", "happened",
        "before", "after", "between", "show", "trace", "across", "camera",
        "cameras", "events", "and", "or", "in", "on", "at", "to", "of",
    }
    return [w for w in words if w not in stopwords]


def _parse_temporal_query(query: str) -> dict:
    """Extract time constraints like 'between 10:00 and 10:15' from query."""
    result: dict = {}
    pattern = r"between\s+(\d{1,2}):(\d{2})\s+and\s+(\d{1,2}):(\d{2})"
    m = re.search(pattern, query.lower())
    if m:
        start_m, start_s = int(m.group(1)), int(m.group(2))
        end_m, end_s = int(m.group(3)), int(m.group(4))
        result["start"] = start_m * 60 + start_s
        result["end"] = end_m * 60 + end_s
    return result


def _score_event(terms: list[str], ev: dict) -> float:
    """Score an event's relevance to the query terms."""
    if not terms:
        return 1.0

    searchable = " ".join([
        ev.get("action_description", ""),
        ev.get("scene_description", "") or "",
        ev.get("event_type", "").replace("_", " "),
        " ".join(ev.get("object_descriptions", [])),
        " ".join(ev.get("observed_facts", [])),
    ]).lower()

    hits = sum(1 for t in terms if t in searchable)
    # Require at least one keyword hit — confidence alone doesn't justify a result
    if hits == 0:
        return 0.0
    score = (hits / len(terms)) * 0.7 + ev.get("confidence", 0.5) * 0.3
    return min(score, 1.0)


def _sort_key(ev: dict) -> float:
    rec_dt = ev.get("rec_dt") or ev.get("recording_datetime")
    if rec_dt:
        try:
            dt = datetime.fromisoformat(rec_dt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp() + ev["timestamp_seconds"]
        except Exception:
            pass
    return ev["timestamp_seconds"]


def _identify_evidence_gaps(
    events: list[Event],
    eligible_ids: list[str],
    rec_meta: dict,
) -> list[str]:
    """Report recordings that contributed no events to the result."""
    covered = {e.recording_id for e in events}
    gaps: list[str] = []
    for rid in eligible_ids:
        if rid not in covered:
            name = rec_meta.get(rid, {}).get("original_filename", rid)
            gaps.append(f"No matching events found in recording '{name}'.")
    return gaps


def _build_narrative(query: str, events: list[Event], ts_reliable: bool) -> str:
    """
    Build a brief narrative from retrieved evidence.
    Only uses data from the events — never invents facts.
    """
    if not events:
        return f"No evidence found relevant to: {query}"

    parts = [f"Investigation query: \"{query}\"\n"]
    parts.append(f"Found {len(events)} relevant event(s):\n")

    for i, ev in enumerate(events[:10], 1):
        ts = _format_ts(ev.timestamp_seconds)
        cam = f"[{ev.camera_name}] " if ev.camera_name else ""
        ts_label = ev.timestamp_absolute[:19].replace("T", " ") if ev.timestamp_absolute else f"video @{ts}"
        parts.append(f"{i}. {cam}{ts_label} — {ev.action_description} (confidence: {ev.confidence:.0%})")

    if not ts_reliable:
        parts.append(
            "\nNote: Absolute timestamps are unavailable for some recordings. "
            "Chronological ordering may be approximate."
        )

    return "\n".join(parts)


def _format_ts(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
