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

const SEVERITY_STYLES: Record<SeverityTag, { stripe: string; badge: string; icon: string }> = {
  Critical: {
    stripe: 'border-l-4 border-red-500',
    badge: 'bg-red-100 text-red-700',
    icon: '🚨',
  },
  Review: {
    stripe: 'border-l-4 border-amber-400',
    badge: 'bg-amber-100 text-amber-700',
    icon: '⚠️',
  },
  Routine: {
    stripe: '',
    badge: 'bg-slate-100 text-slate-600',
    icon: '📹',
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
}

function categoryIcon(category: string): string {
  return CATEGORY_ICONS[category] ?? '📋'
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
    ? `/api/frames/${encodeURIComponent(firstFrame.frame_path)}`
    : null

  const isAcknowledged = item.review_status === 'acknowledged'

  return (
    <div
      data-testid="guardian-message-card"
      className={`rt-card p-0 overflow-hidden cursor-pointer transition-shadow ${style.stripe}
        ${isSelected ? 'ring-2 ring-[#2563EB]' : 'hover:shadow-md'}`}
      onClick={() => onViewEvidence(item)}
    >
      {/* Card header */}
      <div className="flex items-center justify-between px-4 pt-3 pb-2 gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-base shrink-0">{icon}</span>
          <span className="text-sm font-bold text-[#0F172A] uppercase tracking-wide truncate">
            {label}
          </span>
          <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full shrink-0 ${style.badge}`}>
            {item.severityTag}
          </span>
        </div>
        <span className="text-xs text-[#64748B] shrink-0">{fmtTs(item.timestamp_seconds)}</span>
      </div>

      {/* Card body: thumbnail + details */}
      <div className="flex gap-3 px-4 pb-3">
        {/* Thumbnail */}
        <div className="w-28 h-20 rounded-lg bg-[#0F172A] overflow-hidden shrink-0 flex items-center justify-center">
          {thumbSrc ? (
            <img src={thumbSrc} alt="Evidence" className="w-full h-full object-cover" />
          ) : (
            <svg viewBox="0 0 24 24" fill="none" className="w-8 h-8 text-[#475569]">
              <rect x="2" y="3" width="20" height="14" rx="2" stroke="currentColor" strokeWidth="1.5"/>
              <path d="M9 8l6 4-6 4V8z" fill="currentColor"/>
            </svg>
          )}
        </div>

        {/* Details */}
        <div className="flex-1 min-w-0 space-y-1.5">
          <div className="flex items-center gap-1 text-xs text-[#475569]">
            <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5 shrink-0">
              <rect x="2" y="3" width="20" height="14" rx="2" stroke="currentColor" strokeWidth="1.5"/>
            </svg>
            <span className="truncate">{loc ? `${cam} (${loc})` : cam}</span>
          </div>

          <p className="text-xs text-[#1E293B] leading-relaxed line-clamp-2">
            {item.description || '—'}
          </p>

          {/* Confidence bar */}
          <div className="flex items-center gap-2">
            <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5 text-[#64748B] shrink-0">
              <path d="M4 20V14M8 20V10M12 20V4M16 20V10M20 20V14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            <div className="flex-1 h-1.5 bg-[#E2E8F0] rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${item.confidence >= 0.75 ? 'bg-red-500' : item.confidence >= 0.6 ? 'bg-amber-400' : 'bg-slate-400'}`}
                style={{ width: `${Math.round(item.confidence * 100)}%` }}
              />
            </div>
            <span className="text-xs font-medium text-[#475569] shrink-0">
              {Math.round(item.confidence * 100)}%
            </span>
          </div>
        </div>
      </div>

      {/* Footer: actions */}
      <div className="flex items-center gap-2 px-4 pb-3 border-t border-[#F1F5F9] pt-2">
        <button
          onClick={e => { e.stopPropagation(); onViewEvidence(item) }}
          className="rt-btn-primary text-xs py-1.5 px-3 gap-1"
          aria-label="View evidence"
        >
          <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5">
            <path d="M6 4l14 8-14 8V4z" fill="currentColor"/>
          </svg>
          View Evidence
        </button>

        {/* Only alerts can be acknowledged (they have server-side review_status) */}
        {item.source === 'alert' && (
          <button
            onClick={e => {
              e.stopPropagation()
              onAcknowledge(item, isAcknowledged ? 'new' as 'acknowledged' : 'acknowledged')
            }}
            className={`rt-btn-secondary text-xs py-1.5 px-3 gap-1 ${isAcknowledged ? 'opacity-60' : ''}`}
            aria-label={isAcknowledged ? 'Mark new' : 'Acknowledge'}
          >
            ✓ {isAcknowledged ? 'Acknowledged' : 'Acknowledge'}
          </button>
        )}

        {/* Simulation disclaimer on police tab */}
        {isPoliceTab && (
          <span className="ml-auto text-[10px] italic text-[#94A3B8]">Simulation</span>
        )}
      </div>
    </div>
  )
}
