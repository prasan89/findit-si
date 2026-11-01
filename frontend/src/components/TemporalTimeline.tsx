'use client'
import { useCallback, useEffect, useState } from 'react'
import TemporalEventCard from './TemporalEventCard'
import type {
  InvestigationResult,
  Recording,
  TemporalEvent,
  TemporalEventsResponse,
} from '@/types'

interface Props {
  recordings: Recording[]
  onInvestigated: (result: InvestigationResult) => void
}

function formatTs(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function TemporalTimeline({ recordings, onInvestigated }: Props) {
  const [recordingId, setRecordingId] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [minConfidence, setMinConfidence] = useState('')
  const [includeSingleFrame, setIncludeSingleFrame] = useState(true)
  const [response, setResponse] = useState<TemporalEventsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [investigatingId, setInvestigatingId] = useState<string | null>(null)
  const [investigateErrors, setInvestigateErrors] = useState<Record<string, string>>({})

  const indexedRecordings = recordings.filter(r =>
    r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL'
  )

  useEffect(() => {
    if (!recordingId && indexedRecordings.length > 0) {
      setRecordingId(indexedRecordings[0].recording_id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordings])

  const activeRecording = recordings.find(r => r.recording_id === recordingId) ?? null

  const fetchEvents = useCallback(async () => {
    if (!recordingId) return
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      if (start !== '') params.set('start', start)
      if (end !== '') params.set('end', end)
      if (minConfidence !== '') params.set('min_confidence', minConfidence)
      params.set('include_single_frame', String(includeSingleFrame))
      const qs = params.toString()
      const res = await fetch(`/api/recordings/${recordingId}/temporal-events${qs ? `?${qs}` : ''}`)
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setResponse(null)
        setError((err as { detail?: string }).detail || 'Failed to load timeline')
      } else {
        setResponse(await res.json())
      }
    } catch {
      setResponse(null)
      setError('Network error. Is the backend running?')
    }
    setLoading(false)
  }, [recordingId, start, end, minConfidence, includeSingleFrame])

  useEffect(() => {
    if (recordingId) fetchEvents()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordingId])

  const handleInvestigate = async (ev: TemporalEvent) => {
    setInvestigatingId(ev.event_id)
    setInvestigateErrors(prev => {
      const next = { ...prev }
      delete next[ev.event_id]
      return next
    })
    try {
      const query =
        `What happened during the ${ev.category} event ` +
        `at ${formatTs(ev.start_seconds)}–${formatTs(ev.end_seconds)}` +
        (activeRecording?.camera_name ? ` on ${activeRecording.camera_name}` : '') +
        '?'
      const res = await fetch('/api/investigations/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query,
          recording_ids: [ev.recording_id],
          time_start_seconds: ev.start_seconds,
          time_end_seconds: ev.end_seconds,
        }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setInvestigateErrors(prev => ({
          ...prev,
          [ev.event_id]: (err as { detail?: string }).detail || 'Investigation failed',
        }))
      } else {
        onInvestigated(await res.json())
      }
    } catch {
      setInvestigateErrors(prev => ({
        ...prev,
        [ev.event_id]: 'Network error. Is the backend running?',
      }))
    }
    setInvestigatingId(null)
  }

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h2 className="text-lg font-semibold mb-1">Incident Timeline</h2>
        <p className="text-sm text-gray-500 mb-4">
          {indexedRecordings.length === 0
            ? 'No indexed recordings yet — upload videos first.'
            : 'Detected event sequences with evidence clips, ordered by start time.'}
        </p>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
          <label className="col-span-2 text-sm">
            <span className="block font-medium text-gray-700 mb-1">Recording</span>
            <select
              value={recordingId}
              onChange={e => setRecordingId(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="">Select a recording…</option>
              {indexedRecordings.map(r => (
                <option key={r.recording_id} value={r.recording_id}>
                  {r.camera_name ? `${r.camera_name} — ` : ''}{r.original_filename}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="block font-medium text-gray-700 mb-1">Start (s)</span>
            <input
              type="number"
              min="0"
              step="0.1"
              value={start}
              onChange={e => setStart(e.target.value)}
              placeholder="0"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </label>
          <label className="text-sm">
            <span className="block font-medium text-gray-700 mb-1">End (s)</span>
            <input
              type="number"
              min="0"
              step="0.1"
              value={end}
              onChange={e => setEnd(e.target.value)}
              placeholder="∞"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </label>
          <label className="text-sm">
            <span className="block font-medium text-gray-700 mb-1">Min confidence</span>
            <input
              type="number"
              min="0"
              max="1"
              step="0.05"
              value={minConfidence}
              onChange={e => setMinConfidence(e.target.value)}
              placeholder="0"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </label>
          <label className="text-sm flex items-end pb-2 gap-2">
            <input
              type="checkbox"
              checked={includeSingleFrame}
              onChange={e => setIncludeSingleFrame(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-indigo-600"
            />
            <span className="text-gray-700">Include single-frame candidates</span>
          </label>
        </div>

        <button
          onClick={fetchEvents}
          disabled={loading || !recordingId}
          className="px-5 py-2 bg-indigo-600 text-white rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
        >
          {loading ? 'Loading…' : 'Load timeline'}
        </button>
        {error && <p className="text-sm text-red-600 mt-3">{error}</p>}
      </div>

      {response && (
        <div className="space-y-3">
          <div className="flex items-start justify-between gap-2 flex-wrap">
            <p className="text-sm text-gray-500">
              {response.count} event(s)
              {' · '}timestamp source: {response.timestamp_source}
            </p>
          </div>
          {response.events.length > 0 ? (
            response.events.map((ev, i) => (
              <TemporalEventCard
                key={ev.event_id}
                event={ev}
                index={i + 1}
                cameraName={activeRecording?.camera_name}
                recordingFilename={activeRecording?.original_filename}
                investigating={investigatingId === ev.event_id}
                investigateError={investigateErrors[ev.event_id] ?? null}
                onInvestigate={handleInvestigate}
              />
            ))
          ) : (
            <div className="bg-gray-50 rounded-xl border border-gray-200 p-8 text-center text-gray-500">
              No events detected for this recording
              {start !== '' || end !== '' ? ' in the selected time window' : ''}.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
