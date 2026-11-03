import '@testing-library/jest-dom'
import { render, screen, fireEvent } from '@testing-library/react'
import GuardianMessageCard from '../GuardianMessageCard'
import type { FeedItem } from '@/lib/guardianRouting'

function makeItem(overrides: Partial<FeedItem> = {}): FeedItem {
  return {
    id: 'item-1',
    source: 'alert',
    recording_id: 'rec-1',
    timestamp_seconds: 35.0,
    time_range_start: 30.0,
    time_range_end: 42.0,
    category: 'weapon_visible',
    description: 'A weapon was observed in the frame.',
    confidence: 0.9,
    evidence_frames: [],
    review_status: 'new',
    is_mocked: false,
    feeds: ['police', 'owner'],
    severityTag: 'Critical',
    camera_name: 'Gate-02',
    camera_location: 'Main Entrance',
    ...overrides,
  }
}

function renderCard(
  overrides: Partial<FeedItem> = {},
  isPoliceTab = true,
  onViewEvidence = jest.fn(),
  onAcknowledge = jest.fn(),
) {
  const item = makeItem(overrides)
  return {
    item,
    onViewEvidence,
    onAcknowledge,
    ...render(
      <GuardianMessageCard
        item={item}
        isPoliceTab={isPoliceTab}
        onViewEvidence={onViewEvidence}
        onAcknowledge={onAcknowledge}
        isSelected={false}
      />
    ),
  }
}

// ── Rendering ─────────────────────────────────────────────────────────────────

test('renders category label in uppercase', () => {
  renderCard()
  expect(screen.getByText('Weapon Visible')).toBeInTheDocument()
})

test('renders severity badge', () => {
  renderCard({ severityTag: 'Critical' })
  expect(screen.getByText('Critical')).toBeInTheDocument()
})

test('renders Review severity badge', () => {
  renderCard({ severityTag: 'Review', category: 'suspicious-approach-departure' })
  expect(screen.getByText('Review')).toBeInTheDocument()
})

test('renders Routine severity badge', () => {
  renderCard({ severityTag: 'Routine', category: 'person_detected', feeds: ['owner'] })
  expect(screen.getByText('Routine')).toBeInTheDocument()
})

test('renders camera name', () => {
  renderCard({ camera_name: 'Front Gate Camera' })
  expect(screen.getByText(/Front Gate Camera/i)).toBeInTheDocument()
})

test('renders confidence percentage', () => {
  renderCard({ confidence: 0.92 })
  expect(screen.getByText('92%')).toBeInTheDocument()
})

test('renders description text', () => {
  renderCard({ description: 'Person approached from behind, grabbed item.' })
  expect(screen.getByText(/Person approached from behind/i)).toBeInTheDocument()
})

// ── Simulation label ──────────────────────────────────────────────────────────

test('shows simulation label on police tab', () => {
  renderCard({}, true)
  expect(screen.getByText('Simulation')).toBeInTheDocument()
})

test('does not show simulation label on owner tab', () => {
  renderCard({}, false)
  expect(screen.queryByText('Simulation')).not.toBeInTheDocument()
})

// ── No confirmed-crime language ───────────────────────────────────────────────

test('does not show "confirmed crime" or "confirmed theft" language', () => {
  renderCard({ category: 'possible-theft', description: 'Possible chain snatching detected.' })
  // Check entire rendered text does not contain "confirmed theft/crime/robbery"
  const cardEl = screen.getByTestId('guardian-message-card')
  expect(cardEl.textContent).not.toMatch(/confirmed\s+(theft|crime|robbery|assault)/i)
})

// ── Actions ───────────────────────────────────────────────────────────────────

test('View Evidence button fires onViewEvidence callback', () => {
  const { onViewEvidence, item } = renderCard()
  fireEvent.click(screen.getByLabelText('View evidence'))
  expect(onViewEvidence).toHaveBeenCalledWith(item)
})

test('Acknowledge button fires onAcknowledge with acknowledged status', () => {
  const { onAcknowledge, item } = renderCard({ source: 'alert', review_status: 'new' })
  fireEvent.click(screen.getByLabelText('Acknowledge'))
  expect(onAcknowledge).toHaveBeenCalledWith(item, 'acknowledged')
})

test('Acknowledge button not shown for event-sourced items', () => {
  renderCard({ source: 'event' })
  expect(screen.queryByLabelText('Acknowledge')).not.toBeInTheDocument()
})

// ── Dispatch action ───────────────────────────────────────────────────────────

test('shows Dispatch button on police tab for alert items', () => {
  renderCard({ source: 'alert' }, true)
  expect(screen.getByLabelText('Dispatch (simulation)')).toBeInTheDocument()
})

test('does not show Dispatch button on owner tab', () => {
  renderCard({ source: 'alert' }, false)
  expect(screen.queryByLabelText('Dispatch (simulation)')).not.toBeInTheDocument()
})

test('does not show Dispatch button for event-sourced items on police tab', () => {
  renderCard({ source: 'event' }, true)
  expect(screen.queryByLabelText('Dispatch (simulation)')).not.toBeInTheDocument()
})

// ── Thumbnail states ──────────────────────────────────────────────────────────

test('renders evidence thumbnail when evidence frames are present', () => {
  renderCard({ evidence_frames: [{ frame_path: 'test/frame.jpg', timestamp_seconds: 10, role: 'during' }] })
  const img = screen.getByAltText('Evidence')
  expect(img).toBeInTheDocument()
  expect(img).toHaveAttribute('src', '/frames/test%2Fframe.jpg')
})

test('renders no-video state when evidence frames are empty', () => {
  renderCard({ evidence_frames: [] })
  expect(screen.queryByAltText('Evidence')).not.toBeInTheDocument()
})

// ── Dispatch action ───────────────────────────────────────────────────────────

test('shows Dispatch button on police tab for alert items', () => {
  renderCard({ source: 'alert' }, true)
  expect(screen.getByLabelText('Dispatch (simulation)')).toBeInTheDocument()
})

test('does not show Dispatch button on owner tab', () => {
  renderCard({ source: 'alert' }, false)
  expect(screen.queryByLabelText('Dispatch (simulation)')).not.toBeInTheDocument()
})

test('does not show Dispatch button for event-sourced items on police tab', () => {
  renderCard({ source: 'event' }, true)
  expect(screen.queryByLabelText('Dispatch (simulation)')).not.toBeInTheDocument()
})

// ── Thumbnail states ──────────────────────────────────────────────────────────

test('renders evidence thumbnail when evidence frames are present', () => {
  renderCard({ evidence_frames: [{ frame_path: 'test/frame.jpg', timestamp_seconds: 10, role: 'during' }] })
  const img = screen.getByAltText('Evidence')
  expect(img).toBeInTheDocument()
  expect(img).toHaveAttribute('src', '/frames/test%2Fframe.jpg')
})

test('renders no-video state when evidence frames are empty', () => {
  renderCard({ evidence_frames: [] })
  expect(screen.queryByAltText('Evidence')).not.toBeInTheDocument()
})
