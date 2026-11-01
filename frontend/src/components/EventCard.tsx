'use client'
import { useState } from 'react'
import type { Event, EvidenceFrame } from '@/types'

interface Props {
  event: Event
  index: number
  highlight?: boolean
  onPlayEvidence?: (recordingId: string, timestamp: number) => void
}

const MAX_VISIBLE_FACTS = 5

function formatTs(secs: number): string {
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = Math.floor(secs % 60)
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`
}

function confidenceBadge(score: number): string {
  if (score >= 0.75) return 'rt-badge-green'
  if (score >= 0.5) return 'rt-badge-amber'
  return 'rt-badge-red'
}

function confidenceLabel(score: number, pct: number): string {
  if (score >= 0.75) return `High confidence (${pct}%)`
  if (score >= 0.5) return `Medium confidence (${pct}%)`
  return `Low confidence (${pct}%)`
}

function relevanceLabel(score: number, pct: number): string {
  if (score >= 0.75) return `Strong match (${pct}%)`
  if (score >= 0.5) return `Partial match (${pct}%)`
  return `Weak match (${pct}%)`
}

function eventTypeBadge(type: string): string {
  if (type.includes('assault') || type.includes('fall')) return 'rt-badge-red'
  if (type.includes('theft') || type.includes('confrontation') || type.includes('suspicious')) return 'rt-badge-amber'
  return 'rt-badge-blue'
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  person_detected:                 'Person',
  vehicle_detected:                'Vehicle',
  bird_detected:                   'Bird',
  unattended_object:               'Object',
  entry_exit_detected:             'Entry/Exit',
  scene_change:                    'Scene Change',
  'possible-theft':                'Possible theft',
  'possible-assault':              'Possible assault',
  confrontation:                   'Confrontation',
  'sudden-fall':                   'Sudden fall',
  'suspicious-approach-departure': 'Suspicious activity',
  'other-anomaly':                 'Anomaly',
}

export default function EventCard({ event: ev, index, highlight, onPlayEvidence }: Props) {
  const [activeFrame, setActiveFrame] = useState<EvidenceFrame | null>(
    ev.evidence_frames.find(f => f.role === 'during') ?? ev.evidence_frames[0] ?? null
  )
  const typeLabel = EVENT_TYPE_LABELS[ev.event_type] ?? ev.event_type
  const hasRelevance = ev.relevance_score !== undefined && ev.relevance_score !== null

  const tsLabel = ev.timestamp_absolute
    ? new Date(ev.timestamp_absolute).toLocaleString()
    : `Video @${formatTs(ev.timestamp_seconds)}`
  const cameraLabel = ev.camera_name || `Camera ${ev.recording_id.slice(0, 8)}`
  const hasSpan = ev.start_seconds != null && ev.end_seconds != null && (ev.end_seconds ?? 0) > (ev.start_seconds ?? 0)

  const facts = ev.observed_facts.filter(
    f => !f.startsWith('Candidate ') && !f.startsWith('Observed sequence span')
  )
  const visibleFacts = facts.slice(0, MAX_VISIBLE_FACTS)
  const hiddenFacts = facts.slice(MAX_VISIBLE_FACTS)

  return (
    <div className={`rt-card p-4 transition-shadow hover:shadow-md ${highlight ? 'ring-2 ring-[#2563EB] ring-offset-1' : ''}`}>
      <div className="flex gap-4">
        {/* Evidence frame */}
        <div className="flex-shrink-0 space-y-2">
          {activeFrame && (
            <div>
              <img
                src={`/${activeFrame.frame_path}`}
                alt={`Evidence frame (${activeFrame.role})`}
                title={`${activeFrame.frame_path} @ ${activeFrame.timestamp_seconds.toFixed(1)}s`}
                className="w-40 h-28 object-cover rounded-lg bg-[#F1F5F9]"
                onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
              />
              <div className="text-[11px] text-[#94A3B8] text-center mt-1">
                t={activeFrame.timestamp_seconds.toFixed(1)}s · {activeFrame.role}
              </div>
            </div>
          )}
          {onPlayEvidence && (
            <button
              onClick={() => onPlayEvidence(ev.recording_id, ev.timestamp_seconds)}
              className="rt-btn-secondary text-xs py-1 px-2 w-full"
            >
              <svg viewBox="0 0 24 24" fill="none" className="w-3 h-3">
                <path d="M8 5v14l11-7L8 5z" fill="currentColor"/>
              </svg>
              Preview
            </button>
          )}
          {ev.evidence_frames.length > 1 && (
            <div className="flex gap-1 justify-center">
              {(['before', 'during', 'after'] as const).map(role => {
                const frame = ev.evidence_frames.find(f => f.role === role)
                if (!frame) return null
                const isActive = activeFrame?.frame_path === frame.frame_path
                return (
                  <button
                    key={role}
                    onClick={() => setActiveFrame(frame)}
                    className={`text-xs px-2 py-0.5 rounded-lg border transition-colors ${
                      isActive
                        ? 'bg-[#2563EB] text-white border-[#2563EB]'
                        : 'bg-white text-[#64748B] border-[#E2E8F0] hover:bg-[#F1F5F9]'
                    }`}
                  >
                    {role}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Event details */}
        <div className="flex-1 min-w-0 space-y-2">
          {/* Header row */}
          <div className="flex items-start justify-between gap-2 flex-wrap">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="rt-badge-gray font-mono text-xs">{String(index).padStart(2, '0')}</span>
              <span className={eventTypeBadge(ev.event_type)}>{typeLabel}</span>
              {ev.is_primary_finding && (
                <span className="rt-badge bg-[#EFF6FF] text-[#2563EB] ring-1 ring-[#BFDBFE]">★ Primary finding</span>
              )}
              {hasRelevance ? (
                <span className={confidenceBadge(ev.relevance_score as number)}>
                  {relevanceLabel(ev.relevance_score as number, Math.round((ev.relevance_score as number) * 100))}
                </span>
              ) : (
                <span className={confidenceBadge(ev.confidence)}>
                  {confidenceLabel(ev.confidence, Math.round(ev.confidence * 100))}
                </span>
              )}
              {!ev.has_reliable_timestamp && (
                <span className="rt-badge-amber">Relative timestamp</span>
              )}
            </div>
          </div>

          {/* Camera and time */}
          <div className="text-sm text-[#64748B]">
            <span className="font-medium text-[#0F172A]" title={ev.recording_filename || ev.recording_id}>
              {cameraLabel}
            </span>
            {ev.camera_location && <span className="ml-1">· {ev.camera_location}</span>}
            {ev.recording_filename && (
              <span className="text-[#94A3B8] ml-1 text-xs">· {ev.recording_filename}</span>
            )}
            <span className="mx-2 text-[#E2E8F0]">·</span>
            <span>{tsLabel}</span>
            {hasRelevance && (
              <span className="text-[#94A3B8] ml-2 text-xs">
                detection {(ev.confidence * 100).toFixed(0)}%
              </span>
            )}
          </div>

          {hasSpan && (
            <div className="text-xs text-[#94A3B8] font-mono">
              {formatTs(ev.start_seconds as number)}–{formatTs(ev.end_seconds as number)}
              {' '}({((ev.end_seconds as number) - (ev.start_seconds as number)).toFixed(1)}s)
            </div>
          )}

          {/* Primary match callout */}
          {ev.primary_match && (
            <div className="rt-banner-info text-xs">
              <span className="font-semibold">Primary finding: </span>
              {ev.primary_match}
            </div>
          )}

          {/* Action description */}
          <p className="text-sm font-medium text-[#0F172A]">{ev.action_description}</p>

          {/* Tracked entities */}
          {ev.entities && ev.entities.length > 0 && (
            <details>
              <summary className="text-xs text-[#2563EB] cursor-pointer hover:text-[#1D4ED8] font-medium">
                Tracked entities ({ev.entities.length})
              </summary>
              <ul className="text-xs text-[#64748B] mt-1.5 space-y-1 pl-2">
                {ev.entities.map(ent => (
                  <li key={ent.entity_id} className="flex gap-2 items-baseline">
                    <span className="text-[#94A3B8] shrink-0">·</span>
                    <span>{ent.display_label ?? ent.head ?? ent.entity_id}</span>
                    {ent.first_seen_seconds != null && ent.last_seen_seconds != null && (
                      <span className="text-[#94A3B8] font-mono">{formatTs(ent.first_seen_seconds)}–{formatTs(ent.last_seen_seconds)}</span>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {/* Action sequence */}
          {ev.action_sequence && ev.action_sequence.length > 0 && (
            <details>
              <summary className="text-xs text-[#2563EB] cursor-pointer hover:text-[#1D4ED8] font-medium">
                Action sequence ({ev.action_sequence.length} step{ev.action_sequence.length === 1 ? '' : 's'})
              </summary>
              <ol className="mt-1.5 space-y-1">
                {ev.action_sequence.map(step => (
                  <li key={step.step} className="text-xs text-[#64748B] flex gap-2 items-baseline">
                    <span className="text-[#94A3B8] font-mono shrink-0 w-5">{step.step}.</span>
                    <span className="flex-1">{step.description}</span>
                    {step.start_seconds != null && step.end_seconds != null && (
                      <span className="text-[#94A3B8] font-mono shrink-0">
                        {formatTs(step.start_seconds)}–{formatTs(step.end_seconds)}
                      </span>
                    )}
                    {step.frame_path && (
                      <button
                        onClick={() => setActiveFrame({
                          frame_path: step.frame_path as string,
                          timestamp_seconds: step.start_seconds ?? ev.timestamp_seconds,
                          role: 'during',
                        })}
                        className="text-[#2563EB] hover:text-[#1D4ED8] shrink-0 text-xs"
                      >
                        view
                      </button>
                    )}
                  </li>
                ))}
              </ol>
            </details>
          )}

          {/* Observed facts */}
          {facts.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold text-[#16A34A] uppercase tracking-wide mb-1">Observed</p>
              <ul className="text-xs text-[#64748B] space-y-0.5">
                {visibleFacts.map((f, i) => (
                  <li key={i} className="flex gap-1.5">
                    <svg viewBox="0 0 20 20" fill="none" className="w-3.5 h-3.5 text-[#16A34A] mt-0.5 shrink-0">
                      <path d="M16 5l-8 8-4-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                    {f}
                  </li>
                ))}
              </ul>
              {hiddenFacts.length > 0 && (
                <details className="mt-1">
                  <summary className="text-xs text-[#64748B] cursor-pointer hover:text-[#0F172A]">
                    Show all {facts.length} observations ({hiddenFacts.length} more)
                  </summary>
                  <ul className="text-xs text-[#64748B] space-y-0.5 mt-1">
                    {hiddenFacts.map((f, i) => (
                      <li key={i} className="flex gap-1.5">
                        <svg viewBox="0 0 20 20" fill="none" className="w-3.5 h-3.5 text-[#16A34A] mt-0.5 shrink-0">
                          <path d="M16 5l-8 8-4-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                        </svg>
                        {f}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}

          {/* Inferred claims */}
          {ev.inferred_claims.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold text-[#D97706] uppercase tracking-wide mb-1">Inferred (unconfirmed)</p>
              <ul className="text-xs text-[#64748B] space-y-0.5">
                {ev.inferred_claims.map((c, i) => (
                  <li key={i} className="flex gap-1.5">
                    <span className="text-[#D97706] shrink-0 font-bold">~</span>
                    {c}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Uncertainty notes */}
          {ev.uncertainty_notes.length > 0 && (
            <details>
              <summary className="text-xs text-[#64748B] cursor-pointer hover:text-[#0F172A]">
                {ev.uncertainty_notes.length} uncertainty note(s)
              </summary>
              <ul className="text-xs text-[#94A3B8] mt-1 space-y-0.5 pl-3">
                {ev.uncertainty_notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            </details>
          )}

          {/* Cross-camera links */}
          {ev.cross_camera_links.length > 0 && (
            <div className="border-t border-[#E2E8F0] pt-2">
              <p className="text-[11px] font-semibold text-[#0284C7] uppercase tracking-wide mb-1">Cross-Camera Links</p>
              {ev.cross_camera_links.map((lnk, i) => (
                <div key={i} className="text-xs text-[#64748B] flex items-center gap-2">
                  <span className={`rt-badge ${lnk.link_type === 'confirmed' ? 'rt-badge-green' : 'rt-badge-amber'}`}>
                    {lnk.link_type}
                  </span>
                  <span>{lnk.target_camera_name ?? 'Camera'} · {(lnk.confidence * 100).toFixed(0)}%</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
