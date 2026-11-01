"""
Investigation query engine for RealityTrace.

Retrieves stored events by relevance to a natural-language query,
assembles a chronological timeline, and returns ranked results.

Guarantees:
- Retrieval and timestamps come from stored observations; the LLM is NOT used here.
- The narrative is assembled from retrieved evidence text only.
- No events are fabricated; if data is absent, evidence_gaps is populated.
"""
import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from .database import get_db
from .event_extractor import (
    ANIMAL_HYPERNYMS, ANIMAL_LEMMA, ANIMAL_SUBJECTS, BIRD_SUBJECTS,
    INSECT_HYPERNYMS, INSECT_LEMMA, INSECT_SUBJECTS, OBJECT_SUBJECTS,
    PERSON_SUBJECTS, VEHICLE_SUBJECTS, contains_word, term_matches,
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

# Flat set of all incident vocabulary words used by INCIDENT_CATEGORY_MAP
# (populated lazily after the map is defined so it stays DRY).
_INCIDENT_TERMS: Optional[frozenset] = None


def _incident_term_set() -> frozenset:
    """All vocabulary words that trigger temporal detection auto-trigger."""
    global _INCIDENT_TERMS
    if _INCIDENT_TERMS is None:
        words: set[str] = set()
        for vocab, _ in INCIDENT_CATEGORY_MAP:
            words |= vocab
        _INCIDENT_TERMS = frozenset(words)
    return _INCIDENT_TERMS


def _is_incident_query(terms: list[str]) -> bool:
    """True when the tokenized query contains at least one incident word."""
    return bool(set(terms) & _incident_term_set())


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

    # Incident-term queries also surface stored temporal incident
    # candidates (neutral vision text never matches "snatch"/"attack").
    # Loaded before the empty check so incident-only videos still match.
    incident_categories = _incident_categories_for_terms(query_terms)
    incident_dicts: list[dict] = []
    if incident_categories:
        inc_start = (time_start_seconds if time_start_seconds is not None
                     else temporal_filter.get("start"))
        inc_end = (time_end_seconds if time_end_seconds is not None
                   else temporal_filter.get("end"))
        # Auto-trigger temporal detection when incident vocabulary is present
        # and temporal_events has never been populated for these recordings.
        # This ensures investigations don't require a manual "Refine" step.
        await _maybe_auto_trigger_temporal(
            db_path, eligible_ids, query_terms, rec_meta)
        incident_dicts = await _load_incident_events(
            db_path, eligible_ids, incident_categories, rec_meta,
            inc_start, inc_end)

    if not event_rows and not incident_dicts:
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
        ev_dict["entities"] = _load_json(ev_dict.get("entities_json"), [])
        ev_dict["action_sequence"] = _load_json(ev_dict.get("action_sequence_json"), [])

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

    for incident_dict in incident_dicts:
        scored_events.append(
            (incident_dict["_incident_relevance"], incident_dict))

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
            summary=_build_summary(query, []),
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

        if ev_dict.get("_incident_preset"):
            relevance = ev_dict["_incident_relevance"]
            excerpts = ev_dict.get("matched_excerpts", [])
            primary_match = ev_dict.get("primary_match")
        else:
            relevance, excerpts, primary_match = score_relevance(
                query_terms, ev_dict)

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
            entities=ev_dict.get("entities", []),
            action_sequence=ev_dict.get("action_sequence", []),
            relevance_score=relevance,
            matched_excerpts=excerpts,
            primary_match=primary_match,
        )
        events.append(event)
        all_uncertainty_notes.extend(ev_dict["uncertainty_notes"])

    # Flag the single best-relevance event as the primary finding.
    if events:
        primary = max(
            events,
            key=lambda e: e.relevance_score
            if e.relevance_score is not None else 0.0,
        )
        primary.is_primary_finding = True

    # Identify evidence gaps
    evidence_gaps = _identify_evidence_gaps(events, eligible_ids, rec_meta)
    evidence_gaps = evidence_gaps + _ineligible_notes(ineligible)

    # Build narrative from retrieved evidence only
    narrative = _build_narrative(query, events, ts_reliable)
    summary = _build_summary(query, events)

    uncertainty_summary = "; ".join(sorted(set(all_uncertainty_notes)))[:500] if all_uncertainty_notes else ""

    return InvestigationResult(
        investigation_id=investigation_id,
        query=query,
        events=events,
        narrative=narrative,
        summary=summary,
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

    # Subject gate: a query about people, vehicles, birds, insects,
    # animals, or carried objects must match subject evidence in the
    # description — not a generic token like "detected", which appears in
    # every `*_detected` type label. Subject synonyms cover each other
    # ("human" matches an event describing a "person"; "birds" matches
    # "bird"; hypernym "insect"/"animal" matches any insect/animal
    # evidence). Hyponyms ("grub", "dog") instead require their own lemma
    # — ants are not grubs, cats are not dogs. Broad words are not
    # auto-synonyms across classes: "animal" matches animal evidence only,
    # never bird or person evidence (test_animal_does_not_match_people).
    query_person = any(t in PERSON_SUBJECTS for t in terms)
    query_vehicle = any(t in VEHICLE_SUBJECTS for t in terms)
    query_bird = any(t in BIRD_SUBJECTS for t in terms)
    query_insect = any(t in INSECT_HYPERNYMS for t in terms)
    query_animal = any(t in ANIMAL_HYPERNYMS for t in terms)
    query_object = any(t in OBJECT_SUBJECTS for t in terms)
    if query_person and not contains_word(descriptive, PERSON_SUBJECTS):
        return 0.0
    if query_vehicle and not contains_word(descriptive, VEHICLE_SUBJECTS):
        return 0.0
    if query_bird and not contains_word(descriptive, BIRD_SUBJECTS):
        return 0.0
    if query_insect and not contains_word(descriptive, INSECT_SUBJECTS):
        return 0.0
    if query_animal and not contains_word(descriptive, ANIMAL_SUBJECTS):
        return 0.0
    if query_object and not contains_word(descriptive, OBJECT_SUBJECTS):
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
        elif t in ANIMAL_HYPERNYMS:
            hits += 1
        elif t in ANIMAL_SUBJECTS:
            forms = frozenset({t, ANIMAL_LEMMA.get(t, t)})
            if contains_word(descriptive, forms):
                hits += 1
        elif t in OBJECT_SUBJECTS:
            # Covered by the gate above.
            hits += 1
        elif term_matches(searchable, t):
            hits += 1
    # Require at least one keyword hit — confidence alone doesn't justify a result
    if hits == 0:
        return 0.0
    score = (hits / len(terms)) * 0.7 + ev.get("confidence", 0.5) * 0.3
    return min(score, 1.0)


# Incident vocabulary mapped to temporal-detection categories. An incident
# term in the query surfaces stored temporal incident candidates for the
# eligible recordings — the vision model describes attacks neutrally
# ("person", "motorcycle"), so keyword text alone can never match them.
INCIDENT_CATEGORY_MAP: list[tuple[frozenset, frozenset]] = [
    # Theft vocabulary also surfaces violent-contact candidates: a
    # chain-snatch is a grab plus force, and neutral vision text never
    # states the object transfer — the correlator can only reach
    # possible-assault from approach/contact/departure stages. Both stay
    # observation-only candidates, never confirmed verdicts.
    (frozenset({"theft", "thief", "thieves", "steal", "stealing", "stole",
                "stolen", "snatch", "snatched", "snatching", "snatcher",
                "rob", "robbed", "robber", "robbery", "robberies",
                "burglar", "burglary", "loot", "looted", "pickpocket",
                "shoplift", "mugging", "mugged"}),
     frozenset({"possible-theft", "possible-assault"})),
    (frozenset({"attack", "attacked", "attacking", "attacker",
                "assault", "assaulted", "assaulting", "assailant",
                "fight", "fighting", "hit", "hitting", "beat", "beating",
                "threat", "threaten", "threatening", "harass",
                "stab", "stabbing", "shoot", "shooting", "shot",
                "violence", "violent"}),
     frozenset({"possible-assault", "confrontation"})),
    (frozenset({"fall", "fell", "fallen", "falling", "collapse",
                "collapsed", "collapsing", "faint", "fainted",
                "stumble", "stumbled", "tripped", "slip", "slipped"}),
     frozenset({"sudden-fall"})),
    (frozenset({"escape", "escaped", "escaping", "flee", "fleeing",
                "fled", "runaway", "chase", "chasing", "pursuit"}),
     frozenset({"suspicious-approach-departure"})),
    (frozenset({"incident", "incidents", "crime", "criminal", "suspicious",
                "anomaly", "anomalous"}),
     frozenset({"possible-theft", "possible-assault", "confrontation",
                "sudden-fall", "suspicious-approach-departure",
                "other-anomaly"})),
]

MAX_INCIDENT_EVENTS = 10


def _incident_categories_for_terms(terms: list[str]) -> set[str]:
    """Temporal categories implied by incident vocabulary in the query."""
    categories: set[str] = set()
    term_set = set(terms)
    for vocabulary, cats in INCIDENT_CATEGORY_MAP:
        if term_set & vocabulary:
            categories |= set(cats)
    return categories


def _subject_evidence_forms(term: str) -> Optional[frozenset]:
    """Word forms that satisfy a subject term (synonyms/lemmas included)."""
    if term in PERSON_SUBJECTS:
        return PERSON_SUBJECTS
    if term in VEHICLE_SUBJECTS:
        return VEHICLE_SUBJECTS
    if term in BIRD_SUBJECTS:
        return BIRD_SUBJECTS
    if term in INSECT_HYPERNYMS:
        return INSECT_SUBJECTS
    if term in INSECT_SUBJECTS:
        return frozenset({term, INSECT_LEMMA[term]})
    if term in ANIMAL_HYPERNYMS:
        return ANIMAL_SUBJECTS
    if term in ANIMAL_SUBJECTS:
        return frozenset({term, ANIMAL_LEMMA.get(term, term)})
    if term in OBJECT_SUBJECTS:
        return OBJECT_SUBJECTS
    return None


def _term_evidence_weight(term: str, headline: str, blob: str) -> float:
    """Prominence weight: 1.0 for headline matches, 0.5 for buried ones.

    A term matching the event's headline (action/type) means the event is
    *about* the query; a term appearing only deep in the fact blob is
    weaker evidence. Subject synonyms count via their evidence forms.
    """
    forms = _subject_evidence_forms(term)
    if forms is not None:
        if contains_word(headline, forms):
            return 1.0
        if contains_word(blob, forms):
            return 0.5
        return 0.0
    if term_matches(headline, term):
        return 1.0
    if term_matches(blob, term):
        return 0.5
    return 0.0


def score_relevance(terms: list[str], ev_dict: dict) -> tuple[float, list[str], Optional[str]]:
    """Calibrated query-time relevance plus supporting excerpts.

    Returns (relevance, matched_excerpts, primary_match). `relevance`
    blends prominence-weighted term coverage with the stored detection
    confidence; `primary_match` is the single evidence string the UI
    should headline (the action when it matches, else the first matching
    fact) so a buried mention never presents as the headline finding.
    """
    if not terms:
        return 1.0, [], None
    headline = " ".join([
        ev_dict.get("action_description", ""),
        ev_dict.get("event_type", "").replace("_", " "),
    ])
    blob = " ".join([
        ev_dict.get("scene_description", "") or "",
        " ".join(ev_dict.get("object_descriptions", [])),
        " ".join(ev_dict.get("observed_facts", [])),
    ])
    weights = [_term_evidence_weight(t, headline, headline + " " + blob)
               for t in terms]
    coverage = sum(weights) / len(terms)
    relevance = min(0.7 * coverage + 0.3 * ev_dict.get("confidence", 0.5), 1.0)

    def _matches(text: str) -> bool:
        for t in terms:
            forms = _subject_evidence_forms(t)
            if forms is not None:
                if contains_word(text, forms):
                    return True
            elif term_matches(text, t):
                return True
        return False

    excerpts: list[str] = []
    for fact in list(ev_dict.get("observed_facts", [])) + \
            list(ev_dict.get("object_descriptions", [])):
        if fact and _matches(fact) and fact not in excerpts:
            excerpts.append(fact)
        if len(excerpts) >= 3:
            break
    primary_match: Optional[str] = None
    if _matches(ev_dict.get("action_description", "")):
        primary_match = ev_dict.get("action_description") or None
    elif excerpts:
        primary_match = excerpts[0]
    return round(relevance, 3), excerpts, primary_match


async def _count_temporal_events(db_path: str, recording_id: str) -> int:
    """Return count of temporal_events rows for this recording."""
    try:
        async with get_db(db_path) as db:
            row = await (await db.execute(
                "SELECT COUNT(*) FROM temporal_events WHERE recording_id = ?",
                (recording_id,))).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


async def _maybe_auto_trigger_temporal(
    db_path: str,
    eligible_ids: list[str],
    query_terms: list[str],
    rec_meta: dict,
) -> None:
    """Auto-trigger temporal detection for recordings with no prior refinement.

    Only fires when the query contains incident vocabulary. Idempotent:
    recordings that already have temporal_events rows are skipped. Bounded
    by temporal_auto_trigger_timeout_s per recording (0 = disabled).
    Storage and meta_client are resolved from app state; if unavailable
    (e.g. in tests without a running server), the trigger silently no-ops.
    """
    if not _is_incident_query(query_terms):
        return
    try:
        from .config import get_settings
        settings = get_settings()
        timeout_s = settings.temporal_auto_trigger_timeout_s
    except Exception:
        timeout_s = 30.0
    if timeout_s <= 0:
        return
    # Resolve storage and meta_client from the app's dependency registry.
    # If the registry is not populated (e.g. unit tests without a running
    # server), silently skip and fall through to stored temporal_events.
    try:
        from .deps import is_registered, get_storage_instance, get_meta_client_instance
        if not is_registered():
            logger.debug("temporal auto-trigger: app deps not registered, skipping")
            return
        storage = get_storage_instance()
        meta_client = get_meta_client_instance()
    except Exception:
        logger.debug("temporal auto-trigger: app deps unavailable, skipping")
        return
    from .temporal_pipeline import run_temporal_detection
    for recording_id in eligible_ids:
        existing = await _count_temporal_events(db_path, recording_id)
        if existing > 0:
            logger.debug(
                "temporal auto-trigger: %s already has %d events, skipping",
                recording_id, existing)
            continue
        logger.info(
            "temporal auto-trigger: running for %s (query incident match)",
            recording_id)
        try:
            result = await asyncio.wait_for(
                run_temporal_detection(
                    recording_id=recording_id,
                    db_path=db_path,
                    storage=storage,
                    meta_client=meta_client,
                    reason="auto_trigger_investigation",
                ),
                timeout=timeout_s,
            )
            n_events = len(result.get("events", []))
            logger.info(
                "temporal auto-trigger: %s produced %d events (status=%s)",
                recording_id, n_events, result.get("status"))
        except asyncio.TimeoutError:
            logger.warning(
                "temporal auto-trigger timed out after %.0fs for %s",
                timeout_s, recording_id)
        except Exception as exc:
            logger.warning(
                "temporal auto-trigger failed for %s: %s", recording_id, exc)


async def _load_incident_events(
    db_path: str,
    eligible_ids: list[str],
    categories: set[str],
    rec_meta: dict,
    eff_start: Optional[float],
    eff_end: Optional[float],
) -> list[dict]:
    """Load stored temporal incident candidates as event dicts.

    Returns at most MAX_INCIDENT_EVENTS dicts shaped like the standard
    retrieval path (plus preset relevance fields), so incident windows
    surface in the timeline even though their neutral vision text never
    matches incident vocabulary.
    """
    if not categories or not eligible_ids:
        return []
    placeholders = ",".join("?" * len(eligible_ids))
    cat_placeholders = ",".join("?" * len(categories))
    try:
        async with get_db(db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                f"""SELECT event_id, recording_id, category, start_seconds,
                           end_seconds, timestamp_seconds, timestamp_source,
                           confidence, evidence_json
                    FROM temporal_events
                    WHERE recording_id IN ({placeholders})
                      AND category IN ({cat_placeholders})
                    ORDER BY timestamp_seconds
                    LIMIT {MAX_INCIDENT_EVENTS}""",
                eligible_ids + sorted(categories),
            )).fetchall()
    except Exception:
        # temporal_events table predates some databases; absence means
        # no incident candidates, never an error.
        return []
    incident_dicts = []
    for row in rows:
        evd = _temporal_row_to_event_dict(dict(row), rec_meta)
        if evd is None:
            continue
        ts = evd["timestamp_seconds"]
        if eff_start is not None and ts < eff_start:
            continue
        if eff_end is not None and ts > eff_end:
            continue
        incident_dicts.append(evd)
    return incident_dicts


def _temporal_row_to_event_dict(row: dict, rec_meta: dict) -> Optional[dict]:
    """Convert a temporal incident row to a standard event dict."""
    evidence = _load_json(row.get("evidence_json"), {})
    if not isinstance(evidence, dict):
        evidence = {}
    rid = row["recording_id"]
    meta = rec_meta.get(rid, {})
    start = row.get("start_seconds", 0.0) or 0.0
    end = row.get("end_seconds", start) or start
    confidence = row.get("confidence", 0.5) or 0.5

    action_steps = [s for s in evidence.get("action_sequence", [])
                    if isinstance(s, str) and s.strip()]
    observed = [f for f in evidence.get("observed_facts", [])
                if isinstance(f, str)]
    confirmed = [c for c in evidence.get("confirmed", [])
                 if isinstance(c, str)]
    action_description = ("; ".join(action_steps[:3]) or
                          (observed[0] if observed else None) or
                          f"{row.get('category', 'incident')} candidate "
                          f"{start:.1f}s–{end:.1f}s")
    steps = [{
        "step": i + 1,
        "start_seconds": start,
        "end_seconds": end,
        "description": step,
        "frame_path": None,
    } for i, step in enumerate(action_steps)]
    frames = []
    for f in evidence.get("evidence_frames", []) or []:
        if not isinstance(f, dict) or not f.get("frame_path"):
            continue
        frames.append({
            "frame_path": f["frame_path"],
            "timestamp_seconds": f.get("timestamp_seconds", start),
            "role": f.get("role", "during"),
        })
    uncertainty = [u for u in evidence.get("uncertain", [])
                   if isinstance(u, str)]
    if evidence.get("single_frame_candidate"):
        uncertainty.append(
            "Single-frame candidate — brief window, verify visually")
    uncertainty.append(
        "Incident category is an observation-only candidate, not a "
        "confirmed crime classification")

    timestamp_absolute = None
    has_reliable = False
    rec_dt = meta.get("recording_datetime")
    if rec_dt:
        try:
            dt = datetime.fromisoformat(rec_dt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            mid = (start + end) / 2.0
            abs_dt = datetime.fromtimestamp(dt.timestamp() + mid,
                                            tz=timezone.utc)
            timestamp_absolute = abs_dt.isoformat()
            has_reliable = True
        except Exception:
            pass

    relevance = round(min(0.95, 0.80 + 0.15 * confidence), 3)
    candidate_lines = [f for f in observed if f.startswith("Candidate ")]
    incident_primary = (action_steps[0] if action_steps else
                        (candidate_lines[0] if candidate_lines else
                         (observed[0] if observed else None)))
    return {
        "event_id": row["event_id"],
        "recording_id": rid,
        "timestamp_seconds": row.get("timestamp_seconds",
                                     (start + end) / 2.0),
        "start_seconds": start,
        "end_seconds": end,
        "timestamp_absolute": timestamp_absolute,
        "has_reliable_timestamp": has_reliable,
        "event_type": row.get("category", "other-anomaly"),
        "actor_description": None,
        "object_descriptions": [],
        "action_description": action_description,
        "scene_description": None,
        "evidence_frames": frames,
        "confidence": confidence,
        "observed_facts": observed + [f"Confirmed: {c}" for c in confirmed],
        "inferred_claims": [],
        "uncertainty_notes": uncertainty,
        "entities": evidence.get("tracks", []) or [],
        "action_sequence": steps,
        "original_filename": meta.get("original_filename", ""),
        "rec_dt": rec_dt,
        "rec_tz": meta.get("recording_timezone"),
        "cam_name": meta.get("camera_name"),
        "cam_loc": meta.get("camera_location"),
        "_incident_preset": True,
        "_incident_relevance": relevance,
        "matched_excerpts": (observed + confirmed)[:3],
        "primary_match": incident_primary,
    }


def _build_summary(query: str, events: list) -> str:
    """Concise one-line summary: primary finding first, then counts."""
    if not events:
        return f"No events matched '{query}'."
    primary = max(events,
                  key=lambda e: e.relevance_score
                  if e.relevance_score is not None else 0.0)
    label = (primary.primary_match or primary.action_description or
             primary.event_type).strip()
    if len(label) > 90:
        label = label[:87] + "..."
    n_rec = len({e.recording_id for e in events})
    ts = _format_ts(primary.timestamp_seconds)
    return (f"Best match: {label} at {ts} in "
            f"'{primary.recording_filename or primary.recording_id[:8]}' — "
            f"{len(events)} event(s) across {n_rec} recording(s).")


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
