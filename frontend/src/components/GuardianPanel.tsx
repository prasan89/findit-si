'use client'
import { useCallback, useEffect, useState } from 'react'
import type { GuardianAlert, AlertReviewStatus, Recording } from '@/types'
import GuardianAlertCard from './GuardianAlertCard'

interface Props {
  recordings: Recording[]
}

export default function GuardianPanel({ recordings }: Props) {
  const [recordingId, setRecordingId] = useState('')
  const [alerts, setAlerts] = useState<GuardianAlert[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [updatingId, setUpdatingId] = useState<string | null>(null)
  const [filterStatus, setFilterStatus] = useState<AlertReviewStatus | 'all'>('all')
  const [filterSeverity, setFilterSeverity] = useState<string>('all')

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
      const params = new URLSearchParams()
      if (filterStatus !== 'all') params.set('review_status', filterStatus)
      if (filterSeverity !== 'all') params.set('severity', filterSeverity)
      const qs = params.toString()
      const res = await fetch(
        `/api/recordings/${recordingId}/guardian-alerts${qs ? `?${qs}` : ''}`
      )
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setError((err as { detail?: string }).detail || 'Failed to load alerts')
        return
      }
      const data = await res.json()
      setAlerts(data.alerts ?? [])
    } catch {
      setError('Network error. Is the backend running?')
    }
    setLoading(false)
  }, [recordingId, filterStatus, filterSeverity])

  useEffect(() => {
    if (recordingId) fetchAlerts()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordingId])

  const handleUpdateStatus = async (alertId: string, newStatus: AlertReviewStatus) => {
    setUpdatingId(alertId)
    try {
      const res = await fetch(`/api/recordings/${recordingId}/guardian-alerts/${alertId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review_status: newStatus }),
      })
      if (res.ok) {
        setAlerts(prev =>
          prev.map(a => a.alert_id === alertId ? { ...a, review_status: newStatus } : a)
        )
      }
    } finally {
      setUpdatingId(null)
    }
  }

  const hasMocked = alerts.some(a => a.is_mocked)
  const newCount = alerts.filter(a => a.review_status === 'new').length
  const criticalCount = alerts.filter(a => a.severity === 'critical' || a.severity === 'high').length

  return (
    <div className="space-y-5">
      <div className="rt-card p-6">
        <div className="mb-4">
          <h2 className="rt-section-title">Guardian — Hazard Alerts</h2>
          <p className="rt-section-subtitle">
            {indexedRecordings.length === 0
              ? 'No indexed recordings yet — upload videos first.'
              : 'Upload-time hazard detection results. Alerts are generated automatically during video processing.'}
          </p>
        </div>

        {hasMocked && (
          <div className="rt-banner-info text-xs mb-4">
            This recording was processed in simulation mode (no API key configured). Alerts shown are illustrative and not based on real video analysis.
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
          <div className="sm:col-span-1">
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
            <label className="rt-label">Status</label>
            <select
              value={filterStatus}
              onChange={e => setFilterStatus(e.target.value as AlertReviewStatus | 'all')}
              className="rt-input"
            >
              <option value="all">All</option>
              <option value="new">New</option>
              <option value="acknowledged">Acknowledged</option>
              <option value="dismissed">Dismissed</option>
            </select>
          </div>
          <div>
            <label className="rt-label">Severity</label>
            <select
              value={filterSeverity}
              onChange={e => setFilterSeverity(e.target.value)}
              className="rt-input"
            >
              <option value="all">All</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={fetchAlerts}
            disabled={loading || !recordingId}
            className="rt-btn-primary"
          >
            {loading ? 'Loading…' : 'Load alerts'}
          </button>
          {alerts.length > 0 && !loading && (
            <span className="text-sm text-[#64748B]">
              {alerts.length} alert{alerts.length !== 1 ? 's' : ''}
              {newCount > 0 && <span className="text-[#D97706] ml-1">· {newCount} new</span>}
              {criticalCount > 0 && <span className="text-[#DC2626] ml-1">· {criticalCount} critical/high</span>}
            </span>
          )}
        </div>
        {error && <div className="rt-banner-danger mt-3">{error}</div>}
      </div>

      {/* Alert list */}
      {!loading && alerts.length > 0 && (
        <div className="space-y-3">
          {alerts.map(al => (
            <GuardianAlertCard
              key={al.alert_id}
              alert={al}
              updating={updatingId === al.alert_id}
              onAcknowledge={id => handleUpdateStatus(id, 'acknowledged')}
              onDismiss={id => handleUpdateStatus(id, 'dismissed')}
            />
          ))}
        </div>
      )}

      {!loading && alerts.length === 0 && recordingId && !error && (
        <div className="rt-card rt-empty py-12">
          <svg viewBox="0 0 24 24" fill="none" className="w-8 h-8 text-[#E2E8F0] mb-2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <p className="text-sm font-medium text-[#64748B]">No hazards detected</p>
          <p className="text-xs text-[#94A3B8] mt-0.5">
            Guardian found no safety hazards in this recording.
          </p>
        </div>
      )}
    </div>
  )
}
