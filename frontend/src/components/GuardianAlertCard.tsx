'use client'
import type { GuardianAlert, AlertSeverity, HazardCategory } from '@/types'

interface Props {
  alert: GuardianAlert
  onAcknowledge: (alertId: string) => void
  onDismiss: (alertId: string) => void
  updating: boolean
}

function formatTs(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function severityBadge(s: AlertSeverity): string {
  if (s === 'critical' || s === 'high') return 'rt-badge-red'
  if (s === 'medium') return 'rt-badge-amber'
  return 'rt-badge-gray'
}

function reviewBadge(status: string): string {
  if (status === 'acknowledged') return 'rt-badge-green'
  if (status === 'dismissed') return 'rt-badge-gray'
  return 'rt-badge-amber'
}

const CATEGORY_LABELS: Record<HazardCategory, string> = {
  fire_smoke:            'Fire / Smoke',
  person_fall_injury:    'Person Fall / Injury',
  vehicle_collision:     'Vehicle Collision',
  dangerous_animal:      'Dangerous Animal',
  weapon_visible:        'Weapon Visible',
  restricted_area_entry: 'Restricted Area Entry',
  other_hazard:          'Other Hazard',
}

export default function GuardianAlertCard({ alert: al, onAcknowledge, onDismiss, updating }: Props) {
  const thumb = al.evidence_frames?.[0]
  const catLabel = CATEGORY_LABELS[al.hazard_category] ?? al.hazard_category
  const isDismissed = al.review_status === 'dismissed'
  const isAcked = al.review_status === 'acknowledged'

  return (
    <div className={`rt-card p-4 transition-opacity ${isDismissed ? 'opacity-50' : ''}`}>
      <div className="flex gap-4">
        {/* Thumbnail */}
        {thumb && (
          <div className="flex-shrink-0">
            <img
              src={`/${thumb.frame_path}`}
              alt="Evidence frame"
              className="w-32 h-24 object-cover rounded-lg bg-[#F1F5F9]"
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
            <p className="text-[11px] text-[#94A3B8] text-center mt-0.5">
              @{formatTs(thumb.timestamp_seconds)}
            </p>
          </div>
        )}

        {/* Details */}
        <div className="flex-1 min-w-0 space-y-2">
          {/* Header */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className={severityBadge(al.severity)}>{al.severity}</span>
            <span className="rt-badge-blue">{catLabel}</span>
            <span className={reviewBadge(al.review_status)}>{al.review_status}</span>
            {al.is_mocked && <span className="rt-badge-gray">Simulated</span>}
          </div>

          {/* Time + camera */}
          <div className="text-sm text-[#64748B]">
            {(al.camera_name || al.original_filename) && (
              <span className="font-medium text-[#0F172A] mr-1">
                {al.camera_name ?? al.original_filename}
              </span>
            )}
            <span className="font-mono">
              {formatTs(al.time_range_start)}–{formatTs(al.time_range_end)}
            </span>
            <span className="text-[#94A3B8] ml-2 text-xs">
              {(al.confidence * 100).toFixed(0)}% confidence
            </span>
          </div>

          {/* Description */}
          <p className="text-sm text-[#0F172A]">{al.description}</p>

          {/* Actions */}
          <div className="flex gap-2 pt-1">
            <button
              onClick={() => onAcknowledge(al.alert_id)}
              disabled={isAcked || updating}
              className="rt-btn-secondary text-xs py-1 px-3 disabled:opacity-40"
            >
              {isAcked ? 'Acknowledged' : 'Acknowledge'}
            </button>
            <button
              onClick={() => onDismiss(al.alert_id)}
              disabled={isDismissed || updating}
              className="rt-btn-ghost text-xs py-1 px-3 text-[#94A3B8] disabled:opacity-40"
            >
              {isDismissed ? 'Dismissed' : 'Dismiss'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
