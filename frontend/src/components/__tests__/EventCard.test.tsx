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
