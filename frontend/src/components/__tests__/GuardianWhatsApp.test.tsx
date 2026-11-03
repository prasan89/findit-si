import '@testing-library/jest-dom'
import { render, screen, fireEvent } from '@testing-library/react'
import GuardianWhatsApp from '../GuardianWhatsApp'
import type { GuardianAlert, Event, Recording } from '@/types'

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeRecording(overrides: Partial<Recording> = {}): Recording {
  return {
    recording_id: 'rec-1',
    original_filename: 'test.mp4',
    upload_timestamp: '2026-01-01T00:00:00Z',
    processing_status: 'INDEXED',
    frame_count: 0,
    camera_name: 'Street Entrance',
    ...overrides,
  }
}

function makeAlert(overrides: Partial<GuardianAlert> = {}): GuardianAlert {
  return {
    alert_id: 'alert-1',
    recording_id: 'rec-1',
    hazard_category: 'weapon_visible',
    severity: 'critical',
    confidence: 0.9,
    timestamp_seconds: 35.0,
    time_range_start: 30.0,
    time_range_end: 42.0,
    description: 'A weapon was observed in the frame.',
    evidence_frames: [],
    supporting_segment_ids: [],
    review_status: 'new',
    is_mocked: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function makeEvent(overrides: Partial<Event> = {}): Event {
  return {
    event_id: 'ev-1',
    recording_id: 'rec-1',
    timestamp_seconds: 10.0,
    has_reliable_timestamp: false,
    event_type: 'person_detected',
    action_description: 'A person entered the property.',
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

// ── Helpers ───────────────────────────────────────────────────────────────────

function renderWhatsApp(
  alerts: GuardianAlert[],
  events: Event[],
  recording = makeRecording(),
) {
  return render(
    <GuardianWhatsApp alerts={alerts} events={events} recording={recording} />
  )
}

// ── Simulation disclaimer ─────────────────────────────────────────────────────

test('shows simulation disclaimer banner', () => {
  renderWhatsApp([], [])
  expect(screen.getByText(/Simulation Only/i)).toBeInTheDocument()
})

// ── Empty state ───────────────────────────────────────────────────────────────

test('police tab shows empty state when no serious incidents', () => {
  const routineEvent = makeEvent({ event_type: 'person_detected', confidence: 0.9 })
  renderWhatsApp([], [routineEvent])
  expect(screen.getByText(/No serious incidents to report/i)).toBeInTheDocument()
})

test('owner tab shows empty state when no events or alerts', () => {
  renderWhatsApp([], [])
  // Switch to owner tab
  fireEvent.click(screen.getByText(/Owner/i))
  expect(screen.getByText(/No camera activity recorded yet/i)).toBeInTheDocument()
})

// ── Police tab routing ────────────────────────────────────────────────────────

test('police tab includes weapon_visible alert', () => {
  const alert = makeAlert({ hazard_category: 'weapon_visible' })
  renderWhatsApp([alert], [])
  expect(screen.getByText(/Weapon Visible/i)).toBeInTheDocument()
})

test('police tab includes dangerous_animal critical alert', () => {
  const alert = makeAlert({ hazard_category: 'dangerous_animal', severity: 'critical' })
  renderWhatsApp([alert], [])
  expect(screen.getByText(/Dangerous Animal/i)).toBeInTheDocument()
})

test('police tab excludes dangerous_animal low severity', () => {
  const alert = makeAlert({ hazard_category: 'dangerous_animal', severity: 'low' })
  renderWhatsApp([alert], [])
  expect(screen.getByText(/No serious incidents to report/i)).toBeInTheDocument()
})

test('police tab includes possible-theft event with confidence >= 0.6', () => {
  const ev = makeEvent({
    event_type: 'possible-theft',
    confidence: 0.75,
    action_description: 'Person snatched a chain from the victim.',
  })
  renderWhatsApp([], [ev])
  expect(screen.queryByText(/No serious incidents/i)).not.toBeInTheDocument()
  expect(screen.getByText(/Possible Theft/i)).toBeInTheDocument()
})

test('police tab excludes possible-theft event with confidence < 0.6', () => {
  const ev = makeEvent({ event_type: 'possible-theft', confidence: 0.4 })
  renderWhatsApp([], [ev])
  expect(screen.getByText(/No serious incidents to report/i)).toBeInTheDocument()
})

test('police tab excludes person_detected events', () => {
  const ev = makeEvent({ event_type: 'person_detected', confidence: 0.95 })
  renderWhatsApp([], [ev])
  expect(screen.getByText(/No serious incidents to report/i)).toBeInTheDocument()
})

test('police tab excludes vehicle_detected events', () => {
  const ev = makeEvent({ event_type: 'vehicle_detected', confidence: 0.95 })
  renderWhatsApp([], [ev])
  expect(screen.getByText(/No serious incidents to report/i)).toBeInTheDocument()
})

test('police tab includes fire_smoke critical alert', () => {
  const alert = makeAlert({ hazard_category: 'fire_smoke', severity: 'critical' })
  renderWhatsApp([alert], [])
  expect(screen.getByText(/Fire \/ Smoke/i)).toBeInTheDocument()
})

// ── Owner tab routing ─────────────────────────────────────────────────────────

test('owner tab includes person_detected event', () => {
  const ev = makeEvent({ event_type: 'person_detected' })
  renderWhatsApp([], [ev])
  fireEvent.click(screen.getByText(/Owner/i))
  expect(screen.getByText(/Person detected/i)).toBeInTheDocument()
})

test('owner tab includes vehicle_detected event', () => {
  const ev = makeEvent({ event_type: 'vehicle_detected', action_description: 'Vehicle arrived.' })
  renderWhatsApp([], [ev])
  fireEvent.click(screen.getByText(/Owner/i))
  expect(screen.getByText(/Vehicle detected/i)).toBeInTheDocument()
})

test('owner tab includes serious incidents', () => {
  const alert = makeAlert({ hazard_category: 'weapon_visible' })
  renderWhatsApp([alert], [])
  fireEvent.click(screen.getByText(/Owner/i))
  expect(screen.getByText(/Weapon Visible/i)).toBeInTheDocument()
})

test('serious event appears in both police and owner tabs', () => {
  const alert = makeAlert({ hazard_category: 'weapon_visible', severity: 'critical' })
  renderWhatsApp([alert], [])

  // Police tab (default)
  expect(screen.getByText(/Weapon Visible/i)).toBeInTheDocument()

  // Switch to owner
  fireEvent.click(screen.getByText(/Owner/i))
  expect(screen.getByText(/Weapon Visible/i)).toBeInTheDocument()
})

// ── Dedup: event within 5s of alert should not appear separately in police tab ──

test('event within 5s of guardian alert is not shown separately in police tab', () => {
  const alert = makeAlert({ hazard_category: 'weapon_visible', timestamp_seconds: 35.0 })
  // Event at 37s (within 5s) — would match possible-theft policy if allowed
  const ev = makeEvent({
    event_type: 'possible-theft',
    confidence: 0.8,
    timestamp_seconds: 37.0,
    action_description: 'Chain snatch observed.',
  })
  renderWhatsApp([alert], [ev])

  // Should show weapon_visible alert but NOT a second separate message for the event
  const weaponMatches = screen.getAllByText(/Weapon Visible/i)
  expect(weaponMatches.length).toBe(1)

  // Possible Theft should NOT appear as a separate bubble
  expect(screen.queryByText(/Possible Theft/i)).not.toBeInTheDocument()
})

test('event outside 5s dedup window appears separately in police tab', () => {
  const alert = makeAlert({ hazard_category: 'weapon_visible', timestamp_seconds: 35.0 })
  const ev = makeEvent({
    event_type: 'possible-theft',
    confidence: 0.8,
    timestamp_seconds: 60.0, // >5s away
    action_description: 'Later chain snatch.',
  })
  renderWhatsApp([alert], [ev])
  expect(screen.getByText(/Weapon Visible/i)).toBeInTheDocument()
  expect(screen.getByText(/Possible Theft/i)).toBeInTheDocument()
})

// ── Tab switching ─────────────────────────────────────────────────────────────

test('tab switching shows correct headers', () => {
  renderWhatsApp([], [])
  expect(screen.getByText('Police Control Room')).toBeInTheDocument()
  fireEvent.click(screen.getByText(/Owner/i))
  expect(screen.getByText('Property Owner')).toBeInTheDocument()
})

// ── Evidence timestamp button ─────────────────────────────────────────────────

test('police message shows evidence timestamp link', () => {
  const alert = makeAlert({
    hazard_category: 'weapon_visible',
    time_range_start: 30.0,
    time_range_end: 42.0,
  })
  renderWhatsApp([alert], [])
  // Evidence link shows time range — "0:30–0:42"
  expect(screen.getByText('0:30–0:42')).toBeInTheDocument()
})

// ── Message count badges ──────────────────────────────────────────────────────

test('police tab badge shows count of serious messages', () => {
  const alert = makeAlert({ hazard_category: 'weapon_visible' })
  renderWhatsApp([alert], [])
  // The badge "1" should appear next to Police tab
  const policeBadge = screen.getAllByText('1')
  expect(policeBadge.length).toBeGreaterThan(0)
})

// ── Camera name in message ────────────────────────────────────────────────────

test('police message includes camera name', () => {
  const alert = makeAlert({ hazard_category: 'weapon_visible' })
  const rec = makeRecording({ camera_name: 'Front Gate Camera' })
  renderWhatsApp([alert], [], rec)
  expect(screen.getByText(/Front Gate Camera/i)).toBeInTheDocument()
})
