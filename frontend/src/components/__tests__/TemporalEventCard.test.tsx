import '@testing-library/jest-dom'
import { render, screen, fireEvent } from '@testing-library/react'
import TemporalEventCard from '../TemporalEventCard'
import type { TemporalEvent } from '@/types'

function makeEvent(overrides: Partial<TemporalEvent> = {}): TemporalEvent {
  return {
    event_id: 'tev-1',
    recording_id: 'rec-1',
    category: 'sudden-fall',
    candidate_types: ['stumble_fall'],
    stages: ['fall'],
    start_seconds: 12.0,
    end_seconds: 15.5,
    timestamp_seconds: 13.75,
    timestamp_source: 'container_pts',
    confidence: 0.82,
    confirming_frame_count: 3,
    single_frame_candidate: false,
    tracks: [],
    observable_actions: [{ action: 'person falls', t: 13.2 }],
    action_sequence: ['person stumbles', 'person falls to ground'],
    confirmed: ['person on ground at 14.1s'],
    uncertain: ['occlusion during 12.0s–12.5s'],
    observed_facts: ['Observed sequence span 12.0s–15.5s'],
    evidence_frames: [
      { frame_path: 'frames/rec-1/before.jpg', timestamp_seconds: 11.0, role: 'before', timestamp_source: 'container_pts', pts_seconds: 11.02 },
      { frame_path: 'frames/rec-1/during.jpg', timestamp_seconds: 13.75, role: 'during', timestamp_source: 'container_pts', pts_seconds: 13.77 },
    ],
    clip: {
      start_seconds: 10.0,
      end_seconds: 17.5,
      timestamp_source: 'container_pts',
      provenance: {},
      clip_url: '/api/recordings/rec-1/clip?start=10.000&end=17.500',
    },
    provenance: {},
    ...overrides,
  }
}

const noop = () => {}

test('shows event type label with confidence', () => {
  render(
    <TemporalEventCard
      event={makeEvent()}
      index={1}
      investigating={false}
      investigateError={null}
      onInvestigate={noop}
    />,
  )
  expect(screen.getByText('Sudden fall')).toBeInTheDocument()
  expect(screen.getByText(/High confidence \(82%\)/)).toBeInTheDocument()
})

test('shows timestamp range plus duration', () => {
  render(
    <TemporalEventCard
      event={makeEvent()}
      index={1}
      investigating={false}
      investigateError={null}
      onInvestigate={noop}
    />,
  )
  expect(screen.getByText(/0:12–0:15/)).toBeInTheDocument()
  expect(screen.getByText(/\(3\.5s\)/)).toBeInTheDocument()
})

test('shows evidence thumbnails with role navigation', () => {
  render(
    <TemporalEventCard
      event={makeEvent()}
      index={1}
      investigating={false}
      investigateError={null}
      onInvestigate={noop}
    />,
  )
  expect(screen.getByAltText('Evidence frame (during)')).toBeInTheDocument()
  fireEvent.click(screen.getByText('before'))
  expect(screen.getByAltText('Evidence frame (before)')).toBeInTheDocument()
})

test('plays the event clip on demand', () => {
  const { container } = render(
    <TemporalEventCard
      event={makeEvent()}
      index={1}
      investigating={false}
      investigateError={null}
      onInvestigate={noop}
    />,
  )
  expect(container.querySelector('video')).toBeNull()
  fireEvent.click(screen.getByText(/Play event clip/))
  const video = container.querySelector('video')
  expect(video).not.toBeNull()
  expect(video?.getAttribute('src')).toContain('/api/recordings/rec-1/clip')
})

test('expands the action sequence', () => {
  render(
    <TemporalEventCard
      event={makeEvent()}
      index={1}
      investigating={false}
      investigateError={null}
      onInvestigate={noop}
    />,
  )
  fireEvent.click(screen.getByText(/Action sequence \(2 steps\)/))
  expect(screen.getByText('person stumbles')).toBeInTheDocument()
  expect(screen.getByText('person falls to ground')).toBeInTheDocument()
})

test('shows uncertainties with supporting evidence', () => {
  render(
    <TemporalEventCard
      event={makeEvent()}
      index={1}
      investigating={false}
      investigateError={null}
      onInvestigate={noop}
    />,
  )
  expect(screen.getByText(/Uncertainties \(1\)/)).toBeInTheDocument()
  expect(screen.getByText(/occlusion during/)).toBeInTheDocument()
  expect(screen.getByText(/3 confirming frame/)).toBeInTheDocument()
  expect(screen.getByText(/timestamp source: container_pts/)).toBeInTheDocument()
})

test('flags single-frame candidates and approximate timestamps', () => {
  render(
    <TemporalEventCard
      event={makeEvent({ single_frame_candidate: true, timestamp_source: 'coarse_grid' })}
      index={1}
      investigating={false}
      investigateError={null}
      onInvestigate={noop}
    />,
  )
  expect(screen.getByText('Single-frame candidate')).toBeInTheDocument()
  expect(screen.getByText('Approximate timestamp')).toBeInTheDocument()
})

test('investigate button calls onInvestigate with the event', () => {
  const ev = makeEvent()
  const onInvestigate = jest.fn()
  render(
    <TemporalEventCard
      event={ev}
      index={1}
      investigating={false}
      investigateError={null}
      onInvestigate={onInvestigate}
    />,
  )
  fireEvent.click(screen.getByText(/Investigate this event/))
  expect(onInvestigate).toHaveBeenCalledWith(ev)
})

test('shows investigating state and error', () => {
  render(
    <TemporalEventCard
      event={makeEvent()}
      index={1}
      investigating={true}
      investigateError={null}
      onInvestigate={noop}
    />,
  )
  expect(screen.getByText('Investigating…')).toBeInTheDocument()
})
