import {
  feedsForAlert,
  feedsForEvent,
  severityTagForAlert,
  severityTagForEvent,
  deduplicateItems,
  matchesFilter,
  withinTimeRange,
  categoryLabel,
  CATEGORY_LABELS,
} from '../guardianRouting'
import type { GuardianAlert, Event, Recording } from '@/types'

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeAlert(overrides: Partial<GuardianAlert> = {}): GuardianAlert {
  return {
    alert_id: 'a1',
    recording_id: 'rec-1',
    hazard_category: 'weapon_visible',
    severity: 'critical',
    confidence: 0.9,
    timestamp_seconds: 30.0,
    time_range_start: 28.0,
    time_range_end: 35.0,
    description: 'A weapon was observed.',
    evidence_frames: [],
    supporting_segment_ids: [],
    review_status: 'new',
    is_mocked: false,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  }
}

function makeEvent(overrides: Partial<Event> = {}): Event {
  return {
    event_id: 'e1',
    recording_id: 'rec-1',
    timestamp_seconds: 10.0,
    has_reliable_timestamp: false,
    event_type: 'person_detected',
    action_description: 'Person entered the property.',
    object_descriptions: [],
    evidence_frames: [],
    confidence: 0.8,
    observed_facts: [],
    inferred_claims: [],
    uncertainty_notes: [],
    recording_filename: 'test.mp4',
    cross_camera_links: [],
    ...overrides,
  }
}

function makeRecording(overrides: Partial<Recording> = {}): Recording {
  return {
    recording_id: 'rec-1',
    original_filename: 'cam.mp4',
    upload_timestamp: new Date().toISOString(),
    processing_status: 'INDEXED',
    frame_count: 0,
    ...overrides,
  }
}

// ── feedsForAlert ─────────────────────────────────────────────────────────────

describe('feedsForAlert', () => {
  test('weapon_visible → both feeds', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'weapon_visible' }))).toEqual(['police', 'owner'])
  })

  test('fire_smoke critical → both feeds', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'fire_smoke', severity: 'critical' }))).toEqual(['police', 'owner'])
  })

  test('fire_smoke high → both feeds', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'fire_smoke', severity: 'high' }))).toEqual(['police', 'owner'])
  })

  test('fire_smoke medium → owner only', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'fire_smoke', severity: 'medium' }))).toEqual(['owner'])
  })

  test('dangerous_animal critical → both feeds', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'dangerous_animal', severity: 'critical' }))).toEqual(['police', 'owner'])
  })

  test('dangerous_animal low → owner only', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'dangerous_animal', severity: 'low' }))).toEqual(['owner'])
  })

  test('other_hazard critical → both feeds', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'other_hazard', severity: 'critical' }))).toEqual(['police', 'owner'])
  })

  test('person_fall_injury → owner only', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'person_fall_injury', severity: 'critical' }))).toEqual(['owner'])
  })

  test('vehicle_collision → owner only', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'vehicle_collision', severity: 'high' }))).toEqual(['owner'])
  })

  test('restricted_area_entry → owner only', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'restricted_area_entry', severity: 'critical' }))).toEqual(['owner'])
  })
})

// ── feedsForEvent ─────────────────────────────────────────────────────────────

describe('feedsForEvent', () => {
  const ALL_TYPES = [
    'person_detected', 'vehicle_detected', 'possible-theft', 'possible-assault',
    'confrontation', 'suspicious-approach-departure', 'sudden-fall', 'other-anomaly', 'narrative_step',
  ]

  test.each(ALL_TYPES)('every event type reaches owner feed: %s', (evType) => {
    const ev = makeEvent({ event_type: evType, confidence: 0.9 })
    expect(feedsForEvent(ev)).toContain('owner')
  })

  test('possible-theft confidence >= 0.6 → both feeds', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'possible-theft', confidence: 0.7 }))).toEqual(['police', 'owner'])
  })

  test('possible-theft confidence < 0.6 → owner only', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'possible-theft', confidence: 0.5 }))).toEqual(['owner'])
  })

  test('possible-assault confidence >= 0.6 → both feeds', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'possible-assault', confidence: 0.6 }))).toEqual(['police', 'owner'])
  })

  test('confrontation confidence >= 0.6 → both feeds', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'confrontation', confidence: 0.8 }))).toEqual(['police', 'owner'])
  })

  test('suspicious-approach-departure → owner only (not police)', () => {
    const feeds = feedsForEvent(makeEvent({ event_type: 'suspicious-approach-departure', confidence: 0.9 }))
    expect(feeds).toContain('owner')
    expect(feeds).not.toContain('police')
  })

  test('person_detected → owner only', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'person_detected', confidence: 0.95 }))).toEqual(['owner'])
  })

  test('vehicle_detected → owner only', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'vehicle_detected', confidence: 0.95 }))).toEqual(['owner'])
  })
})

// ── severityTagForAlert ───────────────────────────────────────────────────────

describe('severityTagForAlert', () => {
  test('critical → Critical', () => expect(severityTagForAlert(makeAlert({ severity: 'critical' }))).toBe('Critical'))
  test('high → Critical', () => expect(severityTagForAlert(makeAlert({ severity: 'high' }))).toBe('Critical'))
  test('medium → Review', () => expect(severityTagForAlert(makeAlert({ severity: 'medium' }))).toBe('Review'))
  test('low → Routine', () => expect(severityTagForAlert(makeAlert({ severity: 'low' }))).toBe('Routine'))
})

// ── severityTagForEvent ───────────────────────────────────────────────────────

describe('severityTagForEvent', () => {
  test('possible-theft confidence >= 0.75 → Critical', () => {
    expect(severityTagForEvent(makeEvent({ event_type: 'possible-theft', confidence: 0.8 }))).toBe('Critical')
  })

  test('possible-theft confidence 0.6–0.75 → Review', () => {
    expect(severityTagForEvent(makeEvent({ event_type: 'possible-theft', confidence: 0.65 }))).toBe('Review')
  })

  test('suspicious-approach-departure → Review', () => {
    expect(severityTagForEvent(makeEvent({ event_type: 'suspicious-approach-departure', confidence: 0.9 }))).toBe('Review')
  })

  test('person_detected → Routine', () => {
    expect(severityTagForEvent(makeEvent({ event_type: 'person_detected', confidence: 0.9 }))).toBe('Routine')
  })

  test('vehicle_detected → Routine', () => {
    expect(severityTagForEvent(makeEvent({ event_type: 'vehicle_detected', confidence: 0.9 }))).toBe('Routine')
  })

  test('sudden-fall → Review', () => {
    expect(severityTagForEvent(makeEvent({ event_type: 'sudden-fall', confidence: 0.9 }))).toBe('Review')
  })
})

// ── deduplicateItems ──────────────────────────────────────────────────────────

describe('deduplicateItems', () => {
  const recs = [makeRecording()]

  test('alert + event within 5s → one FeedItem', () => {
    const alert = makeAlert({ timestamp_seconds: 30.0 })
    const ev = makeEvent({ event_type: 'possible-theft', confidence: 0.8, timestamp_seconds: 32.0 })
    const items = deduplicateItems([alert], [ev], recs)
    expect(items).toHaveLength(1)
    expect(items[0].source).toBe('alert')
    expect(items[0].event).toBeDefined()
  })

  test('alert + event more than 5s apart → two FeedItems', () => {
    const alert = makeAlert({ timestamp_seconds: 30.0 })
    const ev = makeEvent({ event_type: 'possible-theft', confidence: 0.8, timestamp_seconds: 60.0 })
    const items = deduplicateItems([alert], [ev], recs)
    expect(items).toHaveLength(2)
  })

  test('event on different recording not absorbed', () => {
    const alert = makeAlert({ timestamp_seconds: 30.0, recording_id: 'rec-1' })
    const ev = makeEvent({ timestamp_seconds: 31.0, recording_id: 'rec-2' })
    const items = deduplicateItems([alert], [ev], recs)
    expect(items).toHaveLength(2)
  })

  test('reprocessing same alert (same id): DB dedup is server-side; client shows what it receives', () => {
    // The DB enforces uniqueness via the (recording_id, dedup_key) UNIQUE index.
    // The client does not additionally deduplicate alerts — it trusts the server.
    // If the API returns the same alert twice (shouldn't happen in prod), each becomes a FeedItem.
    // This test documents that contract.
    const alert = makeAlert()
    const items = deduplicateItems([alert], [], recs)
    expect(items).toHaveLength(1)
    expect(items[0].id).toBe('alert-a1')
  })

  test('standalone event (no nearby alert) becomes its own FeedItem', () => {
    const ev = makeEvent({ event_type: 'vehicle_detected', confidence: 0.7 })
    const items = deduplicateItems([], [ev], recs)
    expect(items).toHaveLength(1)
    expect(items[0].source).toBe('event')
  })

  test('alert absorbed event has feeds from alert routing, not event routing', () => {
    // weapon_visible alert absorbs a person_detected event within 5s
    const alert = makeAlert({ hazard_category: 'weapon_visible', timestamp_seconds: 30 })
    const ev = makeEvent({ event_type: 'person_detected', confidence: 0.9, timestamp_seconds: 31 })
    const items = deduplicateItems([alert], [ev], recs)
    expect(items[0].feeds).toContain('police')
  })

  test('items sorted by timestamp ascending', () => {
    const a = makeAlert({ alert_id: 'late', timestamp_seconds: 50 })
    const b = makeEvent({ event_id: 'early', timestamp_seconds: 10 })
    const items = deduplicateItems([a], [b], recs)
    expect(items[0].timestamp_seconds).toBeLessThan(items[1].timestamp_seconds)
  })
})

// ── matchesFilter ─────────────────────────────────────────────────────────────

describe('matchesFilter', () => {
  const policeItem = { feeds: ['police', 'owner'] as ('police' | 'owner')[], severityTag: 'Critical' as const } as Parameters<typeof matchesFilter>[0]
  const reviewItem = { feeds: ['owner'] as ('police' | 'owner')[], severityTag: 'Review' as const } as Parameters<typeof matchesFilter>[0]
  const routineItem = { feeds: ['owner'] as ('police' | 'owner')[], severityTag: 'Routine' as const } as Parameters<typeof matchesFilter>[0]

  test('all filter includes everything', () => {
    expect(matchesFilter(policeItem, 'all')).toBe(true)
    expect(matchesFilter(reviewItem, 'all')).toBe(true)
    expect(matchesFilter(routineItem, 'all')).toBe(true)
  })

  test('police_urgent includes only police-feed items', () => {
    expect(matchesFilter(policeItem, 'police_urgent')).toBe(true)
    expect(matchesFilter(reviewItem, 'police_urgent')).toBe(false)
  })

  test('unusual_review matches Review items', () => {
    expect(matchesFilter(reviewItem, 'unusual_review')).toBe(true)
    expect(matchesFilter(routineItem, 'unusual_review')).toBe(false)
  })

  test('routine matches Routine items', () => {
    expect(matchesFilter(routineItem, 'routine')).toBe(true)
    expect(matchesFilter(policeItem, 'routine')).toBe(false)
  })
})

// ── categoryLabel ─────────────────────────────────────────────────────────────

test('categoryLabel returns human label for known categories', () => {
  expect(categoryLabel('weapon_visible')).toBe('Weapon Visible')
  expect(categoryLabel('possible-theft')).toBe('Possible Theft / Snatching')
  expect(categoryLabel('fire_smoke')).toBe('Fire / Smoke')
})

test('categoryLabel falls back to replacing hyphens for unknown types', () => {
  expect(categoryLabel('some-unknown-type')).toBe('some unknown type')
})

test('all expected hazard categories have labels', () => {
  const hazardCats = ['fire_smoke', 'person_fall_injury', 'vehicle_collision',
    'dangerous_animal', 'weapon_visible', 'restricted_area_entry', 'other_hazard']
  for (const cat of hazardCats) {
    expect(CATEGORY_LABELS[cat]).toBeDefined()
  }
})
