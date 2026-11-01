import '@testing-library/jest-dom'
import { render, screen, fireEvent } from '@testing-library/react'
import EventCard from '../EventCard'
import type { Event } from '@/types'

function makeEvent(overrides: Partial<Event> = {}): Event {
  return {
    event_id: 'ev-1',
    recording_id: 'rec-1',
    camera_name: 'test2',
    timestamp_seconds: 4.5,
    has_reliable_timestamp: false,
    event_type: 'scene_change',
    object_descriptions: ['black bird'],
    action_description: 'standing on dark soil in center of frame facing right, stationary',
    evidence_frames: [],
    confidence: 0.94,
    observed_facts: ['Observed: black bird'],
    inferred_claims: [],
    uncertainty_notes: [],
    recording_filename: 'test2.mp4',
    cross_camera_links: [],
    ...overrides,
  }
}

test('labels a person event as Person with its confidence', () => {
  render(
    <EventCard
      event={makeEvent({ event_type: 'person_detected', action_description: 'red person at entrance' })}
      index={1}
    />,
  )
  expect(screen.getByText('Person')).toBeInTheDocument()
  expect(screen.getByText(/High confidence \(94%\)/)).toBeInTheDocument()
})

test('does not label a bird scene_change event as Person', () => {
  render(<EventCard event={makeEvent()} index={1} />)
  expect(screen.queryByText('Person')).not.toBeInTheDocument()
  expect(screen.getByText('Scene Change')).toBeInTheDocument()
  expect(screen.getByText(/standing on dark soil/)).toBeInTheDocument()
})

test('labels a bird event as Bird', () => {
  render(<EventCard event={makeEvent({ event_type: 'bird_detected' })} index={1} />)
  expect(screen.getByText('Bird')).toBeInTheDocument()
  expect(screen.queryByText('Person')).not.toBeInTheDocument()
})

test('surfaces category-contradiction uncertainty notes', () => {
  render(
    <EventCard
      event={makeEvent({
        event_type: 'person_detected',
        uncertainty_notes: ["Stored category 'person' is not supported by the event description"],
      })}
      index={1}
    />,
  )
  // The contradiction warning must be discoverable, not silently attached.
  fireEvent.click(screen.getByText(/1 uncertainty note/))
  expect(screen.getByText(/not supported by the event description/)).toBeInTheDocument()
})

test('Preview button reports the event recording and timestamp', () => {
  const onPlay = jest.fn()
  render(<EventCard event={makeEvent()} index={1} onPlayEvidence={onPlay} />)
  fireEvent.click(screen.getByRole('button', { name: /Preview/ }))
  expect(onPlay).toHaveBeenCalledWith('rec-1', 4.5)
})

test('headlines calibrated match strength instead of raw confidence', () => {
  render(
    <EventCard
      event={makeEvent({ relevance_score: 0.59, confidence: 0.79 })}
      index={1}
    />,
  )
  expect(screen.getByText(/Partial match \(59%\)/)).toBeInTheDocument()
  expect(screen.getByText(/detection 79%/)).toBeInTheDocument()
  expect(screen.queryByText(/High confidence/)).not.toBeInTheDocument()
})

test('headlines the primary finding excerpt above the generic action', () => {
  render(
    <EventCard
      event={makeEvent({
        primary_match: 'Observed: brown dog lying nearby',
        is_primary_finding: true,
      })}
      index={1}
    />,
  )
  expect(screen.getByText(/Primary finding:/)).toBeInTheDocument()
  expect(screen.getByText(/brown dog lying nearby/)).toBeInTheDocument()
  expect(screen.getByText(/★ Primary finding/)).toBeInTheDocument()
})

test('collapses long observation lists behind an expander', () => {
  const facts = Array.from({ length: 7 }, (_, i) => `Observed: object ${i}`)
  render(<EventCard event={makeEvent({ observed_facts: facts })} index={1} />)
  // First facts render in the open list, outside any <details>.
  expect(screen.getByText(/Observed: object 0/).closest('details')).toBeNull()
  // The rest sit inside a closed expander with an honest count.
  const summary = screen.getByText(/Show all 7 observations \(2 more\)/)
  const details = summary.closest('details')
  expect(details).not.toBeNull()
  expect(details).not.toHaveAttribute('open')
  expect(details).toHaveTextContent('Observed: object 6')
})

test('grounds the camera label with the recording filename', () => {
  render(
    <EventCard
      event={makeEvent({ camera_name: 'check', recording_filename: 'gate.mp4' })}
      index={1}
    />,
  )
  expect(screen.getByText('check')).toBeInTheDocument()
  expect(screen.getByText(/gate\.mp4/)).toBeInTheDocument()
})

test('falls back to a camera id when no camera name exists', () => {
  render(
    <EventCard event={makeEvent({ camera_name: undefined })} index={1} />,
  )
  expect(screen.getByText('Camera rec-1')).toBeInTheDocument()
})

test('labels incident categories meaningfully', () => {
  render(
    <EventCard event={makeEvent({ event_type: 'possible-theft' })} index={1} />,
  )
  expect(screen.getByText('Possible theft')).toBeInTheDocument()
})

test('renders tracked entities and action steps with timestamps', () => {
  render(
    <EventCard
      event={makeEvent({
        entities: [{
          entity_id: 'E1',
          head: 'person',
          display_label: 'person in dark dress',
          first_seen_seconds: 0,
          last_seen_seconds: 9,
          frame_count: 3,
          observation_count: 3,
        }],
        action_sequence: [
          { step: 1, start_seconds: 0, end_seconds: 4, description: 'standing by gate', frame_path: null },
          { step: 2, start_seconds: 5, end_seconds: 9, description: 'walking away', frame_path: null },
        ],
      })}
      index={1}
    />,
  )
  fireEvent.click(screen.getByText(/Tracked entities \(1\)/))
  expect(screen.getByText(/person in dark dress/)).toBeInTheDocument()
  fireEvent.click(screen.getByText(/Action sequence \(2 steps\)/))
  expect(screen.getByText(/walking away/)).toBeInTheDocument()
})

test('captions the evidence thumbnail with its exact timestamp', () => {
  render(
    <EventCard
      event={makeEvent({
        evidence_frames: [
          { frame_path: 'frames/rec-1/a.jpg', timestamp_seconds: 4.5, role: 'during' },
        ],
      })}
      index={1}
    />,
  )
  expect(screen.getByText(/t=4\.5s · during/)).toBeInTheDocument()
})
