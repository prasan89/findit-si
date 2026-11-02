"""Pydantic models for FindIt / RealityTrace API requests and responses."""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, field_validator

from .config import get_settings


class ProcessingStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    INDEXED = "INDEXED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ObjectAttributes(BaseModel):
    color: Optional[str] = None
    shape: Optional[str] = None
    size: Optional[str] = None
    distinctive_details: Optional[str] = None

    model_config = {"extra": "allow"}


class RecordingUploadResponse(BaseModel):
    recording_id: str
    original_filename: str
    upload_timestamp: str
    processing_status: str
    message: str


class RecordingStatus(BaseModel):
    recording_id: str
    original_filename: str
    upload_timestamp: str
    recording_datetime: Optional[str] = None
    recording_timezone: Optional[str] = None
    camera_name: Optional[str] = None
    camera_location: Optional[str] = None
    processing_status: str
    frame_count: int = 0
    video_duration_seconds: Optional[float] = None
    video_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    processing_errors: Optional[list] = None
    indexing_status_detail: Optional[str] = None
    recording_narrative: Optional[dict] = None


class Sighting(BaseModel):
    observation_id: str
    recording_id: str
    recording_filename: str
    video_timestamp_seconds: float
    recording_datetime: Optional[str] = None
    recording_timezone: Optional[str] = None
    has_reliable_timestamp: bool = False
    object_label: str
    object_attributes: Optional[ObjectAttributes] = None
    scene_description: Optional[str] = None
    source_frame_path: str
    detection_score: float
    match_explanation: str


class SearchResult(BaseModel):
    query: str
    best_sighting: Optional[Sighting] = None
    other_sightings: list[Sighting] = []
    timestamp_ordering_reliable: bool = False
    ordering_explanation: str
    searched_recordings: int = 0
    total_candidates: int = 0


# ── RealityTrace event models ─────────────────────────────────────────────────

class EvidenceFrame(BaseModel):
    frame_path: str
    timestamp_seconds: float
    role: str  # "before" | "during" | "after"


class CrossCameraLink(BaseModel):
    target_event_id: str
    target_recording_id: str
    target_camera_name: Optional[str] = None
    confidence: float
    link_type: str  # "candidate" | "confirmed"
    explanation: str


class Event(BaseModel):
    event_id: str
    recording_id: str
    camera_id: Optional[str] = None
    camera_name: Optional[str] = None
    camera_location: Optional[str] = None
    timestamp_seconds: float
    # Phase 4: event span (group bounds); None for legacy rows.
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    timestamp_absolute: Optional[str] = None
    has_reliable_timestamp: bool = False
    event_type: str
    actor_description: Optional[str] = None
    object_descriptions: list[str] = []
    action_description: str
    scene_description: Optional[str] = None
    evidence_frames: list[EvidenceFrame] = []
    confidence: float
    observed_facts: list[str] = []
    inferred_claims: list[str] = []
    uncertainty_notes: list[str] = []
    recording_filename: str = ""
    cross_camera_links: list[CrossCameraLink] = []
    # Entity tracks across adjacent frames + timestamped action steps
    # (persisted by extraction; empty for legacy rows).
    entities: list = []
    action_sequence: list = []
    # Query-time relevance (set by investigate(); absent when unranked):
    # calibrated match strength plus the exact evidence behind it.
    relevance_score: Optional[float] = None
    matched_excerpts: list[str] = []
    primary_match: Optional[str] = None
    is_primary_finding: bool = False

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be 0.0–1.0")
        return v


# ── Temporal event-detection models ─────────────────────────────────────────

class TemporalEvidenceFrame(BaseModel):
    frame_path: str
    timestamp_seconds: float
    role: str  # "before" | "during" | "after"
    timestamp_source: str = "coarse_grid"  # "container_pts" | "coarse_grid"
    pts_seconds: Optional[float] = None


class TemporalClip(BaseModel):
    start_seconds: float
    end_seconds: float
    timestamp_source: str = "coarse_grid"
    provenance: dict = {}
    clip_url: Optional[str] = None


class TemporalEvent(BaseModel):
    event_id: str
    recording_id: str
    category: str  # possible-assault|possible-theft|confrontation|sudden-fall|suspicious-approach-departure|other-anomaly
    candidate_types: list[str] = []
    stages: list[str] = []
    start_seconds: float
    end_seconds: float
    timestamp_seconds: float
    timestamp_source: str = "coarse_grid"
    confidence: float
    confirming_frame_count: int = 0
    single_frame_candidate: bool = False
    tracks: list[dict] = []
    observable_actions: list[dict] = []
    action_sequence: list[str] = []
    confirmed: list[str] = []
    uncertain: list[str] = []
    observed_facts: list[str] = []
    evidence_frames: list[TemporalEvidenceFrame] = []
    clip: Optional[TemporalClip] = None
    provenance: dict = {}

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be 0.0–1.0")
        return v

    @field_validator("category")
    @classmethod
    def category_observation_only(cls, v: str) -> str:
        from .temporal_correlator import SEQUENCE_CATEGORIES, is_crime_verdict
        if is_crime_verdict(v) or v not in SEQUENCE_CATEGORIES:
            raise ValueError(f"category must be one of {SEQUENCE_CATEGORIES}")
        return v


class RefineRequest(BaseModel):
    start: float
    end: float
    reason: str = ""

    @field_validator("start", "end")
    @classmethod
    def times_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("refine bounds must be >= 0")
        return v


class InvestigationRequest(BaseModel):
    query: str
    recording_ids: Optional[list[str]] = None
    time_start_seconds: Optional[float] = None
    time_end_seconds: Optional[float] = None

    @field_validator("query")
    @classmethod
    def query_bounded(cls, v: str) -> str:
        try:
            max_chars = get_settings().max_query_chars
        except Exception:
            max_chars = 2000
        if len(v) > max_chars:
            raise ValueError(f"query exceeds {max_chars} characters")
        return v

    @field_validator("recording_ids")
    @classmethod
    def recording_ids_bounded(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        try:
            max_ids = get_settings().max_recording_ids
        except Exception:
            max_ids = 50
        if len(v) > max_ids:
            raise ValueError(f"recording_ids exceeds {max_ids} entries")
        return v

    @field_validator("time_start_seconds", "time_end_seconds")
    @classmethod
    def times_non_negative(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError("time bounds must be >= 0")
        return v


class MatchClassification(str, Enum):
    RELEVANT_MATCH  = "relevant_match"   # evidence clearly supports the query
    POSSIBLE_MATCH  = "possible_match"   # behaviour may be relevant but ambiguous
    NO_MATCH        = "no_match"         # no relevant activity found
    NEEDS_REVIEW    = "needs_review"     # analysis uncertain or incomplete
    ANALYSIS_FAILED = "analysis_failed"  # video could not be analysed


class VideoAnalysisSummary(BaseModel):
    recording_id: str
    filename: str
    duration_seconds: Optional[float] = None
    thumbnail_path: Optional[str] = None
    camera_name: Optional[str] = None
    camera_location: Optional[str] = None
    processing_status: str = "INDEXED"
    match_classification: MatchClassification
    observation: str
    relevant_timestamps: list[float] = []
    event_ids: list[str] = []
    confidence: Optional[float] = None


class InvestigationResult(BaseModel):
    investigation_id: str
    query: str
    events: list[Event] = []
    narrative: str = ""
    timestamp_ordering_reliable: bool = False
    ordering_explanation: str = ""
    searched_recordings: int = 0
    total_events_found: int = 0
    evidence_gaps: list[str] = []
    uncertainty_summary: str = ""
    # Phase 6: echoes the request ID assigned by middleware (observability).
    request_id: Optional[str] = None
    # Phase 8: True when this response was served from the result cache.
    cache_hit: bool = False
    # Concise one-line incident summary (primary finding first); the full
    # narrative stays in `narrative` for expandable detail.
    summary: str = ""
    # Per-video analysis summaries for the investigation results list view.
    video_summaries: list[VideoAnalysisSummary] = []
