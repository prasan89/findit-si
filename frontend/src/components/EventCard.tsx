'use client'
import { useState } from 'react'
import type { Event, EvidenceFrame } from '@/types'

interface Props {
  event: Event
  index: number
  highlight?: boolean
  onPlayEvidence?: (recordingId: string, timestamp: number) => void
}

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

const EVENT_TYPE_LABELS: Record<string, string> = {
  person_detected: 'Person',
  vehicle_detected: 'Vehicle',
  unattended_object: 'Object',
  entry_exit_detected: 'Entry/Exit',
  scene_change: 'Scene Change',
}

export default function EventCard({ event: ev, index, highlight, onPlayEvidence }: Props) {
  const [activeFrame, setActiveFrame] = useState<EvidenceFrame | null>(
    ev.evidence_frames.find(f => f.role === 'during') ?? ev.evidence_frames[0] ?? null
  )
  const conf = confidenceLabel(ev.confidence)
  const typeLabel = EVENT_TYPE_LABELS[ev.event_type] ?? ev.event_type

  const tsLabel = ev.timestamp_absolute
    ? new Date(ev.timestamp_absolute).toLocaleString()
    : `Video @${formatTs(ev.timestamp_seconds)}`

  return (
    <div className={`rounded-xl border p-4 ${highlight ? 'border-blue-400 bg-blue-50' : 'border-gray-200 bg-white'}`}>
      <div className="flex gap-4">
        {/* Evidence frame panel */}
        <div className="flex-shrink-0 space-y-2">
          {activeFrame && (
            <img
              src={`/${activeFrame.frame_path}`}
              alt={`Evidence frame (${activeFrame.role})`}
              className="w-44 h-32 object-cover rounded-lg bg-gray-100"
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
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
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${conf.cls}`}>
                {conf.text} confidence ({(ev.confidence * 100).toFixed(0)}%)
              </span>
              {!ev.has_reliable_timestamp && (
                <span className="text-xs text-orange-600 bg-orange-50 px-2 py-0.5 rounded">
                  Relative timestamp
                </span>
              )}
            </div>
          </div>

          {/* Camera and time */}
          <div className="text-sm text-gray-700">
            <span className="font-medium">
              {ev.camera_name ?? ev.recording_filename}
            </span>
            {ev.camera_location && (
              <span className="text-gray-500 ml-1">· {ev.camera_location}</span>
            )}
            <span className="text-gray-400 mx-2">·</span>
            <span>{tsLabel}</span>
          </div>

          {/* Action description */}
          <p className="text-sm font-medium text-gray-900">{ev.action_description}</p>

          {/* Observed facts */}
          {ev.observed_facts.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-green-700 uppercase tracking-wide mb-1">
                Observed facts
              </h4>
              <ul className="text-xs text-gray-700 space-y-0.5">
                {ev.observed_facts.map((f, i) => (
                  <li key={i} className="flex gap-1">
                    <span className="text-green-600">✓</span>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </div>
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
