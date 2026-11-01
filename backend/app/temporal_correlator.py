"""Temporal correlation: candidates → optional-stage sequences.

Categories are observation-only hypotheses (possible-*/confrontation/
sudden-fall/other-anomaly). A confirmed-crime label can never be produced
from vision alone: the category set contains none, and is_crime_verdict
guards every emission point.
"""
from dataclasses import dataclass, field

SEQUENCE_CATEGORIES = (
    "possible-assault",
    "possible-theft",
    "confrontation",
    "sudden-fall",
    "suspicious-approach-departure",
    "other-anomaly",
)

# Confirmed-crime verdicts that must never label vision-derived sequences.
_CRIME_VERDICTS = frozenset({
    "assault", "theft", "robbery", "murder", "homicide", "burglary",
    "kidnapping", "crime-confirmed", "guilty",
})

# Candidate type → optional sequence stage.
_STAGE_BY_CANDIDATE = {
    "sudden_approach": "approach",
    "contact_confrontation": "contact",
    "reach_neck_belongings": "contact",
    "stumble_fall": "fall",
    "rapid_departure": "departure",
    "object_removal": "removal",
}


@dataclass
class Sequence:
    category: str
    stages: list[str] = field(default_factory=list)
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    confidence: float = 0.0
    candidate_types: list[str] = field(default_factory=list)
    member_ids: list[str] = field(default_factory=list)


def is_crime_verdict(label: str) -> bool:
    """True only for confirmed-crime verdicts (never emitted)."""
    normalized = (label or "").strip().lower()
    if normalized in _CRIME_VERDICTS:
        return True
    return any(normalized == v or normalized.endswith(f"-{v}")
               for v in _CRIME_VERDICTS
               if not normalized.startswith("possible-"))


def _categorize(types: set[str], stages: set[str]) -> str:
    if "stumble_fall" in types and len(types) == 1:
        return "sudden-fall"
    if "contact_confrontation" in types or "reach_neck_belongings" in types:
        if "approach" in stages or "departure" in stages:
            return "possible-assault"
        return "confrontation"
    if "object_removal" in types:
        return "possible-theft"
    if stages >= {"approach", "departure"}:
        return "suspicious-approach-departure"
    if stages == {"approach"} or stages == {"departure"}:
        return "suspicious-approach-departure"
    return "other-anomaly"


def correlate_sequences(candidates: list, gap_seconds: float = 8.0) -> list[Sequence]:
    """Group time-adjacent candidates into sequences with optional stages.

    candidates: Candidate list (or dicts with candidate_type/start_seconds/
    end_seconds/confidence). Members within gap_seconds chain into one
    sequence; stages derive from member types (approach/contact/fall/
    departure/removal), each optional.
    """
    def _get(c, key):
        return getattr(c, key, c.get(key) if isinstance(c, dict) else None)

    ordered = sorted(candidates, key=lambda c: float(_get(c, "start_seconds")))
    groups: list[list] = []
    for cand in ordered:
        if (groups and float(_get(cand, "start_seconds"))
                - float(_get(groups[-1][-1], "end_seconds")) <= gap_seconds):
            groups[-1].append(cand)
        else:
            groups.append([cand])
    sequences = []
    for group in groups:
        types = [_get(c, "candidate_type") for c in group]
        stages: list[str] = []
        for t in types:
            stage = _STAGE_BY_CANDIDATE.get(t, "other")
            if stage not in stages:
                stages.append(stage)
        category = _categorize(set(types), set(stages))
        assert not is_crime_verdict(category), category
        confs = [float(_get(c, "confidence") or 0.0) for c in group]
        sequences.append(Sequence(
            category=category, stages=stages,
            start_seconds=float(_get(group[0], "start_seconds")),
            end_seconds=float(_get(group[-1], "end_seconds")),
            confidence=round(sum(confs) / len(confs), 3) if confs else 0.0,
            candidate_types=types,
            member_ids=[str(_get(c, "candidate_type")) for c in group],
        ))
    return sequences
