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

function confidenceLabel(score: number): { text: string; cls: string } {
  if (score >= 0.75) return { text: 'High', cls: 'bg-green-100 text-green-800' }
  if (score >= 0.5) return { text: 'Medium', cls: 'bg-yellow-100 text-yellow-800' }
  return { text: 'Low', cls: 'bg-red-100 text-red-800' }
}

function describeValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

const CATEGORY_LABELS: Record<string, string> = {
  'possible-assault': 'Possible assault',
  'possible-theft': 'Possible theft',
  confrontation: 'Confrontation',
  'sudden-fall': 'Sudden fall',
  'suspicious-approach-departure': 'Suspicious approach/departure',
  'other-anomaly': 'Other anomaly',
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
  const conf = confidenceLabel(ev.confidence)
  const typeLabel = CATEGORY_LABELS[ev.category] ?? ev.category
  const duration = Math.max(0, ev.end_seconds - ev.start_seconds)
  const reliableTs = ev.timestamp_source === 'container_pts'

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex gap-4 flex-col sm:flex-row">
        {/* Evidence thumbnails */}
        <div className="flex-shrink-0 space-y-2">
          {activeFrame ? (
            <img
              src={`/${activeFrame.frame_path}`}
              alt={`Evidence frame (${activeFrame.role})`}
              className="w-full sm:w-44 h-32 object-cover rounded-lg bg-gray-100"
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          ) : (
            <div className="w-full sm:w-44 h-32 rounded-lg bg-gray-100 flex items-center justify-center text-xs text-gray-400">
              No evidence frames
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
                    className={`text-xs px-2 py-0.5 rounded border transition-colors ${
                      isActive
                        ? 'bg-blue-600 text-white border-blue-600'
                        : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
                    }`}
                  >
                    {role}
                  </button>
                )
              })}
            </div>
          )}
          {activeFrame && (
            <p className="text-[11px] text-gray-400 text-center">
              @{formatTs(activeFrame.timestamp_seconds)}
              {activeFrame.pts_seconds != null && ` (PTS ${activeFrame.pts_seconds.toFixed(2)}s)`}
            </p>
          )}
        </div>

        {/* Event details */}
        <div className="flex-1 min-w-0 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded font-medium">
              {String(index).padStart(2, '0')}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-purple-100 text-purple-800">
              {typeLabel}
            </span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${conf.cls}`}>
              {conf.text} confidence ({(ev.confidence * 100).toFixed(0)}%)
            </span>
            {!reliableTs && (
              <span className="text-xs text-orange-600 bg-orange-50 px-2 py-0.5 rounded">
                Approximate timestamp
              </span>
            )}
            {ev.single_frame_candidate && (
              <span className="text-xs text-amber-700 bg-amber-50 px-2 py-0.5 rounded border border-amber-200">
                Single-frame candidate
              </span>
            )}
          </div>

          <div className="text-sm text-gray-700">
            <span className="font-medium">{cameraName ?? recordingFilename ?? ev.recording_id}</span>
            <span className="text-gray-400 mx-2">·</span>
            <span>
              {formatTs(ev.start_seconds)}–{formatTs(ev.end_seconds)}
              {' '}({formatDuration(duration)})
            </span>
            <span className="text-gray-400 mx-2">·</span>
            <span className="text-gray-500">
              midpoint @{formatTs(ev.timestamp_seconds)}
            </span>
          </div>

          {ev.candidate_types.length > 0 && (
            <div className="flex gap-1 flex-wrap">
              {ev.candidate_types.map(t => (
                <span key={t} className="text-[11px] px-2 py-0.5 rounded bg-gray-100 text-gray-600">
                  {t}
                </span>
              ))}
              {ev.stages.length > 0 && (
                <span className="text-[11px] px-2 py-0.5 text-gray-500">
                  stages: {ev.stages.join(' → ')}
                </span>
              )}
            </div>
          )}

          {/* Event clip playback */}
          {ev.clip?.clip_url && (
            <div>
              {!showClip ? (
                <button
                  onClick={() => setShowClip(true)}
                  className="text-xs px-3 py-1.5 rounded-lg border border-indigo-300 text-indigo-700 hover:bg-indigo-50 transition-colors"
                >
                  ▶ Play event clip ({formatTs(ev.clip.start_seconds)}–{formatTs(ev.clip.end_seconds)})
                </button>
              ) : (
                <video
                  src={ev.clip.clip_url}
                  controls
                  preload="metadata"
                  className="w-full max-h-64 rounded-lg bg-black"
                />
              )}
            </div>
          )}

          {/* Expandable action sequence */}
          {(ev.action_sequence.length > 0 || ev.observable_actions.length > 0 || ev.tracks.length > 0) && (
            <details>
              <summary className="text-xs text-indigo-700 cursor-pointer hover:text-indigo-900 font-medium">
                Action sequence ({ev.action_sequence.length} step{ev.action_sequence.length === 1 ? '' : 's'})
              </summary>
              <ol className="text-xs text-gray-700 mt-1 space-y-0.5 pl-4 list-decimal">
                {ev.action_sequence.map((step, i) => (
                  <li key={i}>{step}</li>
                ))}
              </ol>
              {ev.observable_actions.length > 0 && (
                <div className="mt-2">
                  <h5 className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">
                    Observable actions
                  </h5>
                  <ul className="text-xs text-gray-600 mt-0.5 space-y-0.5 pl-3">
                    {ev.observable_actions.map((a, i) => (
                      <li key={i}>· {describeValue(a)}</li>
                    ))}
                  </ul>
                </div>
              )}
              {ev.tracks.length > 0 && (
                <div className="mt-2">
                  <h5 className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">
                    Tracks ({ev.tracks.length})
                  </h5>
                  <ul className="text-xs text-gray-600 mt-0.5 space-y-0.5 pl-3">
                    {ev.tracks.map((t, i) => (
                      <li key={i}>· {describeValue(t)}</li>
                    ))}
                  </ul>
                </div>
              )}
            </details>
          )}

          {/* Confirmed + observed facts */}
          {ev.confirmed.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-green-700 uppercase tracking-wide mb-1">
                Confirmed
              </h4>
              <ul className="text-xs text-gray-700 space-y-0.5">
                {ev.confirmed.map((f, i) => (
                  <li key={i} className="flex gap-1">
                    <span className="text-green-600">✓</span>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {ev.observed_facts.length > 0 && (
            <details>
              <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-700">
                {ev.observed_facts.length} observed fact(s)
              </summary>
              <ul className="text-xs text-gray-600 mt-1 space-y-0.5 pl-3">
                {ev.observed_facts.map((f, i) => (
                  <li key={i}>· {f}</li>
                ))}
              </ul>
            </details>
          )}

          {/* Uncertainties with supporting evidence */}
          {ev.uncertain.length > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
              <h4 className="text-xs font-semibold text-amber-800 uppercase tracking-wide mb-1">
                Uncertainties ({ev.uncertain.length})
              </h4>
              <ul className="text-xs text-amber-800 space-y-0.5 mb-2">
                {ev.uncertain.map((u, i) => (
                  <li key={i} className="flex gap-1">
                    <span className="text-amber-500">~</span>
                    <span>{u}</span>
                  </li>
                ))}
              </ul>
              <p className="text-[11px] text-amber-700 border-t border-amber-200 pt-1">
                Supporting evidence: {ev.confirming_frame_count} confirming frame(s)
                {' · '}{ev.evidence_frames.length} evidence thumbnail(s)
                {' · '}timestamp source: {ev.timestamp_source}
                {ev.candidate_types.length > 0 && ` · candidates: ${ev.candidate_types.join(', ')}`}
              </p>
            </div>
          )}

          {/* Investigate action */}
          <div className="pt-1">
            <button
              onClick={() => onInvestigate(ev)}
              disabled={investigating}
              className="text-xs px-3 py-1.5 rounded-lg bg-indigo-600 text-white font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
            >
              {investigating ? 'Investigating…' : '🔍 Investigate this event'}
            </button>
            {investigateError && (
              <p className="text-xs text-red-600 mt-1">{investigateError}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
