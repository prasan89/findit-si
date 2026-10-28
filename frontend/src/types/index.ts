export type ProcessingStatus = 'UPLOADED' | 'PROCESSING' | 'INDEXED' | 'PARTIAL' | 'FAILED' | 'PENDING'

export interface ObjectAttributes {
  color?: string
  shape?: string
  size?: string
  distinctive_details?: string
}

export interface Recording {
  recording_id: string
  original_filename: string
  safe_filename?: string
  upload_timestamp: string
  recording_datetime?: string
  recording_timezone?: string
  camera_name?: string
  camera_location?: string
  video_duration_seconds?: number
  processing_status: ProcessingStatus
  indexing_status_detail?: string
  frame_count: number
  processing_errors?: string[] | null
  video_path?: string
  thumbnail_path?: string
  file_size_bytes?: number
}

export interface Sighting {
  observation_id: string
  recording_id: string
  recording_filename: string
  video_timestamp_seconds: number
  recording_datetime?: string
  recording_timezone?: string
  has_reliable_timestamp: boolean
  object_label: string
  object_attributes: ObjectAttributes
  scene_description?: string
  source_frame_path: string
  detection_score: number
  match_explanation: string
}

export interface SearchResult {
  query: string
  best_sighting?: Sighting
  other_sightings: Sighting[]
  timestamp_ordering_reliable: boolean
  ordering_explanation: string
  searched_recordings: number
  total_candidates: number
}

// ── RealityTrace investigation types ─────────────────────────────────────────

export interface EvidenceFrame {
  frame_path: string
  timestamp_seconds: number
  role: 'before' | 'during' | 'after'
}

export interface CrossCameraLink {
  target_event_id: string
  target_recording_id: string
  target_camera_name?: string
  confidence: number
  link_type: 'candidate' | 'confirmed'
  explanation: string
}

export interface Event {
  event_id: string
  recording_id: string
  camera_id?: string
  camera_name?: string
  camera_location?: string
  timestamp_seconds: number
  timestamp_absolute?: string
  has_reliable_timestamp: boolean
  event_type: string
  actor_description?: string
  object_descriptions: string[]
  action_description: string
  scene_description?: string
  evidence_frames: EvidenceFrame[]
  confidence: number
  observed_facts: string[]
  inferred_claims: string[]
  uncertainty_notes: string[]
  recording_filename: string
  cross_camera_links: CrossCameraLink[]
}

export interface InvestigationRequest {
  query: string
  recording_ids?: string[]
  time_start_seconds?: number
  time_end_seconds?: number
}

export interface InvestigationResult {
  investigation_id: string
  query: string
  events: Event[]
  narrative: string
  timestamp_ordering_reliable: boolean
  ordering_explanation: string
  searched_recordings: number
  total_events_found: number
  evidence_gaps: string[]
  uncertainty_summary: string
}
