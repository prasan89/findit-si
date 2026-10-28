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
from .event_extractor import (
    BIRD_SUBJECTS, INSECT_HYPERNYMS, INSECT_LEMMA, INSECT_SUBJECTS,
    PERSON_SUBJECTS, VEHICLE_SUBJECTS, contains_word,
)
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
    temporal_filter = _parse_temporal_query(query)
    query_terms = _tokenize(query)
    if temporal_filter:
        # Phase 4: numeric tokens in a temporal query are time-phrase residue
        # ("00", "15"), not content — drop them so the time filter (plus any
        # remaining content terms) decides. With no terms left, scoring falls
        # through to the pure-filter path (_score_event([]) == 1.0).
        query_terms = [t for t in query_terms if not t.isdigit()]

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Resolve eligible recordings
        if recording_ids:
            placeholders = ",".join("?" * len(recording_ids))
            rec_rows = await (await db.execute(
                f"""SELECT recording_id, original_filename, recording_datetime,
                           recording_timezone, camera_name, camera_location,
                           video_duration_seconds
                    FROM recordings
                    WHERE recording_id IN ({placeholders})
                      AND processing_status IN ('INDEXED', 'PARTIAL')""",
                recording_ids,
            )).fetchall()
        else:
            rec_rows = await (await db.execute(
                """SELECT recording_id, original_filename, recording_datetime,
                          recording_timezone, camera_name, camera_location,
                          video_duration_seconds
                   FROM recordings
                   WHERE processing_status IN ('INDEXED', 'PARTIAL')
                   ORDER BY upload_timestamp""",
            )).fetchall()

        # Recordings that exist but cannot be searched (never indexed or
        # failed) are reported, never silently skipped.
        if recording_ids:
            inelig_rows = await (await db.execute(
                f"""SELECT original_filename, processing_status FROM recordings
                    WHERE recording_id IN ({placeholders})
                      AND processing_status NOT IN ('INDEXED', 'PARTIAL')""",
                recording_ids,
            )).fetchall()
        else:
            inelig_rows = await (await db.execute(
                """SELECT original_filename, processing_status FROM recordings
                   WHERE processing_status NOT IN ('INDEXED', 'PARTIAL')"""
            )).fetchall()
        ineligible = [(r["original_filename"], r["processing_status"])
                      for r in inelig_rows]

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
                evidence_gaps=["No recordings have been processed yet."]
                + _ineligible_notes(ineligible),
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

        # Load cross-camera links for the eligible recordings only
        # (Phase 4: previously scanned the whole table on every query).
        link_rows = await (await db.execute(
            f"""SELECT l.*, e.recording_id as target_rec_id
               FROM event_links l
               JOIN events e ON l.target_event_id = e.event_id
               JOIN events se ON l.source_event_id = se.event_id
               WHERE se.recording_id IN ({placeholders})""",
            eligible_ids,
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
    n_time_excluded = 0
    has_time_bounds = (
        time_start_seconds is not None or time_end_seconds is not None
        or "start" in temporal_filter or "end" in temporal_filter
    )
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
            n_time_excluded += 1
            continue
        if eff_end is not None and ts > eff_end:
            n_time_excluded += 1
            continue

        score = _score_event(query_terms, ev_dict)
        if score >= MIN_EVENT_SCORE:
            scored_events.append((score, ev_dict))

    if not scored_events:
        gaps = [f"No events matched the query '{query}'."]
        if has_time_bounds and n_time_excluded > 0:
            # Phase 4: say explicitly when the time window — not the content —
            # is what excluded everything.
            gaps.append(
                f"{n_time_excluded} event(s) exist outside the requested time "
                f"range and were excluded by the time filter."
            )
        gaps = gaps + _ineligible_notes(ineligible)
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
            evidence_gaps=gaps,
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

    # Phase 4: first/last selection follows the displayed ordering
    # (chronological when timestamps allow, relevance otherwise).
    select = temporal_filter.get("select")
    if select == "first" and scored_events:
        scored_events = scored_events[:1]
    elif select == "last" and scored_events:
        scored_events = scored_events[-1:]

    # Cap results
    total_before_cap = len(scored_events)
    scored_events = scored_events[:MAX_EVENTS_RETURNED]

    # Convert to Event models
    events: list[Event] = []
    all_uncertainty_notes: list[str] = []
    for _score, ev_dict in scored_events:
        # Phase 4: validate evidence references against the recording
        # (in-memory only — stored rows are untouched).
        rec_duration = rec_meta.get(ev_dict["recording_id"], {}).get("video_duration_seconds")
        frame_problems = validate_evidence_frames(ev_dict["evidence_frames"], rec_duration)
        if frame_problems:
            ev_dict["uncertainty_notes"] = ev_dict["uncertainty_notes"] + frame_problems
        category_problems = validate_category_support(ev_dict)
        if category_problems:
            ev_dict["uncertainty_notes"] = ev_dict["uncertainty_notes"] + category_problems

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
            start_seconds=ev_dict.get("start_seconds"),
            end_seconds=ev_dict.get("end_seconds"),
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
    evidence_gaps = evidence_gaps + _ineligible_notes(ineligible)

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
        # Phase 4: generic words that carry no retrieval signal. "all" in
        # particular matched "hallway"/"small" as a substring and broke
        # temporal queries (eval Q4); "first"/"last"/"event" are operators.
        "all", "event", "first",
        # "detected"/"detection" appear in every `*_detected` type label, so
        # as query tokens they match all categorized events regardless of
        # subject ("any dog detected?" scored 0.63 vs bird events with the
        # "dog" token matching nothing). Subject evidence must come from
        # the description instead.
        "detected", "detection", "detections",
    }
    return [w for w in words if w not in stopwords]


def _parse_temporal_query(query: str) -> dict:
    """Extract time constraints from a query.

    Supported (all video-relative seconds, consistent with stored
    timestamp_seconds):
    - 'between MM:SS and MM:SS' -> {"start", "end"}
    - 'before MM:SS'             -> {"end"}
    - 'after MM:SS'              -> {"start"}
    - 'first ...' / '... last'   -> {"select": "first"/"last"}

    Returns {} when no temporal operator is present. Clock-time (absolute)
    queries are NOT supported — MM:SS always means video-relative.
    """
    result: dict = {}
    q = query.lower()
    m = re.search(r"between\s+(\d{1,2}):(\d{2})\s+and\s+(\d{1,2}):(\d{2})", q)
    if m:
        result["start"] = int(m.group(1)) * 60 + int(m.group(2))
        result["end"] = int(m.group(3)) * 60 + int(m.group(4))
    else:
        m_before = re.search(r"before\s+(\d{1,2}):(\d{2})", q)
        if m_before:
            result["end"] = int(m_before.group(1)) * 60 + int(m_before.group(2))
        m_after = re.search(r"after\s+(\d{1,2}):(\d{2})", q)
        if m_after:
            result["start"] = int(m_after.group(1)) * 60 + int(m_after.group(2))
    if re.search(r"\bfirst\b", q):
        result["select"] = "first"
    elif re.search(r"\blast\b", q):
        result["select"] = "last"
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
    # Description text without the stored category: a stored type must never
    # justify itself (a mislabeled row would otherwise match its own label).
    descriptive = " ".join([
        ev.get("action_description", ""),
        ev.get("scene_description", "") or "",
        " ".join(ev.get("object_descriptions", [])),
        " ".join(ev.get("observed_facts", [])),
    ])

    # Subject gate: a query about people, vehicles, birds, or insects must
    # match subject evidence in the description — not a generic token like
    # "detected", which appears in every `*_detected` type label. Subject
    # synonyms cover each other ("human" matches an event describing a
    # "person"; "birds" matches "bird"; hypernym "insect" matches any insect
    # evidence. Insect hyponyms ("grub", "ants") instead require their own
    # lemma — ants are not grubs.
    query_person = any(t in PERSON_SUBJECTS for t in terms)
    query_vehicle = any(t in VEHICLE_SUBJECTS for t in terms)
    query_bird = any(t in BIRD_SUBJECTS for t in terms)
    query_insect = any(t in INSECT_HYPERNYMS for t in terms)
    if query_person and not contains_word(descriptive, PERSON_SUBJECTS):
        return 0.0
    if query_vehicle and not contains_word(descriptive, VEHICLE_SUBJECTS):
        return 0.0
    if query_bird and not contains_word(descriptive, BIRD_SUBJECTS):
        return 0.0
    if query_insect and not contains_word(descriptive, INSECT_SUBJECTS):
        return 0.0

    hits = 0
    for t in terms:
        if t in PERSON_SUBJECTS:
            # Covered by the gate above: any person word satisfies it.
            hits += 1
        elif t in VEHICLE_SUBJECTS:
            hits += 1
        elif t in BIRD_SUBJECTS:
            hits += 1
        elif t in INSECT_HYPERNYMS:
            hits += 1
        elif t in INSECT_SUBJECTS:
            forms = frozenset({t, INSECT_LEMMA[t]})
            if contains_word(descriptive, forms):
                hits += 1
        elif t in searchable:
            hits += 1
    # Require at least one keyword hit — confidence alone doesn't justify a result
    if hits == 0:
        return 0.0
    score = (hits / len(terms)) * 0.7 + ev.get("confidence", 0.5) * 0.3
    return min(score, 1.0)


def validate_category_support(ev_dict: dict) -> list[str]:
    """Flag a stored category the event description does not support.

    Defense-in-depth for rows labeled before the inference fix (or by a
    confused model): the row is still returned when relevant, but the
    contradiction is surfaced instead of presented as confirmed. The stored
    confidence score is never altered here.
    """
    text = " ".join([
        ev_dict.get("action_description", ""),
        ev_dict.get("scene_description", "") or "",
        " ".join(ev_dict.get("object_descriptions", []) or []),
        " ".join(ev_dict.get("observed_facts", []) or []),
    ])
    event_type = ev_dict.get("event_type", "")
    if event_type == "person_detected" and not contains_word(text, PERSON_SUBJECTS):
        return ["Stored category 'person' is not supported by the event "
                "description — verify visually before treating this as a "
                "person detection"]
    if event_type == "vehicle_detected" and not contains_word(text, VEHICLE_SUBJECTS):
        return ["Stored category 'vehicle' is not supported by the event "
                "description — verify visually before treating this as a "
                "vehicle detection"]
    if event_type == "bird_detected" and not contains_word(text, BIRD_SUBJECTS):
        return ["Stored category 'bird' is not supported by the event "
                "description — verify visually before treating this as a "
                "bird detection"]
    return []


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


def validate_evidence_frames(
    frames: list[dict],
    video_duration_seconds: Optional[float] = None,
) -> list[str]:
    """Phase 4: check that evidence frames resolve to usable references.

    Returns a list of human-readable problems (empty == valid). Checks that
    each frame has a non-empty path and a finite timestamp >= 0, and — when
    the recording duration is known — that the timestamp falls within it.
    Validity here is resolvability only; it does NOT mean the frame supports
    any particular claim.
    """
    import math

    problems: list[str] = []
    for i, frame in enumerate(frames):
        if not isinstance(frame, dict):
            problems.append(f"evidence frame #{i} is malformed (not an object)")
            continue
        path = frame.get("frame_path")
        if not path or not isinstance(path, str):
            problems.append(f"evidence frame #{i} has no frame_path")
        ts = frame.get("timestamp_seconds")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
            problems.append(f"evidence frame #{i} has an invalid timestamp: {ts!r}")
            continue
        if ts < 0:
            problems.append(f"evidence frame #{i} has a negative timestamp ({ts}s)")
        elif video_duration_seconds is not None and ts > video_duration_seconds:
            problems.append(
                f"evidence frame #{i} timestamp ({ts:.1f}s) exceeds the "
                f"recording duration ({video_duration_seconds:.1f}s)"
            )
    return problems


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


def _ineligible_notes(ineligible: list[tuple]) -> list[str]:
    """Report recordings that exist but could not be searched at all."""
    notes = []
    for filename, status in ineligible:
        if status == "FAILED":
            notes.append(f"Recording '{filename}' failed processing and "
                         f"was not searched.")
        else:
            notes.append(f"Recording '{filename}' is not indexed yet "
                         f"(status {status}) and was not searched.")
    return notes


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
