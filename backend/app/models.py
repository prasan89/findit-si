"""Pydantic models for FindIt / RealityTrace API requests and responses."""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, field_validator


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

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be 0.0–1.0")
        return v


class InvestigationRequest(BaseModel):
    query: str
    recording_ids: Optional[list[str]] = None
    time_start_seconds: Optional[float] = None
    time_end_seconds: Optional[float] = None


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
