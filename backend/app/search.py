"""Cross-video search: find the most recent credible sighting of an object."""
import json
import re
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from .database import get_db
from .models import ObjectAttributes, SearchResult, Sighting

# Minimum score to consider a match credible
MIN_SCORE_THRESHOLD = 0.3
# Group observations within this many seconds as the same sighting
GROUPING_WINDOW_SECONDS = 5.0


async def search_observations(query: str, db_path: str) -> SearchResult:
    """
    Search all indexed recordings for observations matching the query.
    Returns the most recent credible sighting and alternatives.
    """
    query_terms = _tokenize(query)

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Get all eligible recordings
        eligible_rows = await (await db.execute(
            """SELECT recording_id, original_filename, recording_datetime, recording_timezone
               FROM recordings
               WHERE processing_status IN ('INDEXED', 'PARTIAL')
               ORDER BY upload_timestamp"""
        )).fetchall()

        if not eligible_rows:
            return SearchResult(
                query=query,
                best_sighting=None,
                other_sightings=[],
                timestamp_ordering_reliable=False,
                ordering_explanation="No indexed recordings found.",
                searched_recordings=0,
                total_candidates=0,
            )

        eligible_ids = [r['recording_id'] for r in eligible_rows]
        recording_meta = {
            r['recording_id']: {
                'filename': r['original_filename'],
                'recording_datetime': r['recording_datetime'],
                'recording_timezone': r['recording_timezone'],
            }
            for r in eligible_rows
        }

        # Fetch all observations from eligible recordings
        placeholders = ','.join('?' * len(eligible_ids))
        obs_rows = await (await db.execute(
            f"""SELECT o.*, r.original_filename
                FROM observations o
                JOIN recordings r ON o.recording_id = r.recording_id
                WHERE o.recording_id IN ({placeholders})
                ORDER BY o.recording_id, o.video_timestamp_seconds""",
            eligible_ids
        )).fetchall()

    if not obs_rows:
        return SearchResult(
            query=query,
            best_sighting=None,
            other_sightings=[],
            timestamp_ordering_reliable=False,
            ordering_explanation=f"No observations found in {len(eligible_ids)} indexed recording(s).",
            searched_recordings=len(eligible_ids),
            total_candidates=0,
        )

    # Score each observation
    scored = []
    for row in obs_rows:
        attrs = json.loads(row['object_attributes']) if row['object_attributes'] else {}
        score = _relevance_score(query_terms, row['object_label'], attrs, row['scene_description'])
        if score >= MIN_SCORE_THRESHOLD:
            scored.append({
                'observation_id': row['observation_id'],
                'recording_id': row['recording_id'],
                'recording_filename': row['original_filename'],
                'video_timestamp_seconds': row['video_timestamp_seconds'],
                'recording_datetime': row['recording_datetime'],
                'recording_timezone': row['recording_timezone'],
                'object_label': row['object_label'],
                'object_attributes': attrs,
                'scene_description': row['scene_description'],
                'source_frame_path': row['source_frame_path'],
                'detection_score': row['detection_score'],
                'relevance_score': score,
                'combined_score': (score * 0.7) + (row['detection_score'] * 0.3),
            })

    if not scored:
        return SearchResult(
            query=query,
            best_sighting=None,
            other_sightings=[],
            timestamp_ordering_reliable=False,
            ordering_explanation=f"No matching observations found across {len(eligible_ids)} indexed recording(s).",
            searched_recordings=len(eligible_ids),
            total_candidates=0,
        )

    # Group nearby observations within each recording
    grouped = _group_observations(scored)

    # Determine if we have reliable timestamps across all recordings
    all_have_timestamps = all(
        recording_meta[g['recording_id']]['recording_datetime'] is not None
        for g in grouped
    )
    any_have_timestamps = any(
        recording_meta[g['recording_id']]['recording_datetime'] is not None
        for g in grouped
    )

    # Sort: if all have reliable timestamps, sort by absolute time desc
    # Otherwise, sort within each recording by video_timestamp_seconds
    if all_have_timestamps:
        def sort_key(g):
            return _to_sort_timestamp(g['recording_datetime'], g['video_timestamp_seconds'])
        grouped.sort(key=sort_key, reverse=True)
        ordering_explanation = "Results ordered by recording date and time (reliable timestamps available for all recordings)."
        timestamp_ordering_reliable = True
    elif any_have_timestamps:
        # Sort those with timestamps first (most recent), then those without
        with_ts = [g for g in grouped if g['recording_datetime'] is not None]
        without_ts = [g for g in grouped if g['recording_datetime'] is None]
        with_ts.sort(key=lambda g: _to_sort_timestamp(g['recording_datetime'], g['video_timestamp_seconds']), reverse=True)
        without_ts.sort(key=lambda g: g['combined_score'], reverse=True)
        grouped = with_ts + without_ts
        ordering_explanation = (
            "Some recordings have reliable timestamps and are ordered chronologically. "
            "Recordings without timestamps are grouped separately and cannot be placed in chronological order."
        )
        timestamp_ordering_reliable = False
    else:
        grouped.sort(key=lambda g: g['combined_score'], reverse=True)
        ordering_explanation = (
            "No recording timestamps available. Results are ordered by match confidence only. "
            "Cross-video chronological ordering is unavailable without recording dates."
        )
        timestamp_ordering_reliable = False

    def to_sighting(g: dict) -> Sighting:
        attrs = g['object_attributes']
        if isinstance(attrs, dict):
            attrs = ObjectAttributes(**{k: v for k, v in attrs.items() if k in ObjectAttributes.model_fields})
        return Sighting(
            observation_id=g['observation_id'],
            recording_id=g['recording_id'],
            recording_filename=g['recording_filename'],
            video_timestamp_seconds=g['video_timestamp_seconds'],
            recording_datetime=g['recording_datetime'],
            recording_timezone=g['recording_timezone'],
            has_reliable_timestamp=g['recording_datetime'] is not None,
            object_label=g['object_label'],
            object_attributes=attrs,
            scene_description=g.get('scene_description'),
            source_frame_path=g['source_frame_path'],
            detection_score=g['combined_score'],
            match_explanation=_explain_match(query_terms, g),
        )

    sightings = [to_sighting(g) for g in grouped]

    return SearchResult(
        query=query,
        best_sighting=sightings[0] if sightings else None,
        other_sightings=sightings[1:6],  # up to 5 alternatives
        timestamp_ordering_reliable=timestamp_ordering_reliable,
        ordering_explanation=ordering_explanation,
        searched_recordings=len(eligible_ids),
        total_candidates=len(scored),
    )


def _tokenize(query: str) -> list[str]:
    """Lowercase and split query into meaningful tokens."""
    words = re.findall(r'[a-z0-9]+', query.lower())
    stopwords = {'where', 'when', 'did', 'i', 'last', 'see', 'my', 'the', 'a', 'an',
                 'find', 'was', 'is', 'seen', 'more', 'recently', 'than', 'every',
                 'video', 'appears', 'are', 'there', 'any'}
    return [w for w in words if w not in stopwords]


def _relevance_score(terms: list[str], label: str, attrs: dict, scene: Optional[str]) -> float:
    """Score how relevant an observation is to the search terms."""
    if not terms:
        return 1.0

    searchable = ' '.join([
        label.lower(),
        ' '.join(str(v) for v in attrs.values() if v),
        (scene or '').lower(),
    ])

    hits = sum(1 for t in terms if t in searchable)
    # Bonus for label exact match
    label_lower = label.lower()
    label_hits = sum(1 for t in terms if t in label_lower)

    score = (hits / len(terms)) * 0.6 + (label_hits / len(terms)) * 0.4
    return min(score, 1.0)


def _group_observations(scored: list[dict]) -> list[dict]:
    """
    Within each recording, merge observations within GROUPING_WINDOW_SECONDS.
    Keep the representative observation with highest combined_score.
    """
    by_recording: dict[str, list[dict]] = {}
    for obs in scored:
        rid = obs['recording_id']
        by_recording.setdefault(rid, []).append(obs)

    grouped = []
    for rid, obs_list in by_recording.items():
        obs_list.sort(key=lambda o: o['video_timestamp_seconds'])
        merged = []
        current_group = [obs_list[0]]

        for obs in obs_list[1:]:
            if obs['video_timestamp_seconds'] - current_group[-1]['video_timestamp_seconds'] <= GROUPING_WINDOW_SECONDS:
                current_group.append(obs)
            else:
                merged.append(max(current_group, key=lambda o: o['combined_score']))
                current_group = [obs]
        merged.append(max(current_group, key=lambda o: o['combined_score']))
        grouped.extend(merged)

    return grouped


def _to_sort_timestamp(recording_datetime: Optional[str], video_ts: float) -> float:
    """
    Convert recording_datetime + video_ts to a sortable float (unix timestamp + offset).
    Falls back to 0 if recording_datetime is invalid/None.
    """
    if not recording_datetime:
        return 0.0
    try:
        dt = datetime.fromisoformat(recording_datetime)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() + video_ts
    except Exception:
        return 0.0


def _explain_match(terms: list[str], g: dict) -> str:
    """Generate a human-readable explanation of why this observation matches."""
    parts = []
    label = g['object_label']
    attrs = g['object_attributes'] if isinstance(g['object_attributes'], dict) else {}
    scene = g.get('scene_description', '')

    matched_in_label = [t for t in terms if t in label.lower()]
    matched_in_attrs = [t for t in terms if any(t in str(v).lower() for v in attrs.values())]

    if matched_in_label:
        parts.append(f"Object identified as '{label}'")
    if attrs.get('color'):
        parts.append(f"color: {attrs['color']}")
    if attrs.get('shape'):
        parts.append(f"shape: {attrs['shape']}")
    if attrs.get('distinctive_details'):
        parts.append(f"details: {attrs['distinctive_details']}")
    if scene:
        parts.append(f"Scene: {scene[:100]}")

    ts = g['video_timestamp_seconds']
    m, s = divmod(int(ts), 60)
    h, m = divmod(m, 60)
    ts_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    parts.append(f"at video timestamp {ts_str}")

    score = g['combined_score']
    confidence = "high" if score >= 0.7 else "medium" if score >= 0.5 else "low"
    parts.append(f"({confidence} confidence, score: {score:.2f})")

    return ". ".join(parts) + "."
