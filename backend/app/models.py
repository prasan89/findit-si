"""Pydantic models for FindIt API requests and responses."""
from enum import Enum
from typing import Optional

from pydantic import BaseModel


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
    processing_status: str
    frame_count: int = 0
    video_duration_seconds: Optional[float] = None
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
