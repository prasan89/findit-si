import '@testing-library/jest-dom'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TemporalTimeline from '../TemporalTimeline'
import type { Recording } from '@/types'

function makeRecording(overrides: Partial<Recording> = {}): Recording {
  return {
    recording_id: 'rec-1',
    original_filename: 'cam1.mp4',
    upload_timestamp: '2026-01-01T00:00:00Z',
    camera_name: 'Entrance',
    processing_status: 'INDEXED',
    frame_count: 10,
    ...overrides,
  }
}

const timelineResponse = {
  recording_id: 'rec-1',
  events: [
    {
      event_id: 'tev-1',
      recording_id: 'rec-1',
      category: 'confrontation',
      candidate_types: ['contact_confrontation'],
      stages: ['contact'],
      start_seconds: 5.0,
      end_seconds: 8.0,
      timestamp_seconds: 6.5,
      timestamp_source: 'container_pts',
      confidence: 0.66,
      confirming_frame_count: 2,
      single_frame_candidate: false,
      tracks: [],
      observable_actions: [],
      action_sequence: ['two people approach each other'],
      confirmed: [],
      uncertain: [],
      observed_facts: [],
      evidence_frames: [],
      clip: null,
      provenance: {},
    },
  ],
  count: 1,
  timestamp_source: 'container_pts',
  filters: { start: null, end: null, min_confidence: null, include_single_frame: true },
}

describe('TemporalTimeline', () => {
  const fetchMock = jest.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    global.fetch = fetchMock as unknown as typeof fetch
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => timelineResponse,
    })
  })

  test('loads and renders the timeline for the selected recording', async () => {
    render(<TemporalTimeline recordings={[makeRecording()]} onInvestigated={() => {}} />)
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/recordings/rec-1/temporal-events')
      )
    })
    expect(await screen.findByText('Confrontation')).toBeInTheDocument()
    expect(screen.getByText(/1 event\(s\)/)).toBeInTheDocument()
  })

  test('passes filter params on reload', async () => {
    render(<TemporalTimeline recordings={[makeRecording()]} onInvestigated={() => {}} />)
    await screen.findByText('Confrontation')
    fetchMock.mockClear()
    fireEvent.change(screen.getByLabelText(/Start \(s\)/), { target: { value: '4' } })
    fireEvent.change(screen.getByLabelText(/End \(s\)/), { target: { value: '9' } })
    fireEvent.click(screen.getByText('Load timeline'))
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('start=4')
      )
    })
    const url = String(fetchMock.mock.calls[0][0])
    expect(url).toContain('end=9')
    expect(url).toContain('include_single_frame=true')
  })

  test('investigate posts a scoped investigation and reports the result', async () => {
    const onInvestigated = jest.fn()
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => timelineResponse })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ investigation_id: 'inv-1' }) })
    render(<TemporalTimeline recordings={[makeRecording()]} onInvestigated={onInvestigated} />)
    fireEvent.click(await screen.findByText(/Investigate this event/))
    await waitFor(() => expect(onInvestigated).toHaveBeenCalled())
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/investigations/',
      expect.objectContaining({ method: 'POST' })
    )
    const body = JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))
    expect(body.recording_ids).toEqual(['rec-1'])
    expect(body.time_start_seconds).toBe(5.0)
    expect(body.time_end_seconds).toBe(8.0)
    expect(body.query).toContain('confrontation')
  })

  test('shows backend errors', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ detail: 'Recording not found' }),
    })
    render(<TemporalTimeline recordings={[makeRecording()]} onInvestigated={() => {}} />)
    expect(await screen.findByText('Recording not found')).toBeInTheDocument()
  })
})
