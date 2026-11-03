"""
Centralized police alert eligibility for Guardian.

Only THEFT, ASSAULT, and ABUSE incidents may enter the police notification
workflow.  All other detected incident categories are blocked regardless of
severity, confidence, or source.

Enforcement points
------------------
- Backend: check_police_eligible() called before any police-alert creation,
  dispatch, or retry.
- Frontend: guardianRouting.ts isPoliceAlert() and feedsForEvent() use the
  same canonical category set.

Normalization
-------------
Existing hazard categories and event_type strings are mapped to one of the
three canonical police categories.  Unknown, missing, or ambiguous values are
denied by default (fail-closed).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── Canonical police categories ───────────────────────────────────────────────

POLICE_ALERT_ALLOWED_CATEGORIES: frozenset[str] = frozenset({
    "THEFT",
    "ASSAULT",
    "ABUSE",
})

# ── Mapping: backend hazard_category + event_type → canonical category ────────
#
# Keys are exact strings as stored in guardian_alerts.hazard_category or
# events.event_type.  Values are canonical police categories (UPPERCASE).
# Anything not in this map returns None (ineligible).

_HAZARD_TO_POLICE_CATEGORY: dict[str, str] = {
    # Theft-class hazards (currently stored as other_hazard with theft subtext)
    # — the keywords "snatch", "theft", "robbery" in _UNUSUAL_OBS_HAZARD_MAP
    # all produce other_hazard; we handle the override via description keywords
    # in normalize_incident_category().
}

_EVENT_TYPE_TO_POLICE_CATEGORY: dict[str, str] = {
    # THEFT
    "possible-theft":   "THEFT",
    "robbery":          "THEFT",
    "chain-snatching":  "THEFT",
    "vehicle-theft":    "THEFT",
    "carjacking":       "THEFT",
    # ASSAULT
    "possible-assault": "ASSAULT",
    "confrontation":    "ASSAULT",
    "dangerous-threat": "ASSAULT",
    "armed-person":     "ASSAULT",
    # ABUSE (no current backend event types; reserved for future detection)
}

# Description-level keyword hints used when the hazard_category alone is
# ambiguous (e.g. "other_hazard" covers many things).
_THEFT_DESCRIPTION_KEYWORDS: frozenset[str] = frozenset({
    "snatch", "snatching", "theft", "steal", "stolen", "rob", "robbery",
    "pickpocket", "loot", "looting",
    # Chain snatching and bag grab patterns the LLM may describe indirectly
    "chain", "necklace", "bag grab", "purse", "handbag",
})

_ASSAULT_DESCRIPTION_KEYWORDS: frozenset[str] = frozenset({
    "assault", "attack", "attacked", "punch", "hit", "strike", "beat",
    "struggle", "grab", "grabbed", "violence", "violent", "shove",
    "confront", "confrontation",
    # Physical altercations the LLM describes without crime vocabulary
    "physical interaction", "physical altercation", "physical contact",
    "arm movement", "arm movements",
})

_ABUSE_DESCRIPTION_KEYWORDS: frozenset[str] = frozenset({
    "abuse", "abused", "harass", "harassment", "coerce", "coercion",
    "intimidate", "intimidation", "bully", "bullying", "torment",
})


def normalize_incident_category(
    hazard_category: str | None = None,
    event_type: str | None = None,
    description: str | None = None,
) -> str | None:
    """Return a canonical police category string, or None if ineligible.

    Checks in order:
    1. event_type exact match (most specific)
    2. hazard_category exact match
    3. Description keyword scan for other_hazard (narrows broad category)
    4. Returns None (denied by default)
    """
    # 1. Event type exact match
    if event_type:
        canonical = _EVENT_TYPE_TO_POLICE_CATEGORY.get(event_type)
        if canonical:
            return canonical

    # 2. Hazard category exact match
    if hazard_category:
        canonical = _HAZARD_TO_POLICE_CATEGORY.get(hazard_category)
        if canonical:
            return canonical

        # 3. For other_hazard, scan description keywords
        if hazard_category == "other_hazard" and description:
            desc_lower = description.lower()
            if any(kw in desc_lower for kw in _THEFT_DESCRIPTION_KEYWORDS):
                return "THEFT"
            if any(kw in desc_lower for kw in _ASSAULT_DESCRIPTION_KEYWORDS):
                return "ASSAULT"
            if any(kw in desc_lower for kw in _ABUSE_DESCRIPTION_KEYWORDS):
                return "ABUSE"

    # 4. Unknown / ambiguous → denied
    return None


def is_police_alert_eligible(
    hazard_category: str | None = None,
    event_type: str | None = None,
    description: str | None = None,
) -> bool:
    """Return True only if the incident maps to THEFT, ASSAULT, or ABUSE.

    Fail-closed: any unknown, missing, or ambiguous category returns False.
    Does NOT consider severity — severity alone cannot grant police eligibility.
    """
    canonical = normalize_incident_category(
        hazard_category=hazard_category,
        event_type=event_type,
        description=description,
    )
    return canonical in POLICE_ALERT_ALLOWED_CATEGORIES


# ── Owner alert eligibility ───────────────────────────────────────────────────
#
# The owner feed shows property hazard and security events.
# Routine activity (normal visitors, normal motion, ordinary pet animals) is
# excluded at the detection layer (guardian.py) — only genuinely dangerous
# animal behavior produces a dangerous_animal alert in the first place.

# Hazard categories that are always owner-eligible
OWNER_ELIGIBLE_HAZARD_CATEGORIES: frozenset[str] = frozenset({
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

# Event types that are owner-eligible (security/safety concern)
OWNER_ELIGIBLE_EVENT_TYPES: frozenset[str] = frozenset({
    # Police-eligible (also reach owner)
    "possible-theft",
    "robbery",
    "chain-snatching",
    "vehicle-theft",
    "carjacking",
    "possible-assault",
    "confrontation",
    "dangerous-threat",
    "armed-person",
    # Security/safety events (owner only)
    "suspicious-approach-departure",
    "sudden-fall",
    "other-anomaly",
})

# Event types that are NEVER owner-eligible (routine activity)
OWNER_ROUTINE_EVENT_TYPES: frozenset[str] = frozenset({
    "person_detected",
    "vehicle_detected",
    "narrative_step",
    "motion_detected",
})


def is_owner_alert_eligible(
    hazard_category: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
) -> bool:
    """Return True if the incident should appear in the Property Owner feed.

    Routine detections (normal visitors, normal motion, normal vehicles,
    harmless activity) return False.  Recognized hazard categories and
    security event types return True.

    Unknown / ambiguous inputs return False (fail-closed for both feeds).
    """
    # 1. Recognized hazard categories → always owner-eligible
    if hazard_category and hazard_category in OWNER_ELIGIBLE_HAZARD_CATEGORIES:
        return True

    # 2. Explicitly excluded routine event types → never owner-eligible
    if event_type and event_type in OWNER_ROUTINE_EVENT_TYPES:
        return False

    # 3. Recognized security event types → owner-eligible
    if event_type and event_type in OWNER_ELIGIBLE_EVENT_TYPES:
        return True

    # 4. Unknown / ambiguous → blocked
    return False
