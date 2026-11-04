'use client'
import { useEffect, useRef, useState } from 'react'
import type { Recording } from '@/types'

interface Props {
  recording: Recording
  initialTimestamp?: number
  onClose: () => void
  onDelete?: (id: string, filename: string) => void
}

function formatDuration(secs?: number): string {
  if (!secs) return ''
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function VideoPlayerModal({ recording, initialTimestamp, onClose, onDelete }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [videoError, setVideoError] = useState(false)

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  const handleLoadedMetadata = () => {
    if (initialTimestamp != null && videoRef.current) {
      videoRef.current.currentTime = initialTimestamp
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl mx-4 overflow-hidden border border-[#E2E8F0]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-[#E2E8F0]">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className="font-semibold text-[#0F172A] truncate text-sm">{recording.original_filename}</span>
            {recording.camera_name && (
              <span className="rt-badge-blue flex-shrink-0">{recording.camera_name}</span>
            )}
          </div>
          <div className="flex items-center gap-2 ml-4 flex-shrink-0">
            {onDelete && (
              <button
                onClick={() => onDelete(recording.recording_id, recording.original_filename)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-red-600 hover:bg-red-50 transition-colors"
                aria-label="Delete recording"
              >
                <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5">
                  <path d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                    stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                Delete
              </button>
            )}
            <button
              onClick={onClose}
              className="rt-btn-ghost text-[#94A3B8] hover:text-[#DC2626] p-1.5 rounded-lg"
              aria-label="Close"
            >
              <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
                <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
            </button>
          </div>
        </div>

        {/* Video */}
        <div className="bg-black">
          {videoError ? (
            <div className="flex items-center justify-center h-64 text-[#94A3B8] text-sm">
              Video unavailable
            </div>
          ) : (
            <video
              ref={videoRef}
              src={`/api/recordings/${recording.recording_id}/video`}
              controls
              className="w-full max-h-[70vh]"
              crossOrigin="anonymous"
              onLoadedMetadata={handleLoadedMetadata}
              onError={() => setVideoError(true)}
            />
          )}
        </div>

        {/* Footer metadata */}
        <div className="px-5 py-3 border-t border-[#E2E8F0] flex flex-wrap gap-4 text-xs text-[#64748B]">
          {recording.camera_location && (
            <span className="text-[#2563EB] font-medium">{recording.camera_location}</span>
          )}
          {recording.recording_datetime && (
            <span>{new Date(recording.recording_datetime).toLocaleString()}{recording.recording_timezone ? ` (${recording.recording_timezone})` : ''}</span>
          )}
          {recording.video_duration_seconds != null && (
            <span>Duration: {formatDuration(recording.video_duration_seconds)}</span>
          )}
          {initialTimestamp != null && (
            <span className="text-[#2563EB] font-medium">Seeking to {formatDuration(initialTimestamp)}</span>
          )}
        </div>
      </div>
    </div>
  )
}
