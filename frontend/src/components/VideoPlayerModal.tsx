'use client'
import { useEffect, useRef, useState } from 'react'
import type { Recording } from '@/types'

interface Props {
  recording: Recording
  initialTimestamp?: number
  onClose: () => void
}

function formatDuration(secs?: number): string {
  if (!secs) return ''
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function VideoPlayerModal({ recording, initialTimestamp, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [videoError, setVideoError] = useState(false)

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
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
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-4xl mx-4 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-medium text-gray-900 truncate">{recording.original_filename}</span>
            {recording.camera_name && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-800 font-medium flex-shrink-0">
                {recording.camera_name}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-red-600 transition-colors flex-shrink-0 ml-4 px-2 py-1 text-sm"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Video player */}
        <div className="bg-black">
          {videoError ? (
            <div className="flex items-center justify-center h-64 text-gray-400 text-sm">
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

        {/* Metadata footer */}
        <div className="px-4 py-3 border-t border-gray-100 text-sm text-gray-600 flex flex-wrap gap-4">
          {recording.camera_location && (
            <span className="text-indigo-600">{recording.camera_location}</span>
          )}
          {recording.recording_datetime && (
            <span>{new Date(recording.recording_datetime).toLocaleString()}{recording.recording_timezone ? ` (${recording.recording_timezone})` : ''}</span>
          )}
          {recording.video_duration_seconds != null && (
            <span>Duration: {formatDuration(recording.video_duration_seconds)}</span>
          )}
          {initialTimestamp != null && (
            <span className="text-indigo-600 font-medium">Seeking to {formatDuration(initialTimestamp)}</span>
          )}
        </div>
      </div>
    </div>
  )
}
