import { render, screen } from '@testing-library/react'
import SightingCard from '../SightingCard'
import type { Sighting } from '@/types'

const mockSighting: Sighting = {
  observation_id: 'obs-1',
  recording_id: 'rec-1',
  recording_filename: 'office_afternoon.mp4',
  video_timestamp_seconds: 72,
  recording_datetime: '2026-10-25T14:00:00+00:00',
  recording_timezone: 'UTC',
  has_reliable_timestamp: true,
  object_label: 'notebook',
  object_attributes: { color: 'blue', shape: 'rectangular' },
  scene_description: 'Blue notebook on a desk',
  source_frame_path: 'frames/rec-1/frame.jpg',
  detection_score: 0.91,
  match_explanation: 'Object identified as notebook. color: blue.',
}

test('renders recording filename', () => {
  render(<SightingCard sighting={mockSighting} />)
  expect(screen.getByText('office_afternoon.mp4')).toBeInTheDocument()
})

test('renders video timestamp', () => {
  render(<SightingCard sighting={mockSighting} />)
  expect(screen.getByText('1:12')).toBeInTheDocument()
})

test('shows recording date when has_reliable_timestamp is true', () => {
  render(<SightingCard sighting={mockSighting} />)
  expect(screen.queryByText(/recording date unknown/i)).not.toBeInTheDocument()
})

test('shows unknown date warning when has_reliable_timestamp is false', () => {
  render(<SightingCard sighting={{ ...mockSighting, has_reliable_timestamp: false, recording_datetime: undefined }} />)
  expect(screen.getByText(/recording date unknown/i)).toBeInTheDocument()
})

test('shows high confidence label for score >= 0.7', () => {
  render(<SightingCard sighting={mockSighting} />)
  expect(screen.getByText(/high confidence/i)).toBeInTheDocument()
})

test('shows match explanation', () => {
  render(<SightingCard sighting={mockSighting} />)
  expect(screen.getByText(/Object identified as notebook/)).toBeInTheDocument()
})
