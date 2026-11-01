"""Candidate detection: observable-action signals → typed candidates.

Types describe visible motion/posture only (approach, contact, reaching,
falling, departure, object removal). No crime language is emitted here;
verdicts are impossible by construction — see temporal_correlator.
"""
import re
from dataclasses import dataclass, field

CANDIDATE_TYPES = (
    "sudden_approach",
    "contact_confrontation",
    "reach_neck_belongings",
    "stumble_fall",
    "rapid_departure",
    "object_removal",
)

# Generic observable-action words only. Each entry maps a candidate type to
# word-boundary patterns over "<label> <scene_description>" text.
_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "sudden_approach": (
        r"rush(?:ing|ed)?\s+toward", r"run(?:ning)?\s+toward",
        r"approach(?:ing|ed)?\s+(?:quickly|suddenly|rapidly)",
        r"lung(?:ing|ed)?\s+toward", r"charg(?:ing|ed)?\s+toward",
        r"mov(?:ing|ed)?\s+toward\s+.*quickly",
        r"advanc(?:ing|ed)?\s+(?:quickly|suddenly|rapidly)",
        # Neutral-dynamic phrasings the vision model emits for real
        # approaches ("motorcycle moving toward foreground"). Direction
        # qualifiers keep precision: static "facing toward" never fires.
        r"mov(?:ing|ed)?\s+toward", r"walk(?:ing|ed)?\s+toward",
        r"driv(?:ing|e)?\s+toward", r"rid(?:ing|e)?\s+toward",
        r"com(?:ing|e)?\s+toward", r"advanc(?:ing|ed)?\s+toward",
    ),
    "contact_confrontation": (
        r"grab(?:bing|bed)?", r"push(?:ing|ed)?", r"shov(?:ing|ed)?",
        r"wrestl(?:ing|ed)?", r"grappl(?:ing|ed)?",
        r"holding\s+.*(?:tightly|forcefully)",
        r"close\s+together.*(?:struggl|fight)",
        r"physical\s+contact",
        # Observable struggle/reaction cues ("victim with arms raised
        # toward motorcycle", "reaching toward the other person").
        # Physical-action language only — never a verdict.
        r"arms?\s+raised\s+toward", r"reach(?:ing|ed)?\s+(?:toward|for|out)",
        r"struggl(?:ing|ed|e)?",
    ),
    "reach_neck_belongings": (
        r"reach(?:ing|ed)?\s+toward\s+.*neck",
        r"hand\s+.*(?:neck|throat)",
        r"reach(?:ing|ed)?\s+.*(?:bag|belongings|pocket|necklace|backpack)",
        r"grasp(?:ing|ed)?\s+.*(?:neck|chain|strap)",
    ),
    "stumble_fall": (
        r"trip(?:ping|ped)?", r"stumbl(?:ing|ed)?",
        r"collaps(?:ing|ed)?", r"fall(?:ing|en)?\s+(?:to|on|onto|down)",
        r"lying\s+on\s+(?:the\s+)?(?:ground|floor)",
        r"los(?:ing|t)\s+balance",
    ),
    "rapid_departure": (
        r"sprint(?:ing|ed)?\s+away", r"run(?:ning)?\s+away",
        r"flee(?:ing)?", r"dash(?:ing|ed)?\s+away",
        r"leav(?:ing|es)?\s+(?:quickly|rapidly|hurriedly)",
        r"hurry(?:ing)?\s+(?:away|off|out)",
        # Neutral-dynamic departures ("motorcycle moving away from
        # camera"). Bare "walking away" needs no qualifier here because
        # departure alone only ever yields a low-confidence
        # suspicious-approach-departure candidate, never a verdict.
        r"mov(?:ing|ed)?\s+away", r"walk(?:ing|ed)?\s+away",
        r"driv(?:ing|e)?\s+away", r"rid(?:ing|e)?\s+away",
        r"leav(?:ing|es)?\s+(?:the\s+)?(?:frame|scene|area|camera)",
    ),
    "object_removal": (
        r"being\s+taken", r"taken\s+from",
        r"tak(?:ing|en|e)\s+.*(?:bag|belongings|package|luggage|backpack|object)",
        r"remov(?:ing|ed|e)\s+.*(?:bag|object|item|package)",
        r"pick(?:ing|ed)?\s+up\s+.*(?:bag|belongings|package|luggage)",
        r"carrying\s+away",
        r"snatch(?:ing|ed)?",
    ),
}

_COMPILED: dict[str, list] = {
    ctype: [re.compile(p, re.IGNORECASE) for p in pats]
    for ctype, pats in _SIGNAL_PATTERNS.items()
}


@dataclass
class Candidate:
    candidate_type: str
    start_seconds: float
    end_seconds: float
    confidence: float
    signals: tuple = ()
    observation_ids: tuple = ()
    single_frame_candidate: bool = False
    motion_window: bool = False


def _match_types(text: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for ctype, patterns in _COMPILED.items():
        for pat in patterns:
            m = pat.search(text)
            if m:
                hits.setdefault(ctype, []).append(m.group(0)[:120])
    return hits


def detect_candidates(
    observations: list[dict],
    motion_windows: list | None = None,
    merge_gap: float = 2.0,
) -> list[Candidate]:
    """Type observations into candidates; merge adjacent same-type hits.

    observations: dicts with video_timestamp_seconds, object_label,
    scene_description, detection_score, observation_id.
    motion_windows: optional CandidateWindow list — a window overlapping a
    candidate span raises confidence and sets motion_window=True.
    """
    motion_windows = motion_windows or []
    typed: list[Candidate] = []
    for obs in observations:
        text = f"{obs.get('object_label', '')} {obs.get('scene_description', '')}"
        for ctype, signals in _match_types(text).items():
            ts = float(obs.get("video_timestamp_seconds", 0.0))
            base = float(obs.get("detection_score", 0.5))
            typed.append(Candidate(
                candidate_type=ctype, start_seconds=ts, end_seconds=ts,
                confidence=round(min(0.95, 0.35 + 0.5 * base), 3),
                signals=tuple(signals),
                observation_ids=(obs.get("observation_id", ""),),
                single_frame_candidate=True))

    typed.sort(key=lambda c: (c.candidate_type, c.start_seconds))
    merged: list[Candidate] = []
    for cand in typed:
        if (merged and merged[-1].candidate_type == cand.candidate_type
                and cand.start_seconds - merged[-1].end_seconds <= merge_gap):
            prev = merged[-1]
            merged[-1] = Candidate(
                candidate_type=prev.candidate_type,
                start_seconds=prev.start_seconds,
                end_seconds=cand.end_seconds,
                confidence=round(max(prev.confidence, cand.confidence), 3),
                signals=prev.signals + cand.signals,
                observation_ids=prev.observation_ids + cand.observation_ids,
                single_frame_candidate=False)
        else:
            merged.append(cand)

    for i, cand in enumerate(merged):
        for w in motion_windows:
            ws = getattr(w, "start_seconds", w[0] if isinstance(w, (list, tuple)) else 0)
            we = getattr(w, "end_seconds", w[1] if isinstance(w, (list, tuple)) else 0)
            if ws <= cand.end_seconds and we >= cand.start_seconds:
                merged[i] = Candidate(
                    candidate_type=cand.candidate_type,
                    start_seconds=cand.start_seconds,
                    end_seconds=cand.end_seconds,
                    confidence=round(min(0.95, cand.confidence + 0.1), 3),
                    signals=cand.signals, observation_ids=cand.observation_ids,
                    single_frame_candidate=cand.single_frame_candidate,
                    motion_window=True)
                break
    # Single-frame candidates are kept but capped below 0.6 (low-confidence).
    capped = []
    for cand in merged:
        conf = min(cand.confidence, 0.55) if cand.single_frame_candidate else cand.confidence
        capped.append(Candidate(
            candidate_type=cand.candidate_type,
            start_seconds=cand.start_seconds, end_seconds=cand.end_seconds,
            confidence=conf, signals=cand.signals,
            observation_ids=cand.observation_ids,
            single_frame_candidate=cand.single_frame_candidate,
            motion_window=cand.motion_window))
    return sorted(capped, key=lambda c: c.start_seconds)
