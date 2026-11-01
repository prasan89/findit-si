'use client'
import { useState } from 'react'
import type { TemporalEvent, TemporalEvidenceFrame } from '@/types'

interface Props {
  event: TemporalEvent
  index: number
  cameraName?: string
  recordingFilename?: string
  investigating: boolean
  investigateError: string | null
  onInvestigate: (event: TemporalEvent) => void
}

function formatTs(secs: number): string {
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = Math.floor(secs % 60)
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`
}

function formatDuration(secs: number): string {
  return secs < 60 ? `${secs.toFixed(1)}s` : formatTs(secs)
}

function describeValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  try { return JSON.stringify(value) } catch { return String(value) }
}

const CATEGORY_LABELS: Record<string, string> = {
  'possible-assault':              'Possible assault',
  'possible-theft':                'Possible theft',
  confrontation:                   'Confrontation',
  'sudden-fall':                   'Sudden fall',
  'suspicious-approach-departure': 'Suspicious activity',
  'other-anomaly':                 'Anomaly',
}

function categoryBadge(cat: string): string {
  if (cat.includes('assault') || cat.includes('fall')) return 'rt-badge-red'
  if (cat.includes('theft') || cat.includes('confrontation') || cat.includes('suspicious')) return 'rt-badge-amber'
  return 'rt-badge-gray'
}

function confidenceLabel(score: number, pct: number): string {
  if (score >= 0.75) return `High confidence (${pct}%)`
  if (score >= 0.5) return `Medium confidence (${pct}%)`
  return `Low confidence (${pct}%)`
}

function confidenceBadge(score: number): string {
  if (score >= 0.75) return 'rt-badge-green'
  if (score >= 0.5) return 'rt-badge-amber'
  return 'rt-badge-red'
}

const ROLES = ['before', 'during', 'after'] as const

export default function TemporalEventCard({
  event: ev,
  index,
  cameraName,
  recordingFilename,
  investigating,
  investigateError,
  onInvestigate,
}: Props) {
  const [activeFrame, setActiveFrame] = useState<TemporalEvidenceFrame | null>(
    ev.evidence_frames.find(f => f.role === 'during') ?? ev.evidence_frames[0] ?? null
  )
  const [showClip, setShowClip] = useState(false)
  const typeLabel = CATEGORY_LABELS[ev.category] ?? ev.category
  const duration = Math.max(0, ev.end_seconds - ev.start_seconds)
  const reliableTs = ev.timestamp_source === 'container_pts'

  return (
    <div className="rt-card p-4">
      <div className="flex gap-4 flex-col sm:flex-row">
        {/* Evidence thumbnail */}
        <div className="flex-shrink-0 space-y-2">
          {activeFrame ? (
            <img
              src={`/${activeFrame.frame_path}`}
              alt={`Evidence frame (${activeFrame.role})`}
              className="w-full sm:w-44 h-32 object-cover rounded-lg bg-[#F1F5F9]"
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          ) : (
            <div className="w-full sm:w-44 h-32 rounded-lg bg-[#F1F5F9] flex items-center justify-center text-xs text-[#94A3B8]">
              No frames
            </div>
          )}
          {ev.evidence_frames.length > 1 && (
            <div className="flex gap-1 justify-center">
              {ROLES.map(role => {
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
          {activeFrame && (
            <p className="text-[11px] text-[#94A3B8] text-center">
              @{formatTs(activeFrame.timestamp_seconds)}
            </p>
          )}
        </div>

        {/* Details */}
        <div className="flex-1 min-w-0 space-y-2">
          {/* Header row */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="rt-badge-gray font-mono">{String(index).padStart(2, '0')}</span>
            <span className={categoryBadge(ev.category)}>{typeLabel}</span>
            <span className={confidenceBadge(ev.confidence)}>
              {confidenceLabel(ev.confidence, Math.round(ev.confidence * 100))}
            </span>
            {!reliableTs && <span className="rt-badge-amber">Approximate timestamp</span>}
            {ev.single_frame_candidate && <span className="rt-badge-amber">Single-frame candidate</span>}
          </div>

          {/* Time + camera */}
          <div className="text-sm text-[#64748B]">
            <span className="font-medium text-[#0F172A]">{cameraName ?? recordingFilename ?? ev.recording_id}</span>
            <span className="mx-2 text-[#E2E8F0]">·</span>
            <span className="font-mono">{formatTs(ev.start_seconds)}–{formatTs(ev.end_seconds)}</span>
            <span className="text-[#94A3B8] ml-1">({formatDuration(duration)})</span>
          </div>

          {/* Candidate types */}
          {ev.candidate_types.length > 0 && (
            <div className="flex gap-1 flex-wrap">
              {ev.candidate_types.map(t => (
                <span key={t} className="text-[11px] px-2 py-0.5 rounded bg-[#F1F5F9] text-[#64748B] border border-[#E2E8F0]">
                  {t}
                </span>
              ))}
              {ev.stages.length > 0 && (
                <span className="text-[11px] text-[#94A3B8]">
                  stages: {ev.stages.join(' → ')}
                </span>
              )}
            </div>
          )}

          {/* Clip playback */}
          {ev.clip?.clip_url && (
            <div>
              {!showClip ? (
                <button
                  onClick={() => setShowClip(true)}
                  className="rt-btn-secondary text-xs py-1.5 px-3"
                >
                  <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5">
                    <path d="M8 5v14l11-7L8 5z" fill="currentColor"/>
                  </svg>
                  Play event clip ({formatTs(ev.clip.start_seconds)}–{formatTs(ev.clip.end_seconds)})
                </button>
              ) : (
                <video src={ev.clip.clip_url} controls preload="metadata" className="w-full max-h-64 rounded-lg bg-black" />
              )}
            </div>
          )}

          {/* Action sequence */}
          {(ev.action_sequence.length > 0 || ev.observable_actions.length > 0 || ev.tracks.length > 0) && (
            <details>
              <summary className="text-xs text-[#2563EB] cursor-pointer hover:text-[#1D4ED8] font-medium">
                Action sequence ({ev.action_sequence.length} step{ev.action_sequence.length === 1 ? '' : 's'})
              </summary>
              <ol className="text-xs text-[#64748B] mt-1.5 space-y-1 pl-4 list-decimal">
                {ev.action_sequence.map((step, i) => <li key={i}>{step}</li>)}
              </ol>
              {ev.observable_actions.length > 0 && (
                <div className="mt-2">
                  <p className="text-[11px] font-semibold text-[#94A3B8] uppercase tracking-wide mb-1">Observable actions</p>
                  <ul className="text-xs text-[#64748B] space-y-0.5">
                    {ev.observable_actions.map((a, i) => <li key={i}>· {describeValue(a)}</li>)}
                  </ul>
                </div>
              )}
            </details>
          )}

          {/* Confirmed */}
          {ev.confirmed.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold text-[#16A34A] uppercase tracking-wide mb-1">Confirmed</p>
              <ul className="text-xs text-[#64748B] space-y-0.5">
                {ev.confirmed.map((f, i) => (
                  <li key={i} className="flex gap-1.5">
                    <svg viewBox="0 0 20 20" fill="none" className="w-3.5 h-3.5 text-[#16A34A] mt-0.5 shrink-0">
                      <path d="M16 5l-8 8-4-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Observed facts (collapsible) */}
          {ev.observed_facts.length > 0 && (
            <details>
              <summary className="text-xs text-[#64748B] cursor-pointer hover:text-[#0F172A]">
                {ev.observed_facts.length} observed fact(s)
              </summary>
              <ul className="text-xs text-[#64748B] mt-1 space-y-0.5 pl-3">
                {ev.observed_facts.map((f, i) => <li key={i}>· {f}</li>)}
              </ul>
            </details>
          )}

          {/* Uncertainties */}
          {ev.uncertain.length > 0 && (
            <div className="rt-banner-warning">
              <p className="text-[11px] font-semibold uppercase tracking-wide mb-1 text-[#D97706]">
                Uncertainties ({ev.uncertain.length})
              </p>
              <ul className="text-xs space-y-0.5">
                {ev.uncertain.map((u, i) => <li key={i}>~ {u}</li>)}
              </ul>
              <p className="text-[11px] mt-2 text-amber-600 border-t border-amber-200 pt-1.5">
                {ev.confirming_frame_count} confirming frame{ev.confirming_frame_count === 1 ? '' : 's'} · {ev.evidence_frames.length} thumbnail{ev.evidence_frames.length === 1 ? '' : 's'} · timestamp source: {ev.timestamp_source}
              </p>
            </div>
          )}

          {/* Investigate CTA */}
          <div className="pt-1">
            <button
              onClick={() => onInvestigate(ev)}
              disabled={investigating}
              className="rt-btn-primary text-xs py-1.5 px-3"
            >
              <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5">
                <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
                <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
              </svg>
              {investigating ? 'Investigating…' : 'Investigate this event'}
            </button>
            {investigateError && <p className="text-xs text-[#DC2626] mt-1">{investigateError}</p>}
          </div>
        </div>
      </div>
    </div>
  )
}
