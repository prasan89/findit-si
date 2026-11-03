'use client'
import { useState } from 'react'
import type { Recording } from '@/types'
import type { FeedItem } from '@/lib/guardianRouting'
import { categoryLabel, isPoliceEligibleAlert, isPoliceEligibleEvent } from '@/lib/guardianRouting'
import VideoPlayerModal from './VideoPlayerModal'

interface Props {
  item: FeedItem | null
  recording?: Recording | null
  onClose: () => void
}

function fmtTs(secs: number): string {
  if (!isFinite(secs)) return '0:00'
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function fmtTimestamp(item: FeedItem): string {
  // Prefer absolute timestamp from alert or event
  const absolute = item.alert?.created_at ?? item.event?.timestamp_absolute
  if (absolute) {
    try {
      return new Date(absolute).toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      // fall through
    }
  }
  return fmtTs(item.timestamp_seconds)
}

const CATEGORY_COLORS: Record<string, string> = {
  fire_smoke:              'bg-red-100 text-red-700',
  person_fall_injury:      'bg-orange-100 text-orange-700',
  vehicle_collision:       'bg-orange-100 text-orange-700',
  dangerous_animal:        'bg-yellow-100 text-yellow-700',
  weapon_visible:          'bg-red-100 text-red-700',
  restricted_area_entry:   'bg-purple-100 text-purple-700',
  other_hazard:            'bg-amber-100 text-amber-700',
  'possible-theft':        'bg-red-100 text-red-700',
  'possible-assault':      'bg-red-100 text-red-700',
  confrontation:           'bg-red-100 text-red-700',
  robbery:                 'bg-red-100 text-red-700',
  'chain-snatching':       'bg-red-100 text-red-700',
  'vehicle-theft':         'bg-red-100 text-red-700',
  carjacking:              'bg-red-100 text-red-700',
  'dangerous-threat':      'bg-red-100 text-red-700',
  'armed-person':          'bg-red-100 text-red-700',
  'suspicious-approach-departure': 'bg-amber-100 text-amber-700',
  'sudden-fall':           'bg-orange-100 text-orange-700',
  'other-anomaly':         'bg-slate-100 text-slate-600',
  person_detected:         'bg-blue-100 text-blue-700',
  vehicle_detected:        'bg-blue-100 text-blue-700',
  narrative_step:          'bg-slate-100 text-slate-600',
}

const SEVERITY_COLORS: Record<string, string> = {
  Critical: 'bg-red-100 text-red-700',
  Review:   'bg-amber-100 text-amber-700',
  Routine:  'bg-slate-100 text-slate-600',
}

// Camera icon for placeholder
function CameraPlaceholder() {
  return (
    <div className="flex flex-col items-center gap-2 text-slate-400">
      <svg viewBox="0 0 24 24" fill="none" className="w-10 h-10" aria-hidden="true">
        <rect x="2" y="6" width="20" height="14" rx="2" stroke="currentColor" strokeWidth="1.5"/>
        <circle cx="12" cy="13" r="3.5" stroke="currentColor" strokeWidth="1.5"/>
        <path d="M8 6l1.5-2h5L16 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
      <span className="text-xs">No preview</span>
    </div>
  )
}

// Confidence bar
function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color =
    pct >= 75 ? 'bg-red-500' :
    pct >= 50 ? 'bg-amber-500' :
    'bg-slate-400'
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-medium text-slate-700 shrink-0">{pct}%</span>
    </div>
  )
}

export default function GuardianEvidencePanel({ item, recording, onClose }: Props) {
  const [videoOpen, setVideoOpen] = useState(false)
  const [thumbError, setThumbError] = useState(false)

  if (!item) return null

  const firstFrame = item.evidence_frames[0]
  const thumbSrc = !thumbError
    ? (firstFrame
        ? `/frames/${encodeURIComponent(firstFrame.frame_path)}`
        : recording
          ? `/thumbnails/${recording.recording_id}.jpg`
          : null)
    : null

  const label = categoryLabel(item.category)
  const camName = item.camera_name ?? recording?.camera_name ?? '—'
  const camLoc = item.camera_location ?? recording?.camera_location

  const categoryColor = CATEGORY_COLORS[item.category] ?? 'bg-slate-100 text-slate-600'
  const severityColor = SEVERITY_COLORS[item.severityTag] ?? 'bg-slate-100 text-slate-600'

  const isPoliceEligible = item.alert
    ? isPoliceEligibleAlert(item.alert)
    : item.event
      ? isPoliceEligibleEvent(item.event)
      : false
  const isAcknowledged = item.review_status === 'acknowledged'
  const showDispatch = isPoliceEligible && isAcknowledged

  const extraFrames = item.evidence_frames.slice(1)

  return (
    <div className="flex flex-col h-full w-80 bg-white border-l border-[#E2E8F0]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#E2E8F0] shrink-0">
        <span className="text-sm font-semibold text-slate-900">Evidence Detail</span>
        <button
          onClick={onClose}
          className="w-7 h-7 rounded flex items-center justify-center text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors"
          aria-label="Close evidence panel"
        >
          <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4" aria-hidden="true">
            <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </button>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {/* Thumbnail */}
        <div className="px-4 pt-4">
          <div className="relative w-full aspect-video bg-slate-900 rounded-md overflow-hidden flex items-center justify-center">
            {thumbSrc ? (
              <img
                src={thumbSrc}
                alt="Evidence frame"
                className="w-full h-full object-cover"
                onError={() => setThumbError(true)}
              />
            ) : (
              <CameraPlaceholder />
            )}
            {recording && (
              <button
                onClick={() => setVideoOpen(true)}
                className="absolute inset-0 flex items-center justify-center bg-black/30 opacity-0 hover:opacity-100 transition-opacity"
                aria-label="Play recording"
              >
                <div className="w-11 h-11 rounded-full bg-white/90 flex items-center justify-center shadow-md">
                  <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5 ml-0.5" aria-hidden="true">
                    <path d="M6 4l14 8-14 8V4z" fill="#0F172A"/>
                  </svg>
                </div>
              </button>
            )}
          </div>
        </div>

        {/* Metadata section */}
        <div className="px-4 pt-4 space-y-3">
          {/* Badges row */}
          <div className="flex flex-wrap gap-1.5">
            <span className={`inline-flex items-center text-xs font-medium px-2 py-0.5 rounded-full ${categoryColor}`}>
              {label}
            </span>
            <span className={`inline-flex items-center text-xs font-medium px-2 py-0.5 rounded-full ${severityColor}`}>
              {item.severityTag}
            </span>
          </div>

          {/* Confidence */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs text-slate-500">Confidence</span>
            </div>
            <ConfidenceBar value={item.confidence} />
          </div>

          {/* Key-value rows */}
          <div className="space-y-2">
            <div className="flex items-start justify-between gap-2 text-xs">
              <span className="text-slate-500 shrink-0">Timestamp</span>
              <span className="text-slate-800 font-medium text-right">{fmtTimestamp(item)}</span>
            </div>
            <div className="flex items-start justify-between gap-2 text-xs">
              <span className="text-slate-500 shrink-0">Camera</span>
              <span className="text-slate-800 font-medium text-right">
                {camLoc ? `${camName} (${camLoc})` : camName}
              </span>
            </div>
          </div>
        </div>

        {/* Description */}
        {item.description && (
          <div className="px-4 pt-3">
            <p className="text-sm text-slate-600 leading-relaxed">{item.description}</p>
          </div>
        )}

        {/* Evidence frames strip */}
        {extraFrames.length > 0 && (
          <div className="px-4 pt-4">
            <p className="text-xs text-slate-400 mb-2 font-medium uppercase tracking-wide">
              Evidence Frames
            </p>
            <div className="flex gap-1.5 overflow-x-auto pb-1">
              {/* Lead frame */}
              {firstFrame && (
                <img
                  key={firstFrame.frame_path}
                  src={`/frames/${encodeURIComponent(firstFrame.frame_path)}`}
                  alt={`Frame at ${fmtTs(firstFrame.timestamp_seconds)}`}
                  className="h-12 w-auto rounded shrink-0 object-cover bg-slate-200"
                />
              )}
              {extraFrames.map(f => (
                <img
                  key={f.frame_path}
                  src={`/frames/${encodeURIComponent(f.frame_path)}`}
                  alt={`Frame at ${fmtTs(f.timestamp_seconds)}`}
                  className="h-12 w-auto rounded shrink-0 object-cover bg-slate-200"
                />
              ))}
            </div>
          </div>
        )}

        {/* Bottom spacer so content is not obscured by action bar */}
        <div className="h-4" />
      </div>

      {/* Actions */}
      <div className="px-4 pb-4 pt-3 border-t border-[#E2E8F0] space-y-2 shrink-0">
        {recording && (
          <button
            onClick={() => setVideoOpen(true)}
            className="rt-btn-primary w-full justify-center text-sm py-2.5"
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4 mr-1.5" aria-hidden="true">
              <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            Open Recording
          </button>
        )}
        {showDispatch && (
          <button
            className="w-full justify-center text-sm py-2.5 rounded-lg font-medium bg-red-600 text-white hover:bg-red-700 active:bg-red-800 transition-colors flex items-center"
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4 mr-1.5" aria-hidden="true">
              <path d="M15 7l-6 6M9 7h6v6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            Dispatch Alert
          </button>
        )}
      </div>

      {videoOpen && recording && (
        <VideoPlayerModal
          recording={recording}
          initialTimestamp={item.time_range_start}
          onClose={() => setVideoOpen(false)}
        />
      )}
    </div>
  )
}
