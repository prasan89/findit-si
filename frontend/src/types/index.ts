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

export interface TrackedEntity {
  entity_id: string
  head?: string
  display_label?: string
  first_seen_seconds?: number
  last_seen_seconds?: number
  frame_count?: number
  observation_count?: number
  representative_frame?: string | null
}

export interface ActionStep {
  step: number
  start_seconds?: number | null
  end_seconds?: number | null
  description: string
  frame_path?: string | null
}

export interface Event {
  event_id: string
  recording_id: string
  camera_id?: string
  camera_name?: string
  camera_location?: string
  timestamp_seconds: number
  start_seconds?: number | null
  end_seconds?: number | null
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
  entities?: TrackedEntity[]
  action_sequence?: ActionStep[]
  relevance_score?: number | null
  matched_excerpts?: string[]
  primary_match?: string | null
  is_primary_finding?: boolean
}

export interface InvestigationRequest {
  query: string
  recording_ids?: string[]
  time_start_seconds?: number
  time_end_seconds?: number
}

export type MatchClassification =
  | 'relevant_match'
  | 'possible_match'
  | 'no_match'
  | 'needs_review'
  | 'analysis_failed'

export interface VideoAnalysisSummary {
  recording_id: string
  filename: string
  duration_seconds?: number | null
  thumbnail_path?: string | null
  camera_name?: string | null
  camera_location?: string | null
  processing_status: string
  match_classification: MatchClassification
  observation: string
  relevant_timestamps: number[]
  event_ids: string[]
  confidence?: number | null
}

export interface InvestigationResult {
  investigation_id: string
  query: string
  events: Event[]
  narrative: string
  summary?: string
  timestamp_ordering_reliable: boolean
  ordering_explanation: string
  searched_recordings: number
  total_events_found: number
  evidence_gaps: string[]
  uncertainty_summary: string
  video_summaries?: VideoAnalysisSummary[]
}

// ── Temporal (incident timeline) types ───────────────────────────────────────
// Mirrors backend/app/models.py TemporalEvent / TemporalEvidenceFrame / TemporalClip.

export interface TemporalEvidenceFrame {
  frame_path: string
  timestamp_seconds: number
  role: string // "before" | "during" | "after"
  timestamp_source: string // "container_pts" | "coarse_grid"
  pts_seconds?: number | null
}

export interface TemporalClip {
  start_seconds: number
  end_seconds: number
  timestamp_source: string
  provenance: Record<string, unknown>
  clip_url?: string | null
}

export interface TemporalEvent {
  event_id: string
  recording_id: string
  category: string
  candidate_types: string[]
  stages: string[]
  start_seconds: number
  end_seconds: number
  timestamp_seconds: number
  timestamp_source: string
  confidence: number
  confirming_frame_count: number
  single_frame_candidate: boolean
  tracks: Record<string, unknown>[]
  observable_actions: Record<string, unknown>[]
  action_sequence: string[]
  confirmed: string[]
  uncertain: string[]
  observed_facts: string[]
  evidence_frames: TemporalEvidenceFrame[]
  clip?: TemporalClip | null
  provenance: Record<string, unknown>
}

export interface TemporalEventsResponse {
  recording_id: string
  events: TemporalEvent[]
  count: number
  timestamp_source: string
  filters: {
    start?: number | null
    end?: number | null
    min_confidence?: number | null
    include_single_frame: boolean
  }
}

// ── Phase 3: Multi-agent investigation types ─────────────────────────────────

export type AgentRunStatus = 'pending' | 'running' | 'completed' | 'skipped' | 'failed' | 'mocked' | 'timed_out'

export interface AgentRunResult {
  agent_name: string
  status: AgentRunStatus
  duration_ms: number
  tokens_used: number
  cost_usd: number
  error?: string
  output?: Record<string, unknown>
}

export interface FinalReport {
  narrative: string
  key_findings: string[]
  uncertainty_notes: string[]
  evidence_citations: string[]
  model_used: string
  agents_run: string[]
}

export interface MultiAgentInvestigationResult {
  trace_id: string
  investigation_id: string
  query: string
  agents: AgentRunResult[]
  final_report: FinalReport | null
  total_cost_usd: number
  total_duration_ms: number
  status: string
}

export type HazardCategory =
  | 'fire_smoke'
  | 'person_fall_injury'
  | 'vehicle_collision'
  | 'dangerous_animal'
  | 'weapon_visible'
  | 'restricted_area_entry'
  | 'other_hazard'

export type AlertSeverity = 'critical' | 'high' | 'medium' | 'low'
export type AlertReviewStatus = 'new' | 'acknowledged' | 'dismissed'

export interface GuardianAlert {
  alert_id: string
  recording_id: string
  hazard_category: HazardCategory
  severity: AlertSeverity
  confidence: number
  timestamp_seconds: number
  time_range_start: number
  time_range_end: number
  description: string
  evidence_frames: EvidenceFrame[]
  supporting_segment_ids: string[]
  review_status: AlertReviewStatus
  is_mocked: boolean
  created_at: string
  updated_at: string
  original_filename?: string
  camera_name?: string
  camera_location?: string
}

export interface GuardianAlertsResponse {
  recording_id: string
  alerts: GuardianAlert[]
  count: number
}
