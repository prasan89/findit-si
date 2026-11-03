import type { GuardianAlert, Event, Recording, AlertReviewStatus } from '@/types'

// ── Feed types ────────────────────────────────────────────────────────────────

export type FeedTarget = 'police' | 'owner'
export type SeverityTag = 'Critical' | 'Review' | 'Routine'

export type FeedItemSource = 'alert' | 'event'

export interface FeedItem {
  id: string
  source: FeedItemSource
  recording_id: string
  timestamp_seconds: number
  time_range_start: number
  time_range_end: number
  category: string          // display label key
  description: string
  confidence: number
  evidence_frames: import('@/types').EvidenceFrame[]
  review_status: AlertReviewStatus
  is_mocked: boolean
  feeds: FeedTarget[]
  severityTag: SeverityTag
  // Original items for detail panel
  alert?: GuardianAlert
  event?: Event
  // Camera info
  camera_name?: string
  camera_location?: string
  recording_filename?: string
}

// ── Serious event types that can reach the police feed ────────────────────────

const POLICE_EVENT_TYPES = new Set([
  'possible-theft',
  'possible-assault',
  'confrontation',
])

// ── Alert hazard categories that reach the police feed ───────────────────────

function isPoliceAlert(alert: GuardianAlert): boolean {
  const { hazard_category: cat, severity: sev } = alert
  if (cat === 'weapon_visible') return true
  if (cat === 'fire_smoke' && (sev === 'critical' || sev === 'high')) return true
  if (cat === 'dangerous_animal' && (sev === 'critical' || sev === 'high')) return true
  if (cat === 'other_hazard' && (sev === 'critical' || sev === 'high')) return true
  return false
}

export function feedsForAlert(alert: GuardianAlert): FeedTarget[] {
  return isPoliceAlert(alert) ? ['police', 'owner'] : ['owner']
}

export function feedsForEvent(ev: Event): FeedTarget[] {
  if (POLICE_EVENT_TYPES.has(ev.event_type) && ev.confidence >= 0.6) {
    return ['police', 'owner']
  }
  // All events reach owner
  return ['owner']
}

// ── Severity tag ──────────────────────────────────────────────────────────────

export function severityTagForAlert(alert: GuardianAlert): SeverityTag {
  if (alert.severity === 'critical' || alert.severity === 'high') return 'Critical'
  if (alert.severity === 'medium') return 'Review'
  return 'Routine'
}

export function severityTagForEvent(ev: Event): SeverityTag {
  if (POLICE_EVENT_TYPES.has(ev.event_type)) {
    if (ev.confidence >= 0.75) return 'Critical'
    if (ev.confidence >= 0.6) return 'Review'
    return 'Review'
  }
  if (ev.event_type === 'suspicious-approach-departure') return 'Review'
  if (ev.event_type === 'sudden-fall') return 'Review'
  if (ev.event_type === 'other-anomaly') return 'Review'
  return 'Routine'
}

// ── Deduplication ─────────────────────────────────────────────────────────────
//
// If an alert and an event share the same recording_id and their timestamps
// are within 5 seconds, they describe the same incident. Collapse to one FeedItem:
// - alert wins for police routing and description
// - event's action_description used if alert has no description
// Events that don't overlap any alert are kept as standalone items.

export function deduplicateItems(
  alerts: GuardianAlert[],
  events: Event[],
  recordings: Recording[],
): FeedItem[] {
  const recMap = new Map(recordings.map(r => [r.recording_id, r]))

  const items: FeedItem[] = []
  const usedEventIds = new Set<string>()

  // First pass: alerts, absorbing nearby events
  for (const alert of alerts) {
    const rec = recMap.get(alert.recording_id)
    const matchingEvent = events.find(
      ev =>
        ev.recording_id === alert.recording_id &&
        Math.abs(ev.timestamp_seconds - alert.timestamp_seconds) < 5 &&
        !usedEventIds.has(ev.event_id),
    )
    if (matchingEvent) usedEventIds.add(matchingEvent.event_id)

    const feeds = feedsForAlert(alert)
    const tag = severityTagForAlert(alert)

    items.push({
      id: `alert-${alert.alert_id}`,
      source: 'alert',
      recording_id: alert.recording_id,
      timestamp_seconds: alert.timestamp_seconds,
      time_range_start: alert.time_range_start,
      time_range_end: alert.time_range_end,
      category: alert.hazard_category,
      description: alert.description || matchingEvent?.action_description || '',
      confidence: alert.confidence,
      evidence_frames: alert.evidence_frames,
      review_status: alert.review_status,
      is_mocked: alert.is_mocked,
      feeds,
      severityTag: tag,
      alert,
      event: matchingEvent,
      camera_name: alert.camera_name ?? rec?.camera_name,
      camera_location: alert.camera_location ?? rec?.camera_location,
      recording_filename: alert.original_filename ?? rec?.original_filename,
    })
  }

  // Second pass: events not absorbed by an alert
  for (const ev of events) {
    if (usedEventIds.has(ev.event_id)) continue
    const rec = recMap.get(ev.recording_id)
    const feeds = feedsForEvent(ev)
    const tag = severityTagForEvent(ev)

    items.push({
      id: `event-${ev.event_id}`,
      source: 'event',
      recording_id: ev.recording_id,
      timestamp_seconds: ev.timestamp_seconds,
      time_range_start: ev.start_seconds ?? ev.timestamp_seconds,
      time_range_end: ev.end_seconds ?? ev.timestamp_seconds,
      category: ev.event_type,
      description: ev.action_description,
      confidence: ev.confidence,
      evidence_frames: ev.evidence_frames,
      review_status: 'new',
      is_mocked: false,
      feeds,
      severityTag: tag,
      event: ev,
      camera_name: ev.camera_name ?? rec?.camera_name,
      camera_location: ev.camera_location ?? rec?.camera_location,
      recording_filename: ev.recording_filename ?? rec?.original_filename,
    })
  }

  // Sort by timestamp ascending
  return items.sort((a, b) => a.timestamp_seconds - b.timestamp_seconds)
}

// ── Time range filter ─────────────────────────────────────────────────────────

export type TimeRange = '24h' | '7d' | 'all'

export function withinTimeRange(item: FeedItem, range: TimeRange): boolean {
  if (range === 'all') return true
  const now = Date.now()
  const cutoff = range === '24h' ? now - 86_400_000 : now - 7 * 86_400_000
  // timestamp_seconds is relative to recording start, not wall clock.
  // We can only filter by wall-clock if the item has an alert with created_at.
  if (item.alert?.created_at) {
    return new Date(item.alert.created_at).getTime() >= cutoff
  }
  if (item.event?.timestamp_absolute) {
    return new Date(item.event.timestamp_absolute).getTime() >= cutoff
  }
  // No wall-clock info — include by default
  return true
}

// ── Filter type ───────────────────────────────────────────────────────────────

export type FeedFilter = 'all' | 'police_urgent' | 'unusual_review' | 'routine'

export function matchesFilter(item: FeedItem, filter: FeedFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'police_urgent') return item.feeds.includes('police')
  if (filter === 'unusual_review') return item.severityTag === 'Review'
  if (filter === 'routine') return item.severityTag === 'Routine'
  return true
}

// ── Category display labels ───────────────────────────────────────────────────

export const CATEGORY_LABELS: Record<string, string> = {
  fire_smoke:                       'Fire / Smoke',
  person_fall_injury:               'Person Fall / Injury',
  vehicle_collision:                'Vehicle Collision',
  dangerous_animal:                 'Dangerous Animal',
  weapon_visible:                   'Weapon Visible',
  restricted_area_entry:            'Restricted Area Entry',
  other_hazard:                     'Hazard Detected',
  'possible-theft':                 'Possible Theft / Snatching',
  'possible-assault':               'Possible Assault',
  confrontation:                    'Confrontation',
  'suspicious-approach-departure':  'Suspicious Approach',
  'sudden-fall':                    'Sudden Fall',
  'other-anomaly':                  'Unusual Activity',
  person_detected:                  'Person Detected',
  vehicle_detected:                 'Vehicle Detected',
  narrative_step:                   'Activity Recorded',
}

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category.replace(/-/g, ' ')
}
