"""
Guardian: upload-time hazard detection and alert management.

Reads stored upload_understandings segments, calls detect_hazards_for_segment()
on the vision client, deduplicates overlapping same-category detections, and
persists results to guardian_alerts. Fully idempotent via UNIQUE(recording_id, dedup_key).
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from .database import get_db

logger = logging.getLogger(__name__)

VALID_HAZARD_CATEGORIES = frozenset({
    "fire_smoke",
    "person_fall_injury",
    "vehicle_collision",
    "dangerous_animal",
    "weapon_visible",
    "restricted_area_entry",
    "other_hazard",
    "flood_water",
    "electrical_hazard",
    "structural_hazard",
    "unauthorized_entry",
    "vandalism",
})

VALID_SEVERITY_LEVELS = frozenset({"critical", "high", "medium", "low"})
VALID_REVIEW_STATUSES = frozenset({"new", "acknowledged", "dismissed"})

HAZARD_CATEGORY_LABELS = {
    "fire_smoke": "Fire / Smoke",
    "person_fall_injury": "Person Fall / Injury",
    "vehicle_collision": "Vehicle Collision",
    "dangerous_animal": "Dangerous Animal",
    "weapon_visible": "Weapon Visible",
    "restricted_area_entry": "Restricted Area Entry",
    "other_hazard": "Other Hazard",
    "flood_water": "Flood / Water Hazard",
    "electrical_hazard": "Electrical Hazard",
    "structural_hazard": "Structural Hazard",
    "unauthorized_entry": "Unauthorized Entry",
    "vandalism": "Vandalism / Property Damage",
}

# Keywords that trigger a guardian analysis call even when unusual_observations is empty
HAZARD_KEYWORDS = frozenset({
    "fire", "flame", "smoke", "burning",
    "fall", "fell", "fallen", "lying", "collapsed", "unconscious",
    "collision", "crash", "impact",
    "snake", "weapon", "gun", "knife", "firearm",
    "flood", "flooding", "water leak", "water damage",
    "electrical", "spark", "short circuit",
    "structural", "collapse",
    "vandal", "vandalism", "graffiti", "break-in", "break in",
    "exposed wire",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_dedup_key(hazard_category: str, time_range_start: float) -> str:
    """Bucket key: same category within the same 30s window deduplicates."""
    return f"{hazard_category}:{int(time_range_start // 30)}"


def _has_hazard_keyword(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in HAZARD_KEYWORDS)


def _normalize_category(cat: str) -> str:
    return cat if cat in VALID_HAZARD_CATEGORIES else "other_hazard"


def _normalize_severity(sev: str) -> str:
    return sev if sev in VALID_SEVERITY_LEVELS else "medium"


# Keywords in entity descriptions that indicate an animal is actively dangerous.
# Presence alone (walking, sitting, playing) is NOT sufficient.
_DANGEROUS_ANIMAL_BEHAVIOR_KEYWORDS: frozenset[str] = frozenset({
    "attack", "attacking", "biting", "bite", "chasing", "chase",
    "lunging", "lunge", "aggressive", "aggression", "threatening",
    "cornered", "charging", "mauling",
})

# Domestic/common animals that are NOT inherently dangerous.
# An entity whose type/description contains one of these words will only
# produce a dangerous_animal alert if a behavior keyword is also present.
_CONDITIONAL_ANIMAL_KEYWORDS: frozenset[str] = frozenset({
    "dog", "cat", "bird", "chicken", "pigeon", "crow",
})

# Entity type → hazard category mapping for direct-video path.
# Only inherently dangerous wild animals are listed unconditionally.
# Domestic animals (dog, cat, bird) also appear here so the behavior-conditional
# check in _classify_hazards_from_understanding can fire when needed.
_ENTITY_HAZARD_MAP: list[tuple[str, str, str, float]] = [
    # (keyword in type/description, hazard_category, severity, confidence)
    ("snake",       "dangerous_animal", "critical", 0.92),
    ("cobra",       "dangerous_animal", "critical", 0.95),
    ("spider",      "dangerous_animal", "high",     0.80),
    # Domestic animals: classified only when behavior keywords confirm danger
    # (the conditional gate is in _classify_hazards_from_understanding)
    ("dog",         "dangerous_animal", "high",     0.82),
    ("cat",         "dangerous_animal", "medium",   0.65),
    ("bird",        "dangerous_animal", "low",      0.55),
    ("bear",        "dangerous_animal", "critical", 0.90),
    ("alligator",   "dangerous_animal", "critical", 0.90),
    ("crocodile",   "dangerous_animal", "critical", 0.90),
    ("tiger",       "dangerous_animal", "critical", 0.95),
    ("lion",        "dangerous_animal", "critical", 0.95),
    ("leopard",     "dangerous_animal", "critical", 0.95),
    ("cheetah",     "dangerous_animal", "critical", 0.92),
    ("panther",     "dangerous_animal", "critical", 0.92),
    ("wolf",        "dangerous_animal", "critical", 0.88),
    ("elephant",    "dangerous_animal", "critical", 0.90),
    ("rhino",       "dangerous_animal", "critical", 0.90),
    ("rhinoceros",  "dangerous_animal", "critical", 0.90),
    ("hyena",       "dangerous_animal", "high",     0.85),
    ("fire",        "fire_smoke",       "critical", 0.90),
    ("flame",       "fire_smoke",       "critical", 0.90),
    ("smoke",       "fire_smoke",       "high",     0.85),
    ("gun",         "weapon_visible",   "critical", 0.90),
    ("knife",       "weapon_visible",   "high",     0.85),
    ("weapon",      "weapon_visible",   "high",     0.85),
    ("fall",        "person_fall_injury","high",     0.80),
    ("collision",   "vehicle_collision","high",     0.80),
    ("flood",       "flood_water",      "high",     0.85),
    ("water",       "flood_water",      "medium",   0.70),
    ("electrical",  "electrical_hazard","high",     0.82),
    ("spark",       "electrical_hazard","critical", 0.88),
    ("collapse",    "structural_hazard","critical", 0.90),
    ("vandal",      "vandalism",        "high",     0.80),
    ("graffiti",    "vandalism",        "medium",   0.72),
]

_UNUSUAL_OBS_HAZARD_MAP: list[tuple[str, str, str, float]] = [
    ("snake",             "dangerous_animal",    "critical", 0.90),
    ("cobra",             "dangerous_animal",    "critical", 0.92),
    ("tiger",             "dangerous_animal",    "critical", 0.95),
    ("lion",              "dangerous_animal",    "critical", 0.95),
    ("leopard",           "dangerous_animal",    "critical", 0.95),
    ("bear",              "dangerous_animal",    "critical", 0.90),
    ("wolf",              "dangerous_animal",    "critical", 0.88),
    ("elephant",          "dangerous_animal",    "critical", 0.90),
    # Dogs / cats only when behavior indicates active danger
    ("dog attack",        "dangerous_animal",    "high",     0.82),
    ("attacking dog",     "dangerous_animal",    "high",     0.82),
    ("dog biting",        "dangerous_animal",    "high",     0.80),
    ("dog chasing",       "dangerous_animal",    "medium",   0.72),
    ("dog aggressive",    "dangerous_animal",    "medium",   0.70),
    ("fire",              "fire_smoke",           "critical", 0.90),
    ("smoke",             "fire_smoke",           "high",     0.85),
    ("gun",               "weapon_visible",       "critical", 0.90),
    ("weapon",            "weapon_visible",       "high",     0.85),
    ("knife",             "weapon_visible",       "high",     0.85),
    ("fell",              "person_fall_injury",   "high",     0.80),
    ("fallen",            "person_fall_injury",   "high",     0.80),
    ("unconscious",       "person_fall_injury",   "critical", 0.88),
    ("collision",         "vehicle_collision",    "high",     0.80),
    # Robbery / assault patterns — including natural-language descriptions
    # that the LLM uses without crime vocabulary (e.g. chain snatching)
    ("snatch",            "other_hazard",         "high",     0.82),
    ("grab",              "other_hazard",         "high",     0.78),
    ("physical interaction", "other_hazard",      "medium",   0.72),
    ("stops beside",      "other_hazard",         "medium",   0.68),
    ("stops directly",    "other_hazard",         "medium",   0.68),
    ("confronts",         "other_hazard",         "high",     0.80),
    ("struggle",          "other_hazard",         "high",     0.85),
    ("assault",           "other_hazard",         "critical", 0.88),
    ("attack",            "other_hazard",         "critical", 0.88),
    ("robbery",           "other_hazard",         "critical", 0.92),
    ("theft",             "other_hazard",         "high",     0.82),
    ("loiter",            "other_hazard",         "low",      0.60),
    ("trespass",          "restricted_area_entry","medium",   0.72),
    # Chain-snatching / theft — LLM often narrates the act without crime words
    ("no longer visible", "other_hazard",         "high",     0.78),
    ("extends arm toward","other_hazard",          "medium",   0.72),
    ("reaches toward",    "other_hazard",          "medium",   0.70),
    ("clutches his neck", "other_hazard",          "high",     0.80),
    ("clutches her neck", "other_hazard",          "high",     0.80),
    ("clutches neck",     "other_hazard",          "high",     0.80),
    ("departs quickly",   "other_hazard",          "medium",   0.68),
    ("speeds away",       "other_hazard",          "medium",   0.68),
    ("drives away quickly","other_hazard",         "medium",   0.68),
    ("runs toward",       "other_hazard",          "medium",   0.65),
    ("points after",      "other_hazard",          "medium",   0.65),
    # Property-hazard categories
    ("flood",             "flood_water",         "high",     0.85),
    ("water leak",        "flood_water",         "high",     0.82),
    ("water damage",      "flood_water",         "high",     0.80),
    ("electrical",        "electrical_hazard",   "high",     0.82),
    ("spark",             "electrical_hazard",   "critical", 0.88),
    ("short circuit",     "electrical_hazard",   "critical", 0.88),
    ("structural",        "structural_hazard",   "high",     0.80),
    ("collapse",          "structural_hazard",   "critical", 0.90),
    ("vandal",            "vandalism",           "high",     0.80),
    ("graffiti",          "vandalism",           "medium",   0.72),
    ("break-in",          "unauthorized_entry",  "high",     0.85),
    ("break in",          "unauthorized_entry",  "high",     0.85),
    ("forced entry",      "unauthorized_entry",  "critical", 0.90),
    ("intrusion",         "unauthorized_entry",  "high",     0.82),
]


def merge_overlapping_hazards(raw: list[dict]) -> list[dict]:
    """Merge same-category hazard detections whose time windows overlap or are adjacent."""
    if not raw:
        return []

    by_category: dict[str, list[dict]] = {}
    for item in raw:
        cat = item["hazard_category"]
        by_category.setdefault(cat, []).append(item)

    merged = []
    for cat, items in by_category.items():
        items_sorted = sorted(items, key=lambda x: x["time_range_start"])
        groups: list[list[dict]] = []
        for item in items_sorted:
            if groups and item["time_range_start"] <= groups[-1][-1]["time_range_end"] + 0.5:
                groups[-1].append(item)
            else:
                groups.append([item])
        for group in groups:
            best = max(group, key=lambda x: x["confidence"])
            all_frames: list[dict] = []
            seen_paths: set = set()
            for g in group:
                for f in g.get("evidence_frames", []):
                    fp = f.get("frame_path", "")
                    if fp not in seen_paths:
                        all_frames.append(f)
                        seen_paths.add(fp)
            seg_ids = list({g["segment_id"] for g in group if g.get("segment_id")})
            merged.append({
                "hazard_category": cat,
                "severity": best["severity"],
                "confidence": max(g["confidence"] for g in group),
                "timestamp_seconds": group[0]["timestamp_seconds"],
                "time_range_start": group[0]["time_range_start"],
                "time_range_end": group[-1]["time_range_end"],
                "description": best["description"],
                "evidence_frames": all_frames,
                "supporting_segment_ids": seg_ids,
                "is_mocked": group[0].get("is_mocked", False),
            })
    return merged


def _classify_hazards_from_understanding(
    entities: list[dict],
    actions: list[dict],
    unusual_observations: list[str],
    scene_context: str,
    seg_start: float,
    seg_end: float,
    segment_id: str,
) -> list[dict]:
    """Derive Guardian hazard detections from structured understanding data.

    Used when no frame images are available (direct video pipeline). Scans
    entity types/descriptions and unusual_observations for hazard keywords and
    maps them to guardian_alerts schema items. No extra model call — the video
    analysis already did the work.
    """
    seen: dict[str, dict] = {}  # category → best detection so far

    def _add(category: str, severity: str, confidence: float, description: str,
             timestamp: float) -> None:
        existing = seen.get(category)
        if existing is None or confidence > existing["confidence"]:
            seen[category] = {
                "hazard_category": category,
                "severity": severity,
                "confidence": confidence,
                "timestamp_seconds": timestamp,
                "time_range_start": seg_start,
                "time_range_end": seg_end,
                "description": description,
                "evidence_frames": [],
                "segment_id": segment_id,
                "is_mocked": False,
            }

    for entity in entities:
        combined = f"{entity.get('type','')} {entity.get('description','')}".lower()
        ts = float(entity.get("first_seen_seconds", seg_start))
        for keyword, cat, sev, conf in _ENTITY_HAZARD_MAP:
            if keyword in combined:
                # For conditional domestic animals: require explicit dangerous
                # behavior in the description before raising a hazard alert.
                if keyword in _CONDITIONAL_ANIMAL_KEYWORDS:
                    if not any(bk in combined for bk in _DANGEROUS_ANIMAL_BEHAVIOR_KEYWORDS):
                        continue
                _add(cat, sev, conf,
                     f"Detected {entity.get('type','entity')}: {entity.get('description','')}",
                     ts)

    for obs in unusual_observations:
        obs_lower = obs.lower()
        for keyword, cat, sev, conf in _UNUSUAL_OBS_HAZARD_MAP:
            if keyword in obs_lower:
                _add(cat, sev, conf, obs, seg_start)

    return list(seen.values())



    """Merge same-category hazard detections whose time windows overlap or are adjacent.

    Input items must have: hazard_category, severity, confidence, timestamp_seconds,
    time_range_start, time_range_end, description, evidence_frames, segment_id.
    Returns a deduplicated list with merged time ranges and max confidence.
    """
    if not raw:
        return []

    by_category: dict[str, list[dict]] = {}
    for item in raw:
        cat = item["hazard_category"]
        by_category.setdefault(cat, []).append(item)

    merged = []
    for cat, items in by_category.items():
        items_sorted = sorted(items, key=lambda x: x["time_range_start"])
        groups: list[list[dict]] = []
        for item in items_sorted:
            if groups and item["time_range_start"] <= groups[-1][-1]["time_range_end"] + 0.5:
                groups[-1].append(item)
            else:
                groups.append([item])
        for group in groups:
            best = max(group, key=lambda x: x["confidence"])
            all_frames: list[dict] = []
            seen_paths = set()
            for g in group:
                for f in g.get("evidence_frames", []):
                    fp = f.get("frame_path", "")
                    if fp not in seen_paths:
                        all_frames.append(f)
                        seen_paths.add(fp)
            seg_ids = list({g["segment_id"] for g in group if g.get("segment_id")})
            merged.append({
                "hazard_category": cat,
                "severity": best["severity"],
                "confidence": max(g["confidence"] for g in group),
                "timestamp_seconds": group[0]["timestamp_seconds"],
                "time_range_start": group[0]["time_range_start"],
                "time_range_end": group[-1]["time_range_end"],
                "description": best["description"],
                "evidence_frames": all_frames,
                "supporting_segment_ids": seg_ids,
                "is_mocked": group[0].get("is_mocked", False),
            })
    return merged


async def upsert_guardian_alert(
    db_path: str,
    recording_id: str,
    alert: dict,
) -> bool:
    """INSERT OR IGNORE alert by (recording_id, dedup_key). Returns True if newly inserted."""
    dedup_key = compute_dedup_key(alert["hazard_category"], alert["time_range_start"])
    alert_id = str(uuid.uuid4())
    now = _now()
    async with get_db(db_path) as db:
        cur = await db.execute(
            """INSERT OR IGNORE INTO guardian_alerts(
                alert_id, recording_id, hazard_category, severity, confidence,
                timestamp_seconds, time_range_start, time_range_end, description,
                evidence_frames_json, supporting_segment_ids_json,
                review_status, is_mocked, dedup_key, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                alert_id,
                recording_id,
                alert["hazard_category"],
                alert["severity"],
                float(alert["confidence"]),
                float(alert["timestamp_seconds"]),
                float(alert["time_range_start"]),
                float(alert["time_range_end"]),
                alert["description"],
                json.dumps(alert.get("evidence_frames", [])),
                json.dumps(alert.get("supporting_segment_ids", [])),
                "new",
                1 if alert.get("is_mocked") else 0,
                dedup_key,
                now,
                now,
            ),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_alerts_for_recording(
    db_path: str,
    recording_id: str,
    review_status: Optional[str] = None,
    severity: Optional[str] = None,
    min_confidence: Optional[float] = None,
) -> list[dict]:
    async with get_db(db_path) as db:
        db.row_factory = __import__("aiosqlite").Row
        conditions = ["recording_id = ?"]
        params: list = [recording_id]
        if review_status:
            conditions.append("review_status = ?")
            params.append(review_status)
        if severity:
            conditions.append("severity = ?")
            params.append(severity)
        if min_confidence is not None:
            conditions.append("confidence >= ?")
            params.append(min_confidence)
        where = " AND ".join(conditions)
        rows = await (await db.execute(
            f"SELECT * FROM guardian_alerts WHERE {where} ORDER BY time_range_start ASC",
            params,
        )).fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_all_alerts(
    db_path: str,
    limit: int = 200,
    offset: int = 0,
    review_status: Optional[str] = None,
    severity: Optional[str] = None,
    hazard_category: Optional[str] = None,
) -> tuple[list[dict], int]:
    """Returns (alerts_with_recording_info, total_count)."""
    import aiosqlite
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        conditions = []
        params: list = []
        if review_status:
            conditions.append("ga.review_status = ?")
            params.append(review_status)
        if severity:
            conditions.append("ga.severity = ?")
            params.append(severity)
        if hazard_category:
            conditions.append("ga.hazard_category = ?")
            params.append(hazard_category)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        total_row = await (await db.execute(
            f"SELECT COUNT(*) FROM guardian_alerts ga {where}", params
        )).fetchone()
        total = total_row[0] if total_row else 0
        rows = await (await db.execute(
            f"""SELECT ga.*,
                       r.original_filename, r.camera_name, r.camera_location
                FROM guardian_alerts ga
                LEFT JOIN recordings r ON r.recording_id = ga.recording_id
                {where}
                ORDER BY ga.time_range_start ASC
                LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        )).fetchall()
    alerts = []
    for r in rows:
        d = _row_to_dict(r)
        d["original_filename"] = r["original_filename"]
        d["camera_name"] = r["camera_name"]
        d["camera_location"] = r["camera_location"]
        alerts.append(d)
    return alerts, total


async def update_alert_review_status(
    db_path: str,
    recording_id: str,
    alert_id: str,
    review_status: str,
) -> bool:
    """Returns True if the alert was found and updated."""
    now = _now()
    async with get_db(db_path) as db:
        cur = await db.execute(
            "UPDATE guardian_alerts SET review_status=?, updated_at=? WHERE alert_id=? AND recording_id=?",
            (review_status, now, alert_id, recording_id),
        )
        await db.commit()
        return cur.rowcount > 0


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["evidence_frames"] = json.loads(d.pop("evidence_frames_json", "[]"))
    d["supporting_segment_ids"] = json.loads(d.pop("supporting_segment_ids_json", "[]"))
    d["is_mocked"] = bool(d.get("is_mocked", 0))
    return d


async def run_guardian_detection(
    recording_id: str,
    db_path: str,
    storage,
    meta_client,
    errors: list,
) -> int:
    """Run Guardian hazard detection after upload_understandings are stored.

    Loads stored segments, calls detect_hazards_for_segment() per segment (if
    the segment has unusual_observations or the client always supports it),
    merges overlapping same-category detections, and upserts alerts.

    Returns count of newly created alerts.
    """
    # Detect mock mode
    from .meta_client import MockMetaClient
    is_mocked = isinstance(meta_client, MockMetaClient) or getattr(meta_client, "_is_mock", False)

    # For the frame-based path we need detect_hazards_for_segment.
    # For the direct-video path (no frames) we use _classify_hazards_from_understanding
    # which needs no model call. We proceed in both cases and decide per-segment below.
    has_frame_detection = hasattr(meta_client, "detect_hazards_for_segment")

    # Load upload understanding rows
    import aiosqlite
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT segment_id, start_seconds, end_seconds,
                      unusual_observations_json, entities_json, actions_json,
                      scene_context
               FROM upload_understandings WHERE recording_id = ? ORDER BY start_seconds""",
            (recording_id,),
        )).fetchall()

    if not rows:
        return 0

    # Load frame paths for this recording
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        obs_rows = await (await db.execute(
            """SELECT video_timestamp_seconds, source_frame_path
               FROM observations WHERE recording_id = ?
               ORDER BY video_timestamp_seconds""",
            (recording_id,),
        )).fetchall()

    frame_by_ts = {r["video_timestamp_seconds"]: r["source_frame_path"] for r in obs_rows}
    sorted_ts = sorted(frame_by_ts.keys())
    has_frames = bool(frame_by_ts)

    raw_detections: list[dict] = []

    for row in rows:
        segment_id = row["segment_id"]
        seg_start = row["start_seconds"]
        seg_end = row["end_seconds"]
        unusual = json.loads(row["unusual_observations_json"] or "[]")
        entities = json.loads(row["entities_json"] or "[]")
        actions = json.loads(row["actions_json"] or "[]")
        ctx = row["scene_context"] or ""

        # Check whether this segment contains hazard-relevant content
        has_unusual = bool(unusual)
        has_keyword = (
            any(_has_hazard_keyword(obs) for obs in unusual)
            or _has_hazard_keyword(ctx)
            or any(
                _has_hazard_keyword(f"{e.get('type','')} {e.get('description','')}")
                for e in entities
            )
        )

        if not has_unusual and not has_keyword:
            continue

        # ── Direct-video path: no frames available ────────────────────────────
        if not has_frames:
            detections = _classify_hazards_from_understanding(
                entities=entities,
                actions=actions,
                unusual_observations=unusual,
                scene_context=ctx,
                seg_start=seg_start,
                seg_end=seg_end,
                segment_id=segment_id,
            )
            raw_detections.extend(detections)
            continue

        # ── Frame-based path ──────────────────────────────────────────────────
        seg_ts = [ts for ts in sorted_ts if seg_start <= ts <= seg_end]
        if not seg_ts:
            continue

        frame_bytes = []
        valid_ts = []
        for ts in seg_ts:
            fp = frame_by_ts.get(ts)
            if not fp:
                continue
            abs_path = storage.data_dir / fp
            try:
                frame_bytes.append(abs_path.read_bytes())
                valid_ts.append(ts)
            except Exception:
                pass

        if not frame_bytes:
            continue

        if not has_frame_detection:
            continue

        try:
            result = await meta_client.detect_hazards_for_segment(frame_bytes, valid_ts)
        except Exception as e:
            logger.warning("Guardian segment analysis failed for %s@%.1f: %s",
                           recording_id[:8], seg_start, e)
            errors.append(f"Guardian analysis failed at {seg_start:.1f}s: {str(e)[:200]}")
            continue

        for hazard in result.get("hazards", []):
            cat = _normalize_category(hazard.get("hazard_category", "other_hazard"))
            sev = _normalize_severity(hazard.get("severity", "medium"))
            conf = float(hazard.get("confidence", 0.5))
            if conf < 0.3:
                continue
            ts_sec = float(hazard.get("timestamp_seconds", seg_start))
            desc = str(hazard.get("description", "Hazard detected"))

            # Pick evidence frames: frame nearest to reported timestamp
            nearest = min(valid_ts, key=lambda t: abs(t - ts_sec))
            evidence_frames = [{
                "frame_path": frame_by_ts[nearest],
                "timestamp_seconds": nearest,
                "role": "during",
            }]

            raw_detections.append({
                "hazard_category": cat,
                "severity": sev,
                "confidence": conf,
                "timestamp_seconds": ts_sec,
                "time_range_start": seg_start,
                "time_range_end": seg_end,
                "description": desc,
                "evidence_frames": evidence_frames,
                "segment_id": segment_id,
                "is_mocked": is_mocked,
            })

    if not raw_detections:
        return 0

    merged = merge_overlapping_hazards(raw_detections)

    created = 0
    for alert in merged:
        try:
            if await upsert_guardian_alert(db_path, recording_id, alert):
                created += 1
        except Exception as e:
            logger.warning("Guardian alert upsert failed: %s", e)

    return created
