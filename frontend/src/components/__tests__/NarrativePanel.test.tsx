import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import NarrativePanel from '../NarrativePanel'
import type { Recording } from '@/types'

function makeRecording(overrides: Partial<Recording> = {}): Recording {
  return {
    recording_id: 'rec-1',
    original_filename: 'test.mp4',
    upload_timestamp: '2026-01-01T00:00:00Z',
    processing_status: 'INDEXED',
    frame_count: 10,
    ...overrides,
  }
}

const NARRATIVE = {
  scene: 'Basement parking garage, overhead camera, nighttime',
  narrative_steps: [
    {
      step: 1,
      time_range: '0:00-0:18',
      start_seconds: 0,
      end_seconds: 18,
      description: 'A woman in a pink saree stands near a maroon car.',
    },
    {
      step: 2,
      time_range: '0:18-0:28',
      start_seconds: 18,
      end_seconds: 28,
      description: 'A man in dark clothing approaches from the right.',
    },
  ],
  key_observations: ['Man loiters for ~30 seconds before approaching'],
  open_questions: ['Camera cut at 0:28 — unclear what follows'],
  entities: [],
}

test('renders empty state when no recordings', () => {
  render(<NarrativePanel recordings={[]} />)
  expect(screen.getByText(/No indexed recordings yet/)).toBeInTheDocument()
})

test('renders unavailable state when narrative is null', () => {
  render(<NarrativePanel recordings={[makeRecording()]} />)
  expect(screen.getByText(/Narrative analysis not yet available/)).toBeInTheDocument()
})

test('renders scene description', () => {
  render(
    <NarrativePanel
      recordings={[makeRecording({ recording_narrative: NARRATIVE })]}
    />
  )
  expect(screen.getByText('Basement parking garage, overhead camera, nighttime')).toBeInTheDocument()
})

test('renders all narrative steps', () => {
  render(
    <NarrativePanel
      recordings={[makeRecording({ recording_narrative: NARRATIVE })]}
    />
  )
  expect(screen.getByText('A woman in a pink saree stands near a maroon car.')).toBeInTheDocument()
  expect(screen.getByText('A man in dark clothing approaches from the right.')).toBeInTheDocument()
})

test('renders step time range badges', () => {
  render(
    <NarrativePanel
      recordings={[makeRecording({ recording_narrative: NARRATIVE })]}
    />
  )
  expect(screen.getByText('0:00-0:18')).toBeInTheDocument()
  expect(screen.getByText('0:18-0:28')).toBeInTheDocument()
})

test('renders key observations', () => {
  render(
    <NarrativePanel
      recordings={[makeRecording({ recording_narrative: NARRATIVE })]}
    />
  )
  expect(screen.getByText('Man loiters for ~30 seconds before approaching')).toBeInTheDocument()
})

test('renders open questions', () => {
  render(
    <NarrativePanel
      recordings={[makeRecording({ recording_narrative: NARRATIVE })]}
    />
  )
  expect(screen.getByText("Camera cut at 0:28 — unclear what follows")).toBeInTheDocument()
})

test('renders camera name in dropdown', () => {
  render(
    <NarrativePanel
      recordings={[makeRecording({ camera_name: 'Entrance Cam', recording_narrative: NARRATIVE })]}
    />
  )
  expect(screen.getByText('Entrance Cam')).toBeInTheDocument()
})

test('renders filename in dropdown when no camera name', () => {
  render(
    <NarrativePanel
      recordings={[makeRecording({ recording_narrative: NARRATIVE })]}
    />
  )
  expect(screen.getByText('test.mp4')).toBeInTheDocument()
})
