import '@testing-library/jest-dom'
import { render, screen, fireEvent } from '@testing-library/react'
import GuardianAlertCard from '../GuardianAlertCard'
import type { GuardianAlert } from '@/types'

function makeAlert(overrides: Partial<GuardianAlert> = {}): GuardianAlert {
  return {
    alert_id: 'alert-1',
    recording_id: 'rec-1',
    hazard_category: 'fire_smoke',
    severity: 'high',
    confidence: 0.85,
    timestamp_seconds: 5.0,
    time_range_start: 0.0,
    time_range_end: 10.0,
    description: 'Visible flames on left wall',
    evidence_frames: [],
    supporting_segment_ids: [],
    review_status: 'new',
    is_mocked: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

test('renders severity badge for high severity', () => {
  render(<GuardianAlertCard alert={makeAlert()} onAcknowledge={jest.fn()} onDismiss={jest.fn()} updating={false} />)
  expect(screen.getByText('high')).toBeInTheDocument()
})

test('renders severity badge for critical severity', () => {
  render(<GuardianAlertCard alert={makeAlert({ severity: 'critical' })} onAcknowledge={jest.fn()} onDismiss={jest.fn()} updating={false} />)
  expect(screen.getByText('critical')).toBeInTheDocument()
})

test('renders human-readable category label', () => {
  render(<GuardianAlertCard alert={makeAlert()} onAcknowledge={jest.fn()} onDismiss={jest.fn()} updating={false} />)
  expect(screen.getByText('Fire / Smoke')).toBeInTheDocument()
})

test('renders correct label for person_fall_injury', () => {
  render(
    <GuardianAlertCard
      alert={makeAlert({ hazard_category: 'person_fall_injury' })}
      onAcknowledge={jest.fn()}
      onDismiss={jest.fn()}
      updating={false}
    />
  )
  expect(screen.getByText('Person Fall / Injury')).toBeInTheDocument()
})

test('renders correct label for weapon_visible', () => {
  render(
    <GuardianAlertCard
      alert={makeAlert({ hazard_category: 'weapon_visible' })}
      onAcknowledge={jest.fn()}
      onDismiss={jest.fn()}
      updating={false}
    />
  )
  expect(screen.getByText('Weapon Visible')).toBeInTheDocument()
})

test('renders description text', () => {
  render(<GuardianAlertCard alert={makeAlert()} onAcknowledge={jest.fn()} onDismiss={jest.fn()} updating={false} />)
  expect(screen.getByText('Visible flames on left wall')).toBeInTheDocument()
})

test('renders confidence percentage', () => {
  render(<GuardianAlertCard alert={makeAlert({ confidence: 0.85 })} onAcknowledge={jest.fn()} onDismiss={jest.fn()} updating={false} />)
  expect(screen.getByText(/85%/)).toBeInTheDocument()
})

test('renders time range', () => {
  render(
    <GuardianAlertCard
      alert={makeAlert({ time_range_start: 65.0, time_range_end: 125.0 })}
      onAcknowledge={jest.fn()}
      onDismiss={jest.fn()}
      updating={false}
    />
  )
  // 65s = 1:05, 125s = 2:05
  expect(screen.getByText(/1:05/)).toBeInTheDocument()
  expect(screen.getByText(/2:05/)).toBeInTheDocument()
})

test('shows Simulated badge when is_mocked is true', () => {
  render(
    <GuardianAlertCard
      alert={makeAlert({ is_mocked: true })}
      onAcknowledge={jest.fn()}
      onDismiss={jest.fn()}
      updating={false}
    />
  )
  expect(screen.getByText('Simulated')).toBeInTheDocument()
})

test('does not show Simulated badge when is_mocked is false', () => {
  render(<GuardianAlertCard alert={makeAlert({ is_mocked: false })} onAcknowledge={jest.fn()} onDismiss={jest.fn()} updating={false} />)
  expect(screen.queryByText('Simulated')).not.toBeInTheDocument()
})

test('calls onAcknowledge with alert_id when Acknowledge clicked', () => {
  const onAcknowledge = jest.fn()
  render(<GuardianAlertCard alert={makeAlert()} onAcknowledge={onAcknowledge} onDismiss={jest.fn()} updating={false} />)
  fireEvent.click(screen.getByRole('button', { name: /Acknowledge/ }))
  expect(onAcknowledge).toHaveBeenCalledWith('alert-1')
})

test('calls onDismiss with alert_id when Dismiss clicked', () => {
  const onDismiss = jest.fn()
  render(<GuardianAlertCard alert={makeAlert()} onAcknowledge={jest.fn()} onDismiss={onDismiss} updating={false} />)
  fireEvent.click(screen.getByRole('button', { name: /^Dismiss$/ }))
  expect(onDismiss).toHaveBeenCalledWith('alert-1')
})

test('Acknowledge button is disabled when already acknowledged', () => {
  render(
    <GuardianAlertCard
      alert={makeAlert({ review_status: 'acknowledged' })}
      onAcknowledge={jest.fn()}
      onDismiss={jest.fn()}
      updating={false}
    />
  )
  expect(screen.getByRole('button', { name: /Acknowledged/ })).toBeDisabled()
})

test('Dismiss button is disabled when already dismissed', () => {
  render(
    <GuardianAlertCard
      alert={makeAlert({ review_status: 'dismissed' })}
      onAcknowledge={jest.fn()}
      onDismiss={jest.fn()}
      updating={false}
    />
  )
  expect(screen.getByRole('button', { name: /Dismissed/ })).toBeDisabled()
})

test('both buttons are disabled while updating', () => {
  render(<GuardianAlertCard alert={makeAlert()} onAcknowledge={jest.fn()} onDismiss={jest.fn()} updating={true} />)
  expect(screen.getByRole('button', { name: /Acknowledge/ })).toBeDisabled()
  expect(screen.getByRole('button', { name: /^Dismiss$/ })).toBeDisabled()
})

test('dismissed alert card has reduced opacity', () => {
  const { container } = render(
    <GuardianAlertCard
      alert={makeAlert({ review_status: 'dismissed' })}
      onAcknowledge={jest.fn()}
      onDismiss={jest.fn()}
      updating={false}
    />
  )
  const card = container.firstChild as HTMLElement
  expect(card.className).toMatch(/opacity-50/)
})

test('non-dismissed alert card does not have reduced opacity', () => {
  const { container } = render(
    <GuardianAlertCard alert={makeAlert()} onAcknowledge={jest.fn()} onDismiss={jest.fn()} updating={false} />
  )
  const card = container.firstChild as HTMLElement
  expect(card.className).not.toMatch(/opacity-50/)
})

test('shows review status badge', () => {
  render(<GuardianAlertCard alert={makeAlert({ review_status: 'new' })} onAcknowledge={jest.fn()} onDismiss={jest.fn()} updating={false} />)
  expect(screen.getByText('new')).toBeInTheDocument()
})

test('renders camera name when provided', () => {
  render(
    <GuardianAlertCard
      alert={makeAlert({ camera_name: 'Entrance Cam' })}
      onAcknowledge={jest.fn()}
      onDismiss={jest.fn()}
      updating={false}
    />
  )
  expect(screen.getByText('Entrance Cam')).toBeInTheDocument()
})

test('renders evidence thumbnail when evidence_frames present', () => {
  render(
    <GuardianAlertCard
      alert={makeAlert({
        evidence_frames: [{ frame_path: 'frames/rec-1/f001.jpg', timestamp_seconds: 5.0, role: 'during' }],
      })}
      onAcknowledge={jest.fn()}
      onDismiss={jest.fn()}
      updating={false}
    />
  )
  const img = screen.getByRole('img', { name: /Evidence frame/i })
  expect(img).toBeInTheDocument()
  expect(img).toHaveAttribute('src', expect.stringContaining('f001.jpg'))
})
