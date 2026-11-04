'use client'
import type { FeedItem, SeverityTag } from '@/lib/guardianRouting'
import { categoryLabel } from '@/lib/guardianRouting'

interface Props {
  item: FeedItem
  isPoliceTab: boolean
  onViewEvidence: (item: FeedItem) => void
  onAcknowledge: (item: FeedItem, status: 'acknowledged' | 'dismissed') => void
  isSelected: boolean
}

function fmtTs(secs: number): string {
  if (!isFinite(secs)) return '0:00'
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

// Severity: left accent color + badge styles
const SEVERITY_STYLES: Record<SeverityTag, { border: string; badge: string; bar: string }> = {
  Critical: {
    border: '#DC2626',
    badge: 'bg-red-100 text-red-700',
    bar: 'bg-red-500',
  },
  Review: {
    border: '#D97706',
    badge: 'bg-amber-100 text-amber-700',
    bar: 'bg-amber-400',
  },
  Routine: {
    border: '#94A3B8',
    badge: 'bg-slate-100 text-slate-500',
    bar: 'bg-slate-400',
  },
}

const CATEGORY_ICONS: Record<string, string> = {
  fire_smoke: '🔥',
  weapon_visible: '🔫',
  dangerous_animal: '🐾',
  vehicle_collision: '🚗',
  person_fall_injury: '🏥',
  'possible-theft': '🚨',
  'possible-assault': '🚨',
  confrontation: '🚨',
  'suspicious-approach-departure': '⚠️',
  robbery: '🚨',
  'chain-snatching': '🚨',
  'vehicle-theft': '🚗',
  carjacking: '🚗',
  'dangerous-threat': '⚠️',
  'armed-person': '🔫',
  flood_water: '💧',
  electrical_hazard: '⚡',
  structural_hazard: '🏗️',
  unauthorized_entry: '🚨',
  vandalism: '🔨',
}

function categoryIcon(category: string): string {
  return CATEGORY_ICONS[category] ?? '📋'
}

// Inline SVG icons (Lucide-style)
function IconCamera({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <rect x="2" y="3" width="20" height="14" rx="2" stroke="currentColor" strokeWidth="1.5" />
      <path d="M9 8l6 4-6 4V8z" fill="currentColor" />
    </svg>
  )
}

function IconPlay({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path d="M6 4l14 8-14 8V4z" fill="currentColor" />
    </svg>
  )
}

function IconCheck({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path d="M20 6L9 17l-5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function IconDispatch({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function GuardianMessageCard({
  item,
  isPoliceTab,
  onViewEvidence,
  onAcknowledge,
  isSelected,
}: Props) {
  const style = SEVERITY_STYLES[item.severityTag]
  const label = categoryLabel(item.category)
  const icon = categoryIcon(item.category)
  const cam = item.camera_name ?? 'Unknown camera'
  const loc = item.camera_location

  const firstFrame = item.evidence_frames[0]
  const thumbSrc = firstFrame
    ? `/frames/${encodeURIComponent(firstFrame.frame_path)}`
    : null

  const isNew = item.review_status === 'new'
  const isAcknowledged = item.review_status === 'acknowledged'
  const confidencePct = Math.round(item.confidence * 100)

  return (
    <div
      data-testid="guardian-message-card"
      className={`relative bg-white rounded-lg border p-4 cursor-pointer transition-all overflow-hidden
        ${isSelected
          ? 'border-blue-400 bg-blue-50/40 shadow-md'
          : 'border-slate-200 hover:shadow-md hover:border-slate-300'
        }`}
      style={{ borderLeftWidth: '4px', borderLeftColor: style.border, borderLeftStyle: 'solid' }}
      onClick={() => onViewEvidence(item)}
    >
      {/* ── Horizontal layout ── */}
      <div className="flex gap-3">
        {/* Thumbnail — 60×45 */}
        <div className="w-[60px] h-[45px] rounded-md bg-slate-800 overflow-hidden shrink-0 flex items-center justify-center">
          {thumbSrc ? (
            <img src={thumbSrc} alt="Evidence" className="w-full h-full object-cover" />
          ) : (
            <IconCamera className="w-5 h-5 text-slate-400" />
          )}
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0 flex flex-col gap-1">

          {/* Top row: category icon badge + label + severity badge + timestamp */}
          <div className="flex items-center gap-2 flex-wrap">
            {/* Category icon in colored badge */}
            <span
              className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-slate-100 text-base shrink-0"
              aria-hidden="true"
            >
              {icon}
            </span>

            <span className="text-sm font-medium text-slate-800 leading-snug truncate">
              {label}
            </span>

            <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full shrink-0 ${style.badge}`}>
              {item.severityTag}
            </span>

            <span className="ml-auto text-xs text-slate-400 shrink-0 tabular-nums">
              {fmtTs(item.timestamp_seconds)}
            </span>
          </div>

          {/* Middle: description */}
          <p className="text-sm text-slate-600 leading-snug line-clamp-2">
            {item.description || '—'}
          </p>

          {/* Bottom row: camera + confidence + action buttons */}
          <div className="flex items-center gap-2 flex-wrap mt-0.5">
            {/* Camera name */}
            <span className="text-xs text-slate-400 truncate min-w-0 shrink">
              {loc ? `${cam} · ${loc}` : cam}
            </span>

            {/* Confidence bar — 80px wide */}
            <div className="flex items-center gap-1 shrink-0">
              <span className="text-xs tabular-nums text-slate-500">{confidencePct}%</span>
              <div className="w-20 h-1.5 bg-slate-200 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${style.bar}`}
                  style={{ width: `${confidencePct}%` }}
                />
              </div>
            </div>

            {/* Action buttons — pushed to the right */}
            <div className="ml-auto flex items-center gap-1 shrink-0" onClick={e => e.stopPropagation()}>
              {/* View button */}
              <button
                onClick={() => onViewEvidence(item)}
                className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors"
                aria-label="View evidence"
              >
                <IconPlay className="w-3 h-3" />
                View
              </button>

              {/* Acknowledge — only alert source, only when new */}
              {item.source === 'alert' && isNew && (
                <button
                  onClick={() => onAcknowledge(item, 'acknowledged')}
                  className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors"
                  aria-label="Acknowledge"
                >
                  <IconCheck className="w-3 h-3" />
                  Ack
                </button>
              )}

              {/* Acknowledge again (toggle) — when already acknowledged */}
              {item.source === 'alert' && isAcknowledged && (
                <button
                  onClick={() => onAcknowledge(item, 'acknowledged')}
                  className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-slate-200 text-slate-500 opacity-60 hover:opacity-80 transition-opacity"
                  aria-label="Acknowledged"
                >
                  <IconCheck className="w-3 h-3" />
                  Acked
                </button>
              )}

              {/* Dismiss (delete from feed) */}
              {item.source === 'alert' && (
                <button
                  onClick={() => onAcknowledge(item, 'dismissed')}
                  className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-slate-200 text-red-500 hover:bg-red-50 hover:border-red-200 transition-colors"
                  aria-label="Dismiss alert"
                >
                  <svg viewBox="0 0 24 24" fill="none" className="w-3 h-3">
                    <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                  </svg>
                  Dismiss
                </button>
              )}

              {/* Dispatch — police tab, alert source only */}
              {isPoliceTab && item.source === 'alert' && (
                <button
                  onClick={() => {/* dispatch simulation */}}
                  className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-slate-200 text-blue-600 hover:bg-blue-50 transition-colors"
                  aria-label="Dispatch (simulation)"
                >
                  <IconDispatch className="w-3 h-3" />
                  Dispatch
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Simulation footer label — police tab only */}
      {isPoliceTab && (
        <div className="mt-2 pt-2 border-t border-slate-100 text-right">
          <span className="text-[10px] italic text-slate-400">Simulation</span>
        </div>
      )}
    </div>
  )
}
