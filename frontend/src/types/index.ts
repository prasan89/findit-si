export type ProcessingStatus = 'UPLOADED' | 'PROCESSING' | 'INDEXED' | 'PARTIAL' | 'FAILED'

export interface ObjectAttributes {
  color?: string
  shape?: string
  size?: string
  distinctive_details?: string
}

export interface Recording {
  recording_id: string
  original_filename: string
  safe_filename: string
  upload_timestamp: string
  recording_datetime?: string
  recording_timezone?: string
  video_duration_seconds?: number
  processing_status: ProcessingStatus
  indexing_status_detail?: string
  frame_count: number
  processing_errors: string[]
  video_path: string
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
