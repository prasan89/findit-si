'use client'
import { useState } from 'react'
import type { Recording, RecordingNarrative } from '@/types'

interface Props {
  recordings: Recording[]
}

function formatTs(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function NarrativePanel({ recordings }: Props) {
  const indexed = recordings.filter(
    r => r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL'
  )
  const [selectedId, setSelectedId] = useState<string>(
    indexed[0]?.recording_id ?? ''
  )

  const selected = recordings.find(r => r.recording_id === selectedId)
  const narrative: RecordingNarrative | undefined = selected?.recording_narrative

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-[#0F172A]">Recording Narrative</h2>
          <p className="text-xs text-[#64748B] mt-0.5">
            Full temporal story generated from the entire recording
          </p>
        </div>
        {indexed.length > 0 && (
          <select
            value={selectedId}
            onChange={e => setSelectedId(e.target.value)}
            className="text-sm border border-[#E2E8F0] rounded-lg px-3 py-1.5 bg-white text-[#0F172A] focus:outline-none focus:ring-2 focus:ring-[#93C5FD]"
          >
            {indexed.map(r => (
              <option key={r.recording_id} value={r.recording_id}>
                {r.camera_name || r.original_filename}
              </option>
            ))}
          </select>
        )}
      </div>

      {indexed.length === 0 && (
        <div className="rt-card p-8 text-center text-[#64748B]">
          <p className="text-sm">No indexed recordings yet. Upload a video to get started.</p>
        </div>
      )}

      {indexed.length > 0 && !narrative && (
        <div className="rt-card p-8 text-center text-[#64748B] space-y-2">
          <div className="mx-auto w-10 h-10 rounded-full bg-[#F1F5F9] flex items-center justify-center">
            <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5 text-[#94A3B8]">
              <path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <p className="text-sm font-medium">Narrative analysis not yet available</p>
          <p className="text-xs text-[#94A3B8]">
            Re-upload or reprocess this recording to generate a whole-recording narrative.
          </p>
        </div>
      )}

      {narrative && (
        <div className="space-y-4">
          {/* Scene context */}
          <div className="rt-card p-4">
            <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest mb-1">Scene</p>
            <p className="text-sm text-[#0F172A]">{narrative.scene}</p>
          </div>

          {/* Narrative steps */}
          {narrative.narrative_steps?.length > 0 && (
            <div className="rt-card p-4 space-y-3">
              <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest">What Happened</p>
              <ol className="space-y-4">
                {narrative.narrative_steps.map(step => (
                  <li key={step.step} className="flex gap-3">
                    <div className="flex-shrink-0 flex flex-col items-center">
                      <div className="w-6 h-6 rounded-full bg-[#EFF6FF] border border-[#BFDBFE] flex items-center justify-center">
                        <span className="text-[10px] font-bold text-[#2563EB]">{step.step}</span>
                      </div>
                      {step.step < narrative.narrative_steps.length && (
                        <div className="w-px flex-1 bg-[#E2E8F0] mt-1" />
                      )}
                    </div>
                    <div className="pb-3 flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs font-medium text-[#2563EB] bg-[#EFF6FF] px-2 py-0.5 rounded-full">
                          {step.time_range || `${formatTs(step.start_seconds)}–${formatTs(step.end_seconds)}`}
                        </span>
                      </div>
                      <p className="text-sm text-[#334155]">{step.description}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* Key observations */}
          {narrative.key_observations?.length > 0 && (
            <div className="rt-card p-4 space-y-2">
              <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest">Key Observations</p>
              <ul className="space-y-1.5">
                {narrative.key_observations.map((obs, i) => (
                  <li key={i} className="flex gap-2 text-sm text-[#334155]">
                    <span className="text-[#2563EB] mt-0.5 flex-shrink-0">•</span>
                    <span>{obs}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Open questions */}
          {narrative.open_questions?.length > 0 && (
            <div className="rt-card p-4 space-y-2 border-l-4 border-[#FCD34D]">
              <p className="text-xs font-semibold text-[#92400E] uppercase tracking-widest">Open Questions</p>
              <ul className="space-y-1.5">
                {narrative.open_questions.map((q, i) => (
                  <li key={i} className="text-sm text-[#78350F]">{q}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
