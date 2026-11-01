'use client'
import { useCallback, useEffect, useState } from 'react'
import TemporalEventCard from './TemporalEventCard'
import type { InvestigationResult, Recording, TemporalEvent, TemporalEventsResponse } from '@/types'

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
    setInvestigateErrors(prev => { const n = { ...prev }; delete n[ev.event_id]; return n })
    try {
      const query =
        `What happened during the ${ev.category} event ` +
        `at ${formatTs(ev.start_seconds)}–${formatTs(ev.end_seconds)}` +
        (activeRecording?.camera_name ? ` on ${activeRecording.camera_name}` : '') + '?'
      const res = await fetch('/api/investigations/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, recording_ids: [ev.recording_id], time_start_seconds: ev.start_seconds, time_end_seconds: ev.end_seconds }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setInvestigateErrors(prev => ({ ...prev, [ev.event_id]: (err as { detail?: string }).detail || 'Investigation failed' }))
      } else {
        onInvestigated(await res.json())
      }
    } catch {
      setInvestigateErrors(prev => ({ ...prev, [ev.event_id]: 'Network error.' }))
    }
    setInvestigatingId(null)
  }

  return (
    <div className="space-y-5">
      {/* Filter card */}
      <div className="rt-card p-6">
        <div className="mb-4">
          <h2 className="rt-section-title">Incident Timeline</h2>
          <p className="rt-section-subtitle">
            {indexedRecordings.length === 0
              ? 'No indexed recordings yet — upload videos first.'
              : 'Detected event sequences ordered by start time.'}
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
          <div className="sm:col-span-2">
            <label className="rt-label">Recording</label>
            <select
              value={recordingId}
              onChange={e => setRecordingId(e.target.value)}
              className="rt-input"
            >
              <option value="">Select a recording…</option>
              {indexedRecordings.map(r => (
                <option key={r.recording_id} value={r.recording_id}>
                  {r.camera_name ? `${r.camera_name} — ` : ''}{r.original_filename}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="rt-label">Start (s)</label>
            <input type="number" min="0" step="0.1" value={start} onChange={e => setStart(e.target.value)}
              placeholder="0" className="rt-input" />
          </div>
          <div>
            <label className="rt-label">End (s)</label>
            <input type="number" min="0" step="0.1" value={end} onChange={e => setEnd(e.target.value)}
              placeholder="∞" className="rt-input" />
          </div>
          <div>
            <label className="rt-label">Min confidence</label>
            <input type="number" min="0" max="1" step="0.05" value={minConfidence} onChange={e => setMinConfidence(e.target.value)}
              placeholder="0" className="rt-input" />
          </div>
          <div className="flex items-end pb-0.5 gap-2">
            <input type="checkbox" id="sf" checked={includeSingleFrame} onChange={e => setIncludeSingleFrame(e.target.checked)}
              className="h-4 w-4 rounded border-[#E2E8F0] text-[#2563EB] focus:ring-[#93C5FD] accent-[#2563EB]" />
            <label htmlFor="sf" className="text-sm text-[#64748B] cursor-pointer">Include single-frame</label>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button onClick={fetchEvents} disabled={loading || !recordingId} className="rt-btn-primary">
            {loading ? 'Loading…' : 'Load Timeline'}
          </button>
          {response && (
            <span className="text-sm text-[#64748B]">
              {response.count} event{response.count !== 1 ? 's' : ''} · {response.timestamp_source}
            </span>
          )}
        </div>
        {error && <div className="rt-banner-danger mt-3">{error}</div>}
      </div>

      {/* Events */}
      {response && (
        response.events.length > 0 ? (
          <div className="space-y-3">
            {response.events.map((ev, i) => (
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
            ))}
          </div>
        ) : (
          <div className="rt-card rt-empty py-12">
            <svg viewBox="0 0 24 24" fill="none" className="w-8 h-8 text-[#E2E8F0] mb-2">
              <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            <p className="text-sm font-medium text-[#64748B]">No events detected</p>
            <p className="text-xs text-[#94A3B8] mt-0.5">
              {start !== '' || end !== '' ? 'Try widening the time window' : 'No events found in this recording'}
            </p>
          </div>
        )
      )}
    </div>
  )
}
