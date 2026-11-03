import {
  feedsForAlert,
  feedsForEvent,
  isPoliceEligibleAlert,
  isPoliceEligibleEvent,
  isOwnerEligibleAlert,
  isOwnerEligibleEvent,
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

// ── isPoliceEligibleAlert ─────────────────────────────────────────────────────

describe('isPoliceEligibleAlert', () => {
  // ── Allowed ────────────────────────────────────────────────────────────────
  test('other_hazard with theft description → eligible', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'other_hazard',
      description: 'Chain snatching robbery observed',
    }))).toBe(true)
  })

  test('other_hazard with assault description → eligible', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'other_hazard',
      description: 'Assault observed between two persons',
    }))).toBe(true)
  })

  test('other_hazard with abuse description → eligible', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'other_hazard',
      description: 'Harassment and abuse directed at victim',
    }))).toBe(true)
  })

  // ── Blocked — severity does not grant eligibility ─────────────────────────
  test('weapon_visible critical → owner only', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'weapon_visible', severity: 'critical',
    }))).toBe(false)
  })

  test('fire_smoke critical → owner only', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'fire_smoke', severity: 'critical',
    }))).toBe(false)
  })

  test('fire_smoke high → owner only', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'fire_smoke', severity: 'high',
    }))).toBe(false)
  })

  test('dangerous_animal critical → owner only', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'dangerous_animal', severity: 'critical',
    }))).toBe(false)
  })

  test('other_hazard critical but unrelated description → owner only', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'other_hazard',
      severity: 'critical',
      description: 'Person walking near entrance',
    }))).toBe(false)
  })

  test('other_hazard no description → owner only', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'other_hazard',
      description: '',
    }))).toBe(false)
  })

  test('person_fall_injury → owner only', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'person_fall_injury', severity: 'critical',
    }))).toBe(false)
  })

  test('vehicle_collision → owner only', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'vehicle_collision', severity: 'critical',
    }))).toBe(false)
  })

  test('restricted_area_entry → owner only', () => {
    expect(isPoliceEligibleAlert(makeAlert({
      hazard_category: 'restricted_area_entry', severity: 'critical',
    }))).toBe(false)
  })
})

// ── feedsForAlert ─────────────────────────────────────────────────────────────

describe('feedsForAlert', () => {
  test('other_hazard with theft description → both feeds', () => {
    expect(feedsForAlert(makeAlert({
      hazard_category: 'other_hazard',
      description: 'Robbery and theft observed near gate',
    }))).toEqual(['police', 'owner'])
  })

  test('other_hazard with assault description → both feeds', () => {
    expect(feedsForAlert(makeAlert({
      hazard_category: 'other_hazard',
      description: 'Assault between individuals',
    }))).toEqual(['police', 'owner'])
  })

  // These were previously police-eligible; now owner-only per strict allowlist
  test('weapon_visible → owner only', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'weapon_visible' }))).toEqual(['owner'])
  })

  test('fire_smoke critical → owner only', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'fire_smoke', severity: 'critical' }))).toEqual(['owner'])
  })

  test('fire_smoke medium → owner only', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'fire_smoke', severity: 'medium' }))).toEqual(['owner'])
  })

  test('dangerous_animal critical → owner only', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'dangerous_animal', severity: 'critical' }))).toEqual(['owner'])
  })

  test('dangerous_animal low → owner only', () => {
    expect(feedsForAlert(makeAlert({ hazard_category: 'dangerous_animal', severity: 'low' }))).toEqual(['owner'])
  })

  test('other_hazard unrelated description → owner only', () => {
    expect(feedsForAlert(makeAlert({
      hazard_category: 'other_hazard',
      severity: 'critical',
      description: 'Person near entrance',
    }))).toEqual(['owner'])
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

// ── isPoliceEligibleEvent ─────────────────────────────────────────────────────

describe('isPoliceEligibleEvent', () => {
  // ── THEFT events ──────────────────────────────────────────────────────────
  test('possible-theft confidence >= 0.6 → eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'possible-theft', confidence: 0.7 }))).toBe(true)
  })

  test('robbery confidence >= 0.6 → eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'robbery', confidence: 0.8 }))).toBe(true)
  })

  test('chain-snatching confidence >= 0.6 → eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'chain-snatching', confidence: 0.9 }))).toBe(true)
  })

  test('vehicle-theft confidence >= 0.6 → eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'vehicle-theft', confidence: 0.75 }))).toBe(true)
  })

  test('carjacking confidence >= 0.6 → eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'carjacking', confidence: 0.65 }))).toBe(true)
  })

  // ── ASSAULT events ────────────────────────────────────────────────────────
  test('possible-assault confidence >= 0.6 → eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'possible-assault', confidence: 0.6 }))).toBe(true)
  })

  test('confrontation confidence >= 0.6 → eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'confrontation', confidence: 0.8 }))).toBe(true)
  })

  test('dangerous-threat confidence >= 0.6 → eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'dangerous-threat', confidence: 0.7 }))).toBe(true)
  })

  test('armed-person confidence >= 0.6 → eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'armed-person', confidence: 0.65 }))).toBe(true)
  })

  // ── Confidence threshold ──────────────────────────────────────────────────
  test('possible-theft confidence < 0.6 → not eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'possible-theft', confidence: 0.5 }))).toBe(false)
  })

  test('robbery confidence < 0.6 → not eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'robbery', confidence: 0.5 }))).toBe(false)
  })

  // ── Blocked event types ───────────────────────────────────────────────────
  test('suspicious-approach-departure → not eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'suspicious-approach-departure', confidence: 0.9 }))).toBe(false)
  })

  test('person_detected → not eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'person_detected', confidence: 0.95 }))).toBe(false)
  })

  test('vehicle_detected → not eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'vehicle_detected', confidence: 0.95 }))).toBe(false)
  })

  test('sudden-fall → not eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'sudden-fall', confidence: 0.9 }))).toBe(false)
  })

  test('other-anomaly → not eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'other-anomaly', confidence: 0.9 }))).toBe(false)
  })

  test('narrative_step → not eligible', () => {
    expect(isPoliceEligibleEvent(makeEvent({ event_type: 'narrative_step', confidence: 0.9 }))).toBe(false)
  })
})

// ── feedsForEvent ─────────────────────────────────────────────────────────────

describe('feedsForEvent', () => {
  const ALL_TYPES = [
    'possible-theft', 'possible-assault',
    'confrontation', 'suspicious-approach-departure', 'sudden-fall', 'other-anomaly',
  ]

  test.each(ALL_TYPES)('every security event type reaches owner feed: %s', (evType) => {
    const ev = makeEvent({ event_type: evType, confidence: 0.9 })
    expect(feedsForEvent(ev)).toContain('owner')
  })

  test('possible-theft confidence >= 0.6 → both feeds', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'possible-theft', confidence: 0.7 }))).toEqual(['police', 'owner'])
  })

  test('possible-theft confidence < 0.6 → owner only (not police, but still owner-eligible)', () => {
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

  test('person_detected → not in owner feed (routine)', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'person_detected', confidence: 0.95 }))).toEqual([])
  })

  test('vehicle_detected → not in owner feed (routine)', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'vehicle_detected', confidence: 0.95 }))).toEqual([])
  })

  test('robbery confidence >= 0.6 → both feeds', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'robbery', confidence: 0.7 }))).toEqual(['police', 'owner'])
  })

  test('vehicle-theft confidence >= 0.6 → both feeds', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'vehicle-theft', confidence: 0.8 }))).toEqual(['police', 'owner'])
  })

  test('chain-snatching confidence >= 0.6 → both feeds', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'chain-snatching', confidence: 0.9 }))).toEqual(['police', 'owner'])
  })

  test('robbery confidence < 0.6 → owner only (not police, but still owner-eligible)', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'robbery', confidence: 0.5 }))).toEqual(['owner'])
  })

  test('dangerous-threat confidence >= 0.6 → both feeds', () => {
    expect(feedsForEvent(makeEvent({ event_type: 'dangerous-threat', confidence: 0.8 }))).toEqual(['police', 'owner'])
  })
})

// ── isOwnerEligibleAlert ──────────────────────────────────────────────────────

describe('isOwnerEligibleAlert', () => {
  test('fire_smoke → owner eligible', () => {
    expect(isOwnerEligibleAlert(makeAlert({ hazard_category: 'fire_smoke' }))).toBe(true)
  })
  test('dangerous_animal → owner eligible', () => {
    expect(isOwnerEligibleAlert(makeAlert({ hazard_category: 'dangerous_animal' }))).toBe(true)
  })
  test('flood_water → owner eligible', () => {
    expect(isOwnerEligibleAlert(makeAlert({ hazard_category: 'flood_water' as any }))).toBe(true)
  })
  test('electrical_hazard → owner eligible', () => {
    expect(isOwnerEligibleAlert(makeAlert({ hazard_category: 'electrical_hazard' as any }))).toBe(true)
  })
  test('structural_hazard → owner eligible', () => {
    expect(isOwnerEligibleAlert(makeAlert({ hazard_category: 'structural_hazard' as any }))).toBe(true)
  })
  test('unauthorized_entry → owner eligible', () => {
    expect(isOwnerEligibleAlert(makeAlert({ hazard_category: 'unauthorized_entry' as any }))).toBe(true)
  })
  test('vandalism → owner eligible', () => {
    expect(isOwnerEligibleAlert(makeAlert({ hazard_category: 'vandalism' as any }))).toBe(true)
  })
  test('weapon_visible → owner eligible', () => {
    expect(isOwnerEligibleAlert(makeAlert({ hazard_category: 'weapon_visible' }))).toBe(true)
  })
  test('restricted_area_entry → owner eligible', () => {
    expect(isOwnerEligibleAlert(makeAlert({ hazard_category: 'restricted_area_entry' }))).toBe(true)
  })
  test('unknown_hazard → not owner eligible', () => {
    expect(isOwnerEligibleAlert(makeAlert({ hazard_category: 'unknown_hazard' as any }))).toBe(false)
  })
})

// ── isOwnerEligibleEvent ──────────────────────────────────────────────────────

describe('isOwnerEligibleEvent', () => {
  test('suspicious-approach-departure → owner eligible', () => {
    expect(isOwnerEligibleEvent(makeEvent({ event_type: 'suspicious-approach-departure' }))).toBe(true)
  })
  test('sudden-fall → owner eligible', () => {
    expect(isOwnerEligibleEvent(makeEvent({ event_type: 'sudden-fall' }))).toBe(true)
  })
  test('possible-theft → owner eligible', () => {
    expect(isOwnerEligibleEvent(makeEvent({ event_type: 'possible-theft' }))).toBe(true)
  })
  test('possible-assault → owner eligible', () => {
    expect(isOwnerEligibleEvent(makeEvent({ event_type: 'possible-assault' }))).toBe(true)
  })
  test('other-anomaly → owner eligible', () => {
    expect(isOwnerEligibleEvent(makeEvent({ event_type: 'other-anomaly' }))).toBe(true)
  })
  test('person_detected → not owner eligible (routine)', () => {
    expect(isOwnerEligibleEvent(makeEvent({ event_type: 'person_detected' }))).toBe(false)
  })
  test('vehicle_detected → not owner eligible (routine)', () => {
    expect(isOwnerEligibleEvent(makeEvent({ event_type: 'vehicle_detected' }))).toBe(false)
  })
  test('narrative_step → not owner eligible (routine)', () => {
    expect(isOwnerEligibleEvent(makeEvent({ event_type: 'narrative_step' }))).toBe(false)
  })
  test('motion_detected → not owner eligible (routine)', () => {
    expect(isOwnerEligibleEvent(makeEvent({ event_type: 'motion_detected' }))).toBe(false)
  })
  test('unknown_event_type → not owner eligible', () => {
    expect(isOwnerEligibleEvent(makeEvent({ event_type: 'unknown_event_type' }))).toBe(false)
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
    const ev = makeEvent({ event_type: 'suspicious-approach-departure', confidence: 0.7 })
    const items = deduplicateItems([], [ev], recs)
    expect(items).toHaveLength(1)
    expect(items[0].source).toBe('event')
  })

  test('alert absorbed event has feeds from alert routing, not event routing', () => {
    // other_hazard with theft description absorbs a person_detected event within 5s
    // → feeds come from alert's police eligibility (theft → police), not from event routing
    const alert = makeAlert({
      hazard_category: 'other_hazard',
      description: 'Robbery observed near entrance',
      timestamp_seconds: 30,
    })
    const ev = makeEvent({ event_type: 'person_detected', confidence: 0.9, timestamp_seconds: 31 })
    const items = deduplicateItems([alert], [ev], recs)
    expect(items[0].feeds).toContain('police')
  })

  test('weapon_visible alert absorbed event has owner-only feeds', () => {
    // weapon_visible is no longer police-eligible under strict allowlist
    const alert = makeAlert({ hazard_category: 'weapon_visible', timestamp_seconds: 30 })
    const ev = makeEvent({ event_type: 'person_detected', confidence: 0.9, timestamp_seconds: 31 })
    const items = deduplicateItems([alert], [ev], recs)
    expect(items[0].feeds).not.toContain('police')
    expect(items[0].feeds).toContain('owner')
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
    'dangerous_animal', 'weapon_visible', 'restricted_area_entry', 'other_hazard',
    'flood_water', 'electrical_hazard', 'structural_hazard', 'unauthorized_entry', 'vandalism']
  for (const cat of hazardCats) {
    expect(CATEGORY_LABELS[cat]).toBeDefined()
  }
})
