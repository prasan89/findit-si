'use client'
import { useState, useRef } from 'react'
import type { Recording } from '@/types'
import VideoPlayerModal from './VideoPlayerModal'

const STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
  UPLOADED:   { label: 'Uploaded',   cls: 'rt-badge-amber' },
  PENDING:    { label: 'Pending',    cls: 'rt-badge-amber' },
  PROCESSING: { label: 'Processing', cls: 'rt-badge-blue'  },
  INDEXED:    { label: 'Indexed',    cls: 'rt-badge-green' },
  PARTIAL:    { label: 'Partial',    cls: 'rt-badge-amber' },
  FAILED:     { label: 'Failed',     cls: 'rt-badge-red'   },
}

interface Props {
  recordings: Recording[]
  onUploadComplete: () => void
  onDelete: () => void
}

export default function VideoLibrary({ recordings, onUploadComplete, onDelete }: Props) {
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [recordingDatetime, setRecordingDatetime] = useState('')
  const [recordingTimezone, setRecordingTimezone] = useState('')
  const [cameraName, setCameraName] = useState('')
  const [cameraLocation, setCameraLocation] = useState('')
  const [activeVideo, setActiveVideo] = useState<Recording | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault()
    const files = fileRef.current?.files
    if (!files || files.length === 0) return
    setUploading(true)
    setUploadError(null)

    for (const file of Array.from(files)) {
      const form = new FormData()
      form.append('file', file)
      if (recordingDatetime) form.append('recording_datetime', recordingDatetime)
      if (recordingTimezone) form.append('recording_timezone', recordingTimezone)
      if (cameraName) form.append('camera_name', cameraName)
      if (cameraLocation) form.append('camera_location', cameraLocation)

      try {
        const res = await fetch('/api/recordings/upload', { method: 'POST', body: form })
        if (!res.ok) {
          const err = await res.json()
          setUploadError(`Upload failed for ${file.name}: ${err.detail || res.statusText}`)
        }
      } catch {
        setUploadError(`Network error uploading ${file.name}`)
      }
    }

    setUploading(false)
    if (fileRef.current) fileRef.current.value = ''
    setUploadOpen(false)
    onUploadComplete()
  }

  const handleDelete = async (id: string, filename: string) => {
    if (!confirm(`Delete "${filename}" and all its indexed data?`)) return
    await fetch(`/api/recordings/${id}`, { method: 'DELETE' })
    onDelete()
  }

  const formatDuration = (secs?: number) => {
    if (!secs) return '—'
    const m = Math.floor(secs / 60), s = Math.floor(secs % 60)
    return `${m}:${String(s).padStart(2, '0')}`
  }

  const indexedCount = recordings.filter(r => r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL').length

  return (
    <div className="space-y-5">
      {/* Header row with toggle button */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="rt-section-title">Camera Library</h2>
          <p className="rt-section-subtitle">{recordings.length} recording{recordings.length !== 1 ? 's' : ''}{indexedCount > 0 ? ` · ${indexedCount} indexed` : ''}</p>
        </div>
        <button
          onClick={() => setUploadOpen(o => !o)}
          className="rt-btn-primary"
        >
          <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
            <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M12 12V4m0 0L8 8m4-4l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          Upload Recording
        </button>
      </div>

      {/* Upload form — collapsed by default */}
      {uploadOpen && (
        <div className="rt-card p-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="rt-section-title">Upload CCTV Recording</h3>
              <p className="rt-section-subtitle">Supports .mp4, .mov, .avi, .mkv, .webm</p>
            </div>
            <button onClick={() => setUploadOpen(false)} className="rt-btn-ghost p-1.5" title="Close">
              <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4"><path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></svg>
            </button>
          </div>
          <form onSubmit={handleUpload} className="space-y-4">
          {/* File picker */}
          <div>
            <label className="rt-label">Video files</label>
            <div className="relative">
              <input
                ref={fileRef}
                type="file"
                multiple
                accept=".mp4,.mov,.avi,.mkv,.webm"
                className="block w-full text-sm text-[#64748B]
                  file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0
                  file:text-sm file:font-medium file:bg-[#EFF6FF] file:text-[#2563EB]
                  hover:file:bg-[#DBEAFE] file:cursor-pointer file:transition-colors"
                required
              />
            </div>
          </div>

          {/* Metadata grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="rt-label">
                Camera name <span className="text-[#94A3B8] font-normal">(optional)</span>
              </label>
              <input type="text" value={cameraName} onChange={e => setCameraName(e.target.value)}
                placeholder="e.g. Entrance Camera" className="rt-input" />
            </div>
            <div>
              <label className="rt-label">
                Camera location <span className="text-[#94A3B8] font-normal">(optional)</span>
              </label>
              <input type="text" value={cameraLocation} onChange={e => setCameraLocation(e.target.value)}
                placeholder="e.g. Front Door" className="rt-input" />
            </div>
            <div>
              <label className="rt-label">
                Recording date/time <span className="text-[#94A3B8] font-normal">(optional)</span>
              </label>
              <input type="datetime-local" value={recordingDatetime} onChange={e => setRecordingDatetime(e.target.value)}
                className="rt-input" />
            </div>
            <div>
              <label className="rt-label">
                Timezone <span className="text-[#94A3B8] font-normal">(e.g. UTC)</span>
              </label>
              <input type="text" value={recordingTimezone} onChange={e => setRecordingTimezone(e.target.value)}
                placeholder="UTC" className="rt-input" />
            </div>
          </div>

          {uploadError && (
            <div className="rt-banner-danger">{uploadError}</div>
          )}

          <button type="submit" disabled={uploading} className="rt-btn-primary">
            {uploading ? (
              <>
                <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                </svg>
                Uploading…
              </>
            ) : (
              <>
                <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
                  <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M12 12V4m0 0L8 8m4-4l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                Upload
              </>
            )}
          </button>
        </form>
        </div>
      )}

      {/* Recording list */}
      {recordings.length === 0 ? (
        <div className="rt-empty rt-card">
          <svg viewBox="0 0 24 24" fill="none" className="w-10 h-10 text-[#E2E8F0] mb-3">
            <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
          </svg>
          <p className="text-sm font-medium text-[#64748B]">No recordings yet</p>
          <p className="text-xs text-[#94A3B8] mt-1">Click "Upload Recording" to get started</p>
        </div>
      ) : (
        <div className="space-y-2">
          {recordings.map(r => {
            const status = STATUS_CONFIG[r.processing_status] ?? { label: r.processing_status, cls: 'rt-badge-gray' }
            return (
              <div key={r.recording_id} className="rt-card p-4 flex items-start gap-4 hover:shadow-md transition-shadow">
                {/* Thumbnail */}
                {r.thumbnail_path ? (
                  <button
                    onClick={() => setActiveVideo(r)}
                    className="relative flex-shrink-0 w-20 h-14 group rounded-lg overflow-hidden focus:outline-none focus:ring-2 focus:ring-[#2563EB]"
                    aria-label={`Play ${r.original_filename}`}
                  >
                    <img
                      src={`/thumbnails/${r.recording_id}.jpg`}
                      alt="thumbnail"
                      className="w-full h-full object-cover bg-[#F1F5F9]"
                    />
                    <div className="absolute inset-0 bg-black/30 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                      <svg viewBox="0 0 24 24" fill="white" className="w-6 h-6">
                        <path d="M8 5v14l11-7L8 5z"/>
                      </svg>
                    </div>
                  </button>
                ) : (
                  <div className="flex-shrink-0 w-20 h-14 rounded-lg bg-[#F1F5F9] flex items-center justify-center">
                    <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5 text-[#94A3B8]">
                      <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5"/>
                    </svg>
                  </div>
                )}

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className="font-medium text-[#0F172A] truncate text-sm">{r.original_filename}</span>
                    {r.camera_name && <span className="rt-badge-blue">{r.camera_name}</span>}
                    <span className={status.cls}>
                      {status.label}
                      {r.processing_status === 'PROCESSING' && (
                        <span className="ml-1.5 inline-block w-1.5 h-1.5 rounded-full bg-[#2563EB] animate-pulse" />
                      )}
                    </span>
                  </div>
                  <div className="text-xs text-[#64748B] space-y-0.5">
                    {r.camera_location && (
                      <div className="text-[#2563EB]">{r.camera_location}</div>
                    )}
                    {r.recording_datetime ? (
                      <div>
                        {new Date(r.recording_datetime).toLocaleString()}
                        {r.recording_timezone ? ` (${r.recording_timezone})` : ''}
                      </div>
                    ) : (
                      <div className="text-amber-600">Recording date unknown</div>
                    )}
                    <div className="flex gap-4 text-[#94A3B8]">
                      {r.indexing_status_detail?.startsWith('Direct video') ? (
                        <span>Direct analysis</span>
                      ) : (
                        <span>{r.frame_count} frames</span>
                      )}
                      {r.video_duration_seconds != null && <span>{formatDuration(r.video_duration_seconds)}</span>}
                      {r.file_size_bytes != null && <span>{(r.file_size_bytes / 1_000_000).toFixed(1)} MB</span>}
                    </div>
                    {r.indexing_status_detail && (
                      <div className="text-[#94A3B8]">{r.indexing_status_detail}</div>
                    )}
                  </div>
                  {(r.processing_errors ?? []).length > 0 && (
                    <details className="mt-1.5">
                      <summary className="text-xs text-[#DC2626] cursor-pointer hover:text-red-700">
                        {r.processing_errors!.length} processing warning(s)
                      </summary>
                      <ul className="text-xs text-[#DC2626] mt-1 space-y-0.5 pl-3 opacity-80">
                        {r.processing_errors!.slice(0, 5).map((e, i) => <li key={i}>· {e}</li>)}
                      </ul>
                    </details>
                  )}
                </div>

                {/* Delete */}
                <button
                  onClick={() => handleDelete(r.recording_id, r.original_filename)}
                  className="rt-btn-danger flex-shrink-0 p-1.5"
                  title="Delete recording"
                >
                  <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
                    <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                  </svg>
                </button>
              </div>
            )
          })}
        </div>
      )}

      {activeVideo && (
        <VideoPlayerModal recording={activeVideo} onClose={() => setActiveVideo(null)} />
      )}
    </div>
  )
}
