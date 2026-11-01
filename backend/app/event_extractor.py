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
# Deliberately bird-only: broader "animal" words must not auto-classify
# (or auto-match) other species, and other animals must not render as Bird.
BIRD_SUBJECTS = frozenset({"bird", "birds"})
# True insects (singular/plural) plus larval forms, for query relevance.
# Retrieval-class only: insect evidence is typically secondary (carried,
# crawled-upon), so it gates queries but never re-types the primary
# subject — a bird scene with ants stays bird_detected. Spiders are
# excluded (arachnids, not insects); "bug" is excluded (ambiguous with
# listening devices and software defects in CCTV context).
INSECT_SUBJECTS = frozenset({
    "insect", "insects", "ant", "ants", "grub", "grubs",
    "larva", "larvae", "fly", "flies", "beetle", "beetles",
    "bee", "bees", "wasp", "wasps",
})
# Hypernym vs hyponym: "insect" matches any insect evidence, but a hyponym
# query ("grub") must match its own lemma — ants are not grubs. Singular and
# plural of the same lemma are equivalent.
INSECT_HYPERNYMS = frozenset({"insect", "insects"})
INSECT_LEMMA = {
    "ant": "ants", "ants": "ant",
    "grub": "grubs", "grubs": "grub",
    "larva": "larvae", "larvae": "larva",
    "fly": "flies", "flies": "fly",
    "beetle": "beetles", "beetles": "beetle",
    "bee": "bees", "bees": "bee",
    "wasp": "wasps", "wasps": "wasp",
}
OBJECT_SUBJECTS = frozenset({
    "bag", "bags", "backpack", "backpacks", "suitcase", "suitcases",
    "luggage", "package", "packages",
    "box", "boxes", "carton", "cartons", "crate", "crates",
    "parcel", "parcels",
})
# Animal subjects for query relevance. Hypernym ("animal") matches animal
# evidence only — never bird or person evidence (broad words are not
# auto-synonyms; see test_animal_does_not_match_people). Hyponyms ("dog")
# require their own lemma — a cat is not a dog. Retrieval-class only: no
# animal event category exists (see test_dog_labels_stay_unforced) —
# unknown subjects stay scene_change and match by description text.
ANIMAL_HYPERNYMS = frozenset({"animal", "animals", "pet", "pets"})
ANIMAL_SUBJECTS = frozenset({
    "animal", "animals", "pet", "pets",
    "dog", "dogs", "puppy", "puppies",
    "cat", "cats", "kitten", "kittens",
    "cow", "cows", "cattle", "buffalo", "buffaloes",
    "horse", "horses", "goat", "goats", "sheep",
    "monkey", "monkeys", "pig", "pigs",
    "donkey", "donkeys", "camel", "camels",
    "elephant", "elephants", "deer",
    "rabbit", "rabbits", "squirrel", "squirrels",
    "rat", "rats", "mouse", "mice",
    "snake", "snakes", "lizard", "lizards",
})
ANIMAL_LEMMA = {
    "dog": "dogs", "dogs": "dog",
    "puppy": "puppies", "puppies": "puppy",
    "cat": "cats", "cats": "cat",
    "kitten": "kittens", "kittens": "kitten",
    "cow": "cows", "cows": "cow",
    "buffalo": "buffaloes", "buffaloes": "buffalo",
    "horse": "horses", "horses": "horse",
    "goat": "goats", "goats": "goat",
    "monkey": "monkeys", "monkeys": "monkey",
    "pig": "pigs", "pigs": "pig",
    "donkey": "donkeys", "donkeys": "donkey",
    "camel": "camels", "camels": "camel",
    "elephant": "elephants", "elephants": "elephant",
    "rabbit": "rabbits", "rabbits": "rabbit",
    "squirrel": "squirrels", "squirrels": "squirrel",
    "rat": "rats", "rats": "rat",
    "mouse": "mice", "mice": "mouse",
    "snake": "snakes", "snakes": "snake",
    "lizard": "lizards", "lizards": "lizard",
}
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


def term_matches(text: str, term: str) -> bool:
    """Word-boundary match of a single query term against text.

    Substring matching ("dog" matching "hotdog", "car" matching "cardboard")
    promoted unrelated events to primary findings; all retrieval matching
    now goes through word boundaries.
    """
    if not text or not term:
        return False
    return re.search(r"\b" + re.escape(term.lower()) + r"\b",
                     text.lower()) is not None


def normalize_description(label: str, attrs: Optional[dict]) -> str:
    """Merge a label with its color/details without duplicating words.

    The model often repeats the color inside the label ("black motorcycle"
    with color "black"); blind prepending produced "black black motorcycle".
    """
    desc = (label or "").strip()
    if not desc:
        return ""
    attrs = attrs or {}
    color = str(attrs.get("color") or "").strip()
    if color and color.lower() not in desc.lower():
        desc = f"{color} {desc}"
    details = str(attrs.get("distinctive_details") or "").strip()
    if details and details.lower() not in desc.lower():
        desc = f"{desc} ({details})"
    # Collapse model stutter ("black black motorcycle" written verbatim).
    desc = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", desc, flags=re.IGNORECASE)
    return desc


# Filler tokens ignored when comparing entity labels across frames.
_ENTITY_FILLERS = frozenset({
    "a", "an", "the", "in", "on", "of", "with", "and", "near", "at",
    "by", "beside", "outside", "inside", "left", "right", "center",
    "centre", "side", "frame", "edge", "foreground", "background",
    "far", "mid",
})

# Head-noun synonyms: the same person is "man" in one frame and "person"
# in the next; normalize so tracking links them.
_HEAD_SYNONYMS = {
    "men": "person", "man": "person", "woman": "person",
    "women": "person", "human": "person", "humans": "person",
    "people": "person", "persons": "person", "boy": "person",
    "girl": "person", "child": "person",
}
for _lemma_map in (ANIMAL_LEMMA, INSECT_LEMMA):
    for _a, _b in _lemma_map.items():
        _singular = min(_a, _b, key=len)
        _HEAD_SYNONYMS[_a] = _singular
        _HEAD_SYNONYMS[_b] = _singular
del _lemma_map, _a, _b, _singular

# Candidate head nouns: the entity kind is the first known noun in the
# label ("person in dark dress" -> person, "brown dog lying" -> dog).
_HEAD_NOUNS = frozenset(set(_HEAD_SYNONYMS) | {
    "person", "dog", "cat", "bird", "car", "vehicle", "truck", "van",
    "motorcycle", "bike", "scooter", "bus", "bicycle", "box", "bag",
    "backpack", "suitcase", "package", "stool", "chair", "table", "gate",
    "door", "shop", "tree", "motorcyclist", "driver", "passenger", "rider",
})

# Minimum content-token Jaccard to link two labels to one entity, and the
# maximum gap (in distinct-frame steps) for "adjacent" frames.
ENTITY_JACCARD_THRESHOLD = 0.5
ENTITY_MAX_FRAME_GAP = 2


def _entity_signature(label: str) -> tuple[str, frozenset]:
    tokens = [t for t in re.findall(r"[a-z0-9]+", label.lower())
              if t not in _ENTITY_FILLERS]
    head = next((t for t in tokens if t in _HEAD_NOUNS),
                tokens[-1] if tokens else "")
    head = _HEAD_SYNONYMS.get(head, head)
    return head, frozenset(tokens)


def track_entities(group: list[dict]) -> list[dict]:
    """Link same-entity observations across adjacent frames.

    The vision model rephrases labels per frame ("person in dark long
    garment" vs "person in long dark garment"); without tracking, one
    person appears as N different evidence entries. Links share a head
    noun, token Jaccard >= 0.5, and nearby frames. Deterministic:
    single time-ordered pass, entities numbered by first appearance.
    """
    ordered = sorted(group, key=lambda o: o["video_timestamp_seconds"])
    frame_index = {ts: i for i, ts in
                   enumerate(sorted({o["video_timestamp_seconds"]
                                     for o in ordered}))}
    entities: list[dict] = []

    def similarity(head_a, toks_a, head_b, toks_b) -> float:
        if head_a != head_b or not head_a:
            return 0.0
        union = toks_a | toks_b
        if not union:
            return 0.0
        return len(toks_a & toks_b) / len(union)

    for obs in ordered:
        head, toks = _entity_signature(obs.get("object_label") or "")
        fidx = frame_index[obs["video_timestamp_seconds"]]
        best, best_sim = None, 0.0
        for ent in entities:
            if fidx - ent["_last_fidx"] > ENTITY_MAX_FRAME_GAP:
                continue
            sim = similarity(head, toks, ent["_head"], ent["_toks"])
            if sim >= ENTITY_JACCARD_THRESHOLD and sim > best_sim:
                best, best_sim = ent, sim
        if best is None:
            best = {
                "entity_id": f"E{len(entities) + 1}",
                "_head": head, "_toks": set(toks),
                "_last_fidx": fidx,
                "first_seen_seconds": obs["video_timestamp_seconds"],
                "last_seen_seconds": obs["video_timestamp_seconds"],
                "frame_timestamps": set(),
                "observation_count": 0,
                "_best": None, "_best_score": -1.0,
            }
            entities.append(best)
        best["_toks"] |= toks
        best["_last_fidx"] = fidx
        best["last_seen_seconds"] = obs["video_timestamp_seconds"]
        best["frame_timestamps"].add(obs["video_timestamp_seconds"])
        best["observation_count"] += 1
        score = obs.get("detection_score", 0.5)
        if score > best["_best_score"]:
            best["_best_score"] = score
            best["_best"] = obs

    tracks = []
    for ent in entities:
        best = ent.pop("_best") or {}
        attrs: dict = {}
        if best.get("object_attributes"):
            try:
                attrs = json.loads(best["object_attributes"])
            except Exception:
                pass
        tracks.append({
            "entity_id": ent["entity_id"],
            "head": ent["_head"] or "object",
            "display_label": normalize_description(
                best.get("object_label", ""), attrs),
            "first_seen_seconds": ent["first_seen_seconds"],
            "last_seen_seconds": ent["last_seen_seconds"],
            "frame_count": len(ent["frame_timestamps"]),
            "observation_count": ent["observation_count"],
            "representative_frame": best.get("source_frame_path"),
        })
    return tracks


MAX_ACTION_STEPS = 20


def build_action_sequence(group: list[dict]) -> tuple[list[dict], bool]:
    """Compress consecutive same-scene observations into timestamped steps.

    Returns (steps, truncated). Each step carries its span plus the frame
    that opens it, so the timeline can show what happened when instead of
    a single generic scene summary.
    """
    ordered = sorted(group, key=lambda o: o["video_timestamp_seconds"])
    steps: list[dict] = []
    for obs in ordered:
        scene = (obs.get("scene_description") or "").strip()
        if not scene:
            continue
        ts = obs["video_timestamp_seconds"]
        if steps and steps[-1]["description"] == scene[:300]:
            steps[-1]["end_seconds"] = ts
        else:
            steps.append({
                "step": len(steps) + 1,
                "start_seconds": ts,
                "end_seconds": ts,
                "description": scene[:300],
                "frame_path": obs.get("source_frame_path"),
            })
    truncated = len(steps) > MAX_ACTION_STEPS
    return steps[:MAX_ACTION_STEPS], truncated


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

    # Persist events (span/entity/sequence columns added by migrations;
    # fall back through legacy column sets on older databases).
    async with get_db(db_path) as db:
        for ev in events:
            base_values = (
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
            )
            try:
                await db.execute(
                    """INSERT OR REPLACE INTO events(
                        event_id, recording_id, timestamp_seconds, timestamp_absolute,
                        has_reliable_timestamp, event_type, actor_description,
                        object_descriptions, action_description, scene_description,
                        evidence_frames, confidence, observed_facts, inferred_claims,
                        uncertainty_notes, source_observation_ids, created_at,
                        start_seconds, end_seconds, entities_json,
                        action_sequence_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    base_values + (
                        ev.get("start_seconds"),
                        ev.get("end_seconds"),
                        json.dumps(ev.get("entities", [])),
                        json.dumps(ev.get("action_sequence", [])),
                    ),
                )
            except Exception:
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
                        base_values + (
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
                        base_values,
                    )
        # Event changes are recording changes: bump updated_at so the
        # investigation-result cache fingerprint invalidates stale rows.
        await db.execute(
            "UPDATE recordings SET updated_at = ? WHERE recording_id = ?",
            (_now(), recording_id),
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
    single_frame = len(distinct_frames) < MIN_CONFIRMING_FRAMES
    if single_frame:
        # Allow single-frame groups through at reduced confidence instead of
        # silently dropping them. Brief incidents (3-5s at 1 FPS) may genuinely
        # produce only one confirming frame; the temporal pipeline then picks
        # them up with dense sampling. The uncertainty note makes this visible.
        logger.debug(
            "Single-frame group at t=%.1fs (%d obs): preserving with low confidence",
            group[0]["video_timestamp_seconds"], len(group),
        )

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
            desc = normalize_description(label, attrs)
            if desc not in object_labels:
                object_labels.append(desc)

        if obs.get("scene_description") and obs["scene_description"] not in scene_parts:
            scene_parts.append(obs["scene_description"])

        total_confidence += obs.get("detection_score", 0.5)

    avg_confidence = total_confidence / len(group) if group else 0.0

    if avg_confidence < MIN_CONFIDENCE:
        return None

    # Cap single-frame events below 0.5 confidence and add an uncertainty
    # note so investigators know to verify visually.
    if single_frame:
        avg_confidence = min(avg_confidence, 0.45)

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
    if single_frame:
        uncertainty_notes.append(
            "Single-frame observation — brief event, not confirmed by adjacent frames; "
            "verify visually or use Timeline tab for dense temporal analysis"
        )
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

    # Entity tracks across adjacent frames + timestamped action steps.
    entities = track_entities(group)
    action_sequence, steps_truncated = build_action_sequence(group)
    if steps_truncated:
        uncertainty_notes.append(
            f"Action sequence truncated to {MAX_ACTION_STEPS} steps — "
            f"see evidence frames for full coverage"
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
        "entities": entities,
        "action_sequence": action_sequence,
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
    if contains_word(combined, BIRD_SUBJECTS):
        return "bird_detected"
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
