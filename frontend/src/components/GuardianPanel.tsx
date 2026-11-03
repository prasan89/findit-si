'use client'
import { useCallback, useEffect, useState } from 'react'
import type { GuardianAlert, Recording, Event } from '@/types'
import GuardianWhatsApp from './GuardianWhatsApp'

interface Props {
  recordings: Recording[]
}

export default function GuardianPanel({ recordings }: Props) {
  const [recordingId, setRecordingId] = useState('')
  const [alerts, setAlerts] = useState<GuardianAlert[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [updatingId, setUpdatingId] = useState<string | null>(null) // kept for future status updates

  const indexedRecordings = recordings.filter(
    r => r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL'
  )

  useEffect(() => {
    if (!recordingId && indexedRecordings.length > 0) {
      setRecordingId(indexedRecordings[0].recording_id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordings])

  const fetchAlerts = useCallback(async () => {
    if (!recordingId) return
    setLoading(true)
    setError(null)
    try {
      const [alertRes, evRes] = await Promise.all([
        fetch(`/api/recordings/${recordingId}/guardian-alerts`),
        fetch(`/api/recordings/${recordingId}/events`),
      ])
      if (!alertRes.ok) {
        const err = await alertRes.json().catch(() => ({}))
        setError((err as { detail?: string }).detail || 'Failed to load alerts')
        return
      }
      const alertData = await alertRes.json()
      setAlerts(alertData.alerts ?? [])
      if (evRes.ok) {
        const evData = await evRes.json()
        setEvents(evData.events ?? [])
      }
    } catch {
      setError('Network error. Is the backend running?')
    }
    setLoading(false)
  }, [recordingId])

  useEffect(() => {
    if (recordingId) fetchAlerts()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordingId])

  const hasMocked = alerts.some(a => a.is_mocked)
  const activeRecording = indexedRecordings.find(r => r.recording_id === recordingId)

  return (
    <div className="space-y-5">
      <div className="rt-card p-6">
        <div className="mb-4">
          <h2 className="rt-section-title">Guardian — WhatsApp Alerts</h2>
          <p className="rt-section-subtitle">
            {indexedRecordings.length === 0
              ? 'No indexed recordings yet — upload videos first.'
              : 'Simulated alert notifications based on CCTV analysis. Select a recording to view.'}
          </p>
        </div>

        {hasMocked && (
          <div className="rt-banner-info text-xs mb-4">
            This recording was processed in simulation mode (no API key configured). Alerts shown are illustrative.
          </div>
        )}

        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-40">
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
          <button
            onClick={fetchAlerts}
            disabled={loading || !recordingId}
            className="rt-btn-primary"
          >
            {loading ? 'Loading…' : 'Load'}
          </button>
        </div>
        {error && <div className="rt-banner-danger mt-3">{error}</div>}
      </div>

      {/* WhatsApp simulation */}
      {!loading && activeRecording && (alerts.length > 0 || events.length > 0) && (
        <GuardianWhatsApp
          alerts={alerts}
          events={events}
          recording={activeRecording}
        />
      )}

      {!loading && activeRecording && alerts.length === 0 && events.length === 0 && !error && (
        <div className="rt-card rt-empty py-12">
          <svg viewBox="0 0 24 24" fill="none" className="w-8 h-8 text-[#E2E8F0] mb-2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <p className="text-sm font-medium text-[#64748B]">No activity recorded</p>
          <p className="text-xs text-[#94A3B8] mt-0.5">
            Guardian found no events or hazards in this recording.
          </p>
        </div>
      )}
    </div>
  )
}
