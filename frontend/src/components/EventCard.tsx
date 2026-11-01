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

function confidenceLabel(score: number): { text: string; cls: string } {
  if (score >= 0.75) return { text: 'High', cls: 'bg-green-100 text-green-800' }
  if (score >= 0.5) return { text: 'Medium', cls: 'bg-yellow-100 text-yellow-800' }
  return { text: 'Low', cls: 'bg-red-100 text-red-800' }
}

function relevanceLabel(score: number): { text: string; cls: string } {
  if (score >= 0.75) return { text: 'Strong match', cls: 'bg-green-100 text-green-800' }
  if (score >= 0.5) return { text: 'Partial match', cls: 'bg-yellow-100 text-yellow-800' }
  return { text: 'Weak match', cls: 'bg-red-100 text-red-800' }
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  person_detected: 'Person',
  vehicle_detected: 'Vehicle',
  bird_detected: 'Bird',
  unattended_object: 'Object',
  entry_exit_detected: 'Entry/Exit',
  scene_change: 'Scene Change',
  'possible-theft': 'Possible theft',
  'possible-assault': 'Possible assault',
  confrontation: 'Confrontation',
  'sudden-fall': 'Sudden fall',
  'suspicious-approach-departure': 'Suspicious approach',
  'other-anomaly': 'Anomaly',
}

export default function EventCard({ event: ev, index, highlight, onPlayEvidence }: Props) {
  const [activeFrame, setActiveFrame] = useState<EvidenceFrame | null>(
    ev.evidence_frames.find(f => f.role === 'during') ?? ev.evidence_frames[0] ?? null
  )
  const conf = confidenceLabel(ev.confidence)
  const typeLabel = EVENT_TYPE_LABELS[ev.event_type] ?? ev.event_type
  const hasRelevance = ev.relevance_score !== undefined && ev.relevance_score !== null
  const rel = hasRelevance ? relevanceLabel(ev.relevance_score as number) : null

  const tsLabel = ev.timestamp_absolute
    ? new Date(ev.timestamp_absolute).toLocaleString()
    : `Video @${formatTs(ev.timestamp_seconds)}`
  const cameraLabel = ev.camera_name || `Camera ${ev.recording_id.slice(0, 8)}`
  const hasSpan = ev.start_seconds !== undefined && ev.start_seconds !== null &&
    ev.end_seconds !== undefined && ev.end_seconds !== null &&
    ev.end_seconds > ev.start_seconds

  const facts = ev.observed_facts
  const visibleFacts = facts.slice(0, MAX_VISIBLE_FACTS)
  const hiddenFacts = facts.slice(MAX_VISIBLE_FACTS)

  return (
    <div className={`rounded-xl border p-4 ${highlight ? 'border-blue-400 bg-blue-50' : 'border-gray-200 bg-white'}`}>
      <div className="flex gap-4">
        {/* Evidence frame panel */}
        <div className="flex-shrink-0 space-y-2">
          {activeFrame && (
            <div>
              <img
                src={`/${activeFrame.frame_path}`}
                alt={`Evidence frame (${activeFrame.role})`}
                title={`${activeFrame.frame_path} @ ${activeFrame.timestamp_seconds.toFixed(1)}s`}
                className="w-44 h-32 object-cover rounded-lg bg-gray-100"
                onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
              />
              <div className="text-[11px] text-gray-500 text-center mt-1">
                t={activeFrame.timestamp_seconds.toFixed(1)}s · {activeFrame.role}
              </div>
            </div>
          )}
          {onPlayEvidence && (
            <button
              onClick={() => onPlayEvidence(ev.recording_id, ev.timestamp_seconds)}
              className="text-xs px-2 py-1 rounded border border-indigo-300 text-indigo-700 hover:bg-indigo-50 transition-colors w-full"
            >
              ▶ Preview
            </button>
          )}
          {/* Before / During / After navigation */}
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
        </div>

        {/* Event details */}
        <div className="flex-1 min-w-0 space-y-2">
          {/* Header row */}
          <div className="flex items-start justify-between gap-2 flex-wrap">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded font-medium">
                {String(index).padStart(2, '0')}
              </span>
              <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-purple-100 text-purple-800">
                {typeLabel}
              </span>
              {ev.is_primary_finding && (
                <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-blue-600 text-white">
                  ★ Primary finding
                </span>
              )}
              {rel && hasRelevance ? (
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${rel.cls}`}>
                  {rel.text} ({((ev.relevance_score as number) * 100).toFixed(0)}%)
                </span>
              ) : (
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${conf.cls}`}>
                  {conf.text} confidence ({(ev.confidence * 100).toFixed(0)}%)
                </span>
              )}
              {!ev.has_reliable_timestamp && (
                <span className="text-xs text-orange-600 bg-orange-50 px-2 py-0.5 rounded">
                  Relative timestamp
                </span>
              )}
            </div>
          </div>

          {/* Camera and time */}
          <div className="text-sm text-gray-700">
            <span className="font-medium" title={ev.recording_filename || ev.recording_id}>
              {cameraLabel}
            </span>
            {ev.camera_location && (
              <span className="text-gray-500 ml-1">· {ev.camera_location}</span>
            )}
            {ev.recording_filename && (
              <span className="text-gray-400 ml-1 text-xs">· {ev.recording_filename}</span>
            )}
            <span className="text-gray-400 mx-2">·</span>
            <span>{tsLabel}</span>
            {hasRelevance && (
              <span className="text-gray-400 ml-2 text-xs">
                detection {(ev.confidence * 100).toFixed(0)}%
              </span>
            )}
          </div>
          {hasSpan && (
            <div className="text-xs text-gray-500">
              Span {formatTs(ev.start_seconds as number)}–{formatTs(ev.end_seconds as number)}
              {' '}({((ev.end_seconds as number) - (ev.start_seconds as number)).toFixed(1)}s)
            </div>
          )}

          {/* Primary match callout */}
          {ev.primary_match && (
            <div className="text-xs text-blue-900 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2">
              <span className="font-semibold">Primary finding: </span>
              {ev.primary_match}
            </div>
          )}

          {/* Action description */}
          <p className="text-sm font-medium text-gray-900">{ev.action_description}</p>

          {/* Action sequence */}
          {ev.action_sequence && ev.action_sequence.length > 0 && (
            <details>
              <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-700">
                Action sequence ({ev.action_sequence.length} step{ev.action_sequence.length === 1 ? '' : 's'})
              </summary>
              <ol className="mt-1 space-y-1">
                {ev.action_sequence.map(step => (
                  <li key={step.step} className="text-xs text-gray-700 flex gap-2 items-baseline">
                    <span className="text-gray-400 font-mono shrink-0 w-6">{step.step}.</span>
                    <span className="flex-1">{step.description}</span>
                    {step.start_seconds !== undefined && step.start_seconds !== null &&
                      step.end_seconds !== undefined && step.end_seconds !== null && (
                      <span className="text-gray-400 font-mono shrink-0">
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
                        className="text-blue-600 hover:text-blue-800 shrink-0"
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
              <h4 className="text-xs font-semibold text-green-700 uppercase tracking-wide mb-1">
                Observed facts
              </h4>
              <ul className="text-xs text-gray-700 space-y-0.5">
                {visibleFacts.map((f, i) => (
                  <li key={i} className="flex gap-1">
                    <span className="text-green-600">✓</span>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
              {hiddenFacts.length > 0 && (
                <details className="mt-1">
                  <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-700">
                    Show all {facts.length} observations ({hiddenFacts.length} more)
                  </summary>
                  <ul className="text-xs text-gray-700 space-y-0.5 mt-1">
                    {hiddenFacts.map((f, i) => (
                      <li key={i} className="flex gap-1">
                        <span className="text-green-600">✓</span>
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}

          {/* Tracked entities */}
          {ev.entities && ev.entities.length > 0 && (
            <details>
              <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-700">
                Tracked entities ({ev.entities.length})
              </summary>
              <ul className="text-xs text-gray-700 mt-1 space-y-0.5">
                {ev.entities.map(ent => (
                  <li key={ent.entity_id} className="flex gap-2 items-baseline">
                    <span className="font-mono text-gray-500 shrink-0">{ent.entity_id}</span>
                    <span className="flex-1">{ent.display_label || ent.head || ent.entity_id}</span>
                    {ent.first_seen_seconds !== undefined && ent.last_seen_seconds !== undefined && (
                      <span className="text-gray-400 font-mono shrink-0">
                        {formatTs(ent.first_seen_seconds)}–{formatTs(ent.last_seen_seconds)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {/* Inferred claims */}
          {ev.inferred_claims.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-amber-700 uppercase tracking-wide mb-1">
                Inferences (unconfirmed)
              </h4>
              <ul className="text-xs text-amber-800 space-y-0.5">
                {ev.inferred_claims.map((c, i) => (
                  <li key={i} className="flex gap-1">
                    <span className="text-amber-500">~</span>
                    <span>{c}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Uncertainty notes */}
          {ev.uncertainty_notes.length > 0 && (
            <details>
              <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-700">
                {ev.uncertainty_notes.length} uncertainty note(s)
              </summary>
              <ul className="text-xs text-gray-500 mt-1 space-y-0.5 pl-3">
                {ev.uncertainty_notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </details>
          )}

          {/* Cross-camera links */}
          {ev.cross_camera_links.length > 0 && (
            <div className="border-t border-gray-100 pt-2">
              <h4 className="text-xs font-semibold text-blue-700 uppercase tracking-wide mb-1">
                Cross-camera links
              </h4>
              {ev.cross_camera_links.map((lnk, i) => (
                <div key={i} className="text-xs text-gray-600 flex items-center gap-1.5">
                  <span className={`px-1.5 py-0.5 rounded text-white text-[10px] font-medium ${
                    lnk.link_type === 'confirmed' ? 'bg-green-600' : 'bg-amber-500'
                  }`}>
                    {lnk.link_type}
                  </span>
                  <span>
                    {lnk.target_camera_name ?? 'Camera'} · {(lnk.confidence * 100).toFixed(0)}% confidence
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
