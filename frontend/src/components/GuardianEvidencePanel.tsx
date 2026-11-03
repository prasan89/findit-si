'use client'
import { useState } from 'react'
import type { Recording } from '@/types'
import type { FeedItem } from '@/lib/guardianRouting'
import { categoryLabel } from '@/lib/guardianRouting'
import VideoPlayerModal from './VideoPlayerModal'

interface Props {
  item: FeedItem
  recording?: Recording
  onClose: () => void
}

function fmtTs(secs: number): string {
  if (!isFinite(secs)) return '0:00'
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function fmtDuration(start: number, end: number): string {
  const d = Math.round(end - start)
  if (d <= 0) return '—'
  return `${fmtTs(start)}–${fmtTs(end)} (${d}s)`
}

export default function GuardianEvidencePanel({ item, recording, onClose }: Props) {
  const [videoOpen, setVideoOpen] = useState(false)

  const firstFrame = item.evidence_frames[0]
  const thumbSrc = firstFrame
    ? `/api/frames/${encodeURIComponent(firstFrame.frame_path)}`
    : recording?.thumbnail_path
      ? `/api/recordings/${recording.recording_id}/thumbnail`
      : null

  const label = categoryLabel(item.category)
  const camName = item.camera_name ?? recording?.camera_name ?? '—'
  const camLoc = item.camera_location ?? recording?.camera_location

  const severityColors: Record<string, string> = {
    Critical: 'bg-red-100 text-red-700',
    Review: 'bg-amber-100 text-amber-700',
    Routine: 'bg-slate-100 text-slate-600',
  }

  return (
    <div className="flex flex-col h-full">
      {/* Panel header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#E2E8F0]">
        <div className="flex items-center gap-2">
          <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4 text-[#64748B]">
            <rect x="2" y="3" width="20" height="14" rx="2" stroke="currentColor" strokeWidth="1.5"/>
            <path d="M8 21h8M12 17v4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          <span className="text-sm font-semibold text-[#0F172A]">Evidence</span>
        </div>
        <button
          onClick={onClose}
          className="w-6 h-6 rounded flex items-center justify-center text-[#94A3B8] hover:text-[#0F172A] hover:bg-[#F1F5F9] transition-colors"
          aria-label="Close evidence panel"
        >
          ✕
        </button>
      </div>

      {/* Thumbnail / video */}
      <div className="relative bg-[#0F172A] aspect-video mx-4 mt-4 rounded-lg overflow-hidden flex items-center justify-center">
        {thumbSrc ? (
          <img src={thumbSrc} alt="Evidence frame" className="w-full h-full object-cover" />
        ) : (
          <div className="flex flex-col items-center gap-2 text-[#475569]">
            <svg viewBox="0 0 24 24" fill="none" className="w-10 h-10">
              <rect x="2" y="3" width="20" height="14" rx="2" stroke="currentColor" strokeWidth="1.5"/>
              <path d="M9 8l6 4-6 4V8z" fill="currentColor"/>
            </svg>
            <span className="text-xs">No preview</span>
          </div>
        )}
        {recording && (
          <button
            onClick={() => setVideoOpen(true)}
            className="absolute inset-0 flex items-center justify-center bg-black/30 opacity-0 hover:opacity-100 transition-opacity"
            aria-label="Play video"
          >
            <div className="w-12 h-12 rounded-full bg-white/90 flex items-center justify-center shadow-md">
              <svg viewBox="0 0 24 24" fill="none" className="w-6 h-6 ml-0.5">
                <path d="M6 4l14 8-14 8V4z" fill="#0F172A"/>
              </svg>
            </div>
          </button>
        )}
      </div>

      {/* Metadata */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        <div>
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${severityColors[item.severityTag] ?? 'bg-slate-100 text-slate-600'}`}>
            {item.severityTag}
          </span>
        </div>

        {[
          { label: 'Event time', value: fmtTs(item.timestamp_seconds) },
          {
            label: 'Duration',
            value: fmtDuration(item.time_range_start, item.time_range_end),
          },
          { label: 'Camera', value: camLoc ? `${camName} (${camLoc})` : camName },
          { label: 'Event type', value: label },
          { label: 'Confidence', value: `${Math.round(item.confidence * 100)}%` },
        ].map(({ label: l, value: v }) => (
          <div key={l} className="flex justify-between items-start gap-2 text-xs">
            <span className="text-[#64748B] shrink-0">{l}</span>
            <span className="text-[#0F172A] font-medium text-right">{v}</span>
          </div>
        ))}

        {item.description && (
          <div className="pt-2 border-t border-[#F1F5F9]">
            <p className="text-xs text-[#475569] leading-relaxed">{item.description}</p>
          </div>
        )}
      </div>

      {/* Actions */}
      {recording && (
        <div className="px-4 pb-4 pt-2 border-t border-[#E2E8F0] space-y-2">
          <button
            onClick={() => setVideoOpen(true)}
            className="rt-btn-primary w-full justify-center text-sm py-2.5"
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
              <path d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            Open Recording
          </button>
        </div>
      )}

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
