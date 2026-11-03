'use client'
import { useState, useRef } from 'react'
import type { Recording, ProcessingStatus } from '@/types'

// ── Status badge config ───────────────────────────────────────────────────────

interface StatusConfig {
  label: string
  className: string
  animated?: boolean
}

const STATUS_CONFIG: Record<ProcessingStatus, StatusConfig> = {
  INDEXED:    { label: 'Indexed',    className: 'bg-green-100 text-green-700 border border-green-200' },
  PROCESSING: { label: 'Processing', className: 'bg-blue-100 text-blue-700 border border-blue-200', animated: true },
  UPLOADED:   { label: 'Uploaded',   className: 'bg-slate-100 text-slate-600 border border-slate-200' },
  PENDING:    { label: 'Pending',    className: 'bg-slate-100 text-slate-600 border border-slate-200' },
  PARTIAL:    { label: 'Partial',    className: 'bg-amber-100 text-amber-700 border border-amber-200' },
  FAILED:     { label: 'Failed',     className: 'bg-red-100 text-red-700 border border-red-200' },
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  recordings: Recording[]
  onRecordingSelect: (r: Recording) => void
  onRefresh: () => void
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDuration(secs?: number): string {
  if (secs == null) return ''
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatTimestamp(dt?: string, tz?: string): string {
  if (!dt) return ''
  try {
    const d = new Date(dt)
    const base = d.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
    return tz ? `${base} (${tz})` : base
  } catch {
    return dt
  }
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: ProcessingStatus }) {
  const cfg = STATUS_CONFIG[status] ?? { label: status, className: 'bg-slate-100 text-slate-600 border border-slate-200' }
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${cfg.className}`}>
      {cfg.animated && (
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
      )}
      {cfg.label}
    </span>
  )
}

function SkeletonCard() {
  return (
    <div className="rounded-xl border border-slate-200 bg-white overflow-hidden animate-pulse">
      <div className="aspect-video bg-slate-200" />
      <div className="p-3 space-y-2">
        <div className="h-3.5 bg-slate-200 rounded w-3/4" />
        <div className="h-3 bg-slate-100 rounded w-1/2" />
        <div className="h-3 bg-slate-100 rounded w-2/3" />
      </div>
    </div>
  )
}

function VideoIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <path
        d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z"
        stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"
      />
    </svg>
  )
}

// ── Upload form ───────────────────────────────────────────────────────────────

interface UploadFormProps {
  onSuccess: () => void
  onClose: () => void
}

function UploadForm({ onSuccess, onClose }: UploadFormProps) {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [cameraName, setCameraName] = useState('')
  const [cameraLocation, setCameraLocation] = useState('')
  const [recordingDatetime, setRecordingDatetime] = useState('')
  const [timezone, setTimezone] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const files = fileRef.current?.files
    if (!files || files.length === 0) return
    setUploading(true)
    setError(null)

    for (const file of Array.from(files)) {
      const form = new FormData()
      form.append('file', file)
      if (recordingDatetime) form.append('recording_datetime', recordingDatetime)
      if (timezone) form.append('recording_timezone', timezone)
      if (cameraName) form.append('camera_name', cameraName)
      if (cameraLocation) form.append('camera_location', cameraLocation)

      try {
        const res = await fetch('/api/recordings/upload', { method: 'POST', body: form })
        if (!res.ok) {
          const err = await res.json().catch(() => ({}))
          setError(`Upload failed for ${file.name}: ${err.detail ?? res.statusText}`)
          setUploading(false)
          return
        }
      } catch {
        setError(`Network error uploading ${file.name}`)
        setUploading(false)
        return
      }
    }

    setUploading(false)
    if (fileRef.current) fileRef.current.value = ''
    onSuccess()
    onClose()
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Panel header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Upload footage</h3>
          <p className="text-xs text-slate-500 mt-0.5">Supported: .mp4 .mov .avi .mkv .webm</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
          aria-label="Close upload panel"
        >
          <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
            <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
          </svg>
        </button>
      </div>

      <form onSubmit={handleSubmit} className="p-5 space-y-4">
        {/* File picker */}
        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1.5">
            Video files <span className="text-red-500">*</span>
          </label>
          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".mp4,.mov,.avi,.mkv,.webm"
            required
            className="block w-full text-sm text-slate-600
              file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0
              file:text-sm file:font-medium file:bg-blue-50 file:text-blue-700
              hover:file:bg-blue-100 file:cursor-pointer file:transition-colors"
          />
        </div>

        {/* Metadata grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">
              Camera name <span className="text-slate-400 font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={cameraName}
              onChange={e => setCameraName(e.target.value)}
              placeholder="e.g. Entrance Camera"
              className="block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900
                placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                transition-colors"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">
              Camera location <span className="text-slate-400 font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={cameraLocation}
              onChange={e => setCameraLocation(e.target.value)}
              placeholder="e.g. Front Door"
              className="block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900
                placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                transition-colors"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">
              Recording date &amp; time <span className="text-slate-400 font-normal">(optional)</span>
            </label>
            <input
              type="datetime-local"
              value={recordingDatetime}
              onChange={e => setRecordingDatetime(e.target.value)}
              className="block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900
                focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-colors"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">
              Timezone <span className="text-slate-400 font-normal">(e.g. UTC, America/New_York)</span>
            </label>
            <input
              type="text"
              value={timezone}
              onChange={e => setTimezone(e.target.value)}
              placeholder="UTC"
              className="block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900
                placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                transition-colors"
            />
          </div>
        </div>

        {error && (
          <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2.5 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-2 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-100 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={uploading}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium
              hover:bg-blue-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
          >
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
                  <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M12 12V4m0 0L8 8m4-4l4 4"
                    stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                Upload
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  )
}

// ── Recording card ────────────────────────────────────────────────────────────

interface RecordingCardProps {
  recording: Recording
  onSelect: (r: Recording) => void
  onDelete: (id: string, filename: string) => void
}

function RecordingCard({ recording: r, onSelect, onDelete }: RecordingCardProps) {
  const [imgError, setImgError] = useState(false)
  const hasThumbnail = !!r.thumbnail_path && !imgError
  const duration = formatDuration(r.video_duration_seconds)
  const timestamp = formatTimestamp(r.recording_datetime, r.recording_timezone)

  return (
    <div className="group relative rounded-xl border border-slate-200 bg-white overflow-hidden shadow-sm hover:shadow-md transition-shadow">
      {/* Thumbnail */}
      <button
        className="block w-full aspect-video bg-slate-100 overflow-hidden focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset"
        onClick={() => onSelect(r)}
        aria-label={`View ${r.original_filename}`}
      >
        {hasThumbnail ? (
          <img
            src={`/thumbnails/${r.recording_id}.jpg`}
            alt={`Thumbnail for ${r.original_filename}`}
            className="w-full h-full object-cover"
            onError={() => setImgError(true)}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <VideoIcon className="w-10 h-10 text-slate-300" />
          </div>
        )}

        {/* Play overlay on hover */}
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/25 transition-colors flex items-center justify-center">
          <div className="opacity-0 group-hover:opacity-100 transition-opacity bg-white/90 rounded-full w-12 h-12 flex items-center justify-center shadow-lg">
            <svg viewBox="0 0 24 24" fill="none" width="20" height="20" aria-hidden="true">
              <polygon points="6,4 20,12 6,20" fill="#1E293B"/>
            </svg>
          </div>
        </div>

        {/* Duration overlay */}
        {duration && (
          <span className="absolute bottom-2 right-2 bg-black/60 text-white text-xs font-medium px-1.5 py-0.5 rounded">
            {duration}
          </span>
        )}

        {/* Play overlay on hover */}
        <div className="absolute inset-0 bg-black/20 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center pointer-events-none">
          <div className="w-10 h-10 rounded-full bg-white/90 flex items-center justify-center shadow-md">
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5 text-slate-700 ml-0.5">
              <path d="M8 5v14l11-7L8 5z"/>
            </svg>
          </div>
        </div>
      </button>

      {/* Card body */}
      <div className="p-3">
        <div className="flex items-start justify-between gap-2 mb-1.5">
          <div className="min-w-0 flex-1">
            {r.camera_name ? (
              <p className="text-sm font-semibold text-slate-900 truncate">{r.camera_name}</p>
            ) : null}
            <p className="text-xs text-slate-500 truncate" title={r.original_filename}>
              {r.original_filename}
            </p>
          </div>
          {/* Delete button — visible on hover */}
          <button
            onClick={e => { e.stopPropagation(); onDelete(r.recording_id, r.original_filename) }}
            className="flex-shrink-0 p-1 rounded-md text-slate-300 hover:text-red-600 hover:bg-red-50
              opacity-0 group-hover:opacity-100 transition-all focus:opacity-100"
            aria-label={`Delete ${r.original_filename}`}
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
              <path d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        </div>

        <div className="flex items-center justify-between gap-2 flex-wrap">
          <StatusBadge status={r.processing_status} />
          {timestamp && (
            <span className="text-xs text-slate-400 truncate">{timestamp}</span>
          )}
        </div>

        {r.camera_location && (
          <p className="mt-1.5 text-xs text-slate-400 truncate">
            {r.camera_location}
          </p>
        )}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function VideoLibrary({ recordings, onRecordingSelect, onRefresh }: Props) {
  const [uploadOpen, setUploadOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [loading] = useState(false)

  const handleDelete = async (id: string, filename: string) => {
    if (!confirm(`Delete "${filename}" and all its indexed data?`)) return
    await fetch(`/api/recordings/${id}`, { method: 'DELETE' })
    onRefresh()
  }

  // Filtered recordings
  const filtered = search.trim()
    ? recordings.filter(r => {
        const q = search.toLowerCase()
        return (
          r.original_filename.toLowerCase().includes(q) ||
          (r.camera_name ?? '').toLowerCase().includes(q) ||
          (r.camera_location ?? '').toLowerCase().includes(q)
        )
      })
    : recordings

  // Stats
  const indexedCount = recordings.filter(r => r.processing_status === 'INDEXED').length
  const processingCount = recordings.filter(r => r.processing_status === 'PROCESSING').length

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Recordings</h1>
          <p className="text-sm text-slate-500 mt-0.5">Your uploaded security footage</p>
        </div>
        <button
          onClick={() => setUploadOpen(o => !o)}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium
            hover:bg-blue-700 transition-colors shadow-sm flex-shrink-0"
        >
          <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
            <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M12 12V4m0 0L8 8m4-4l4 4"
              stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          Upload
        </button>
      </div>

      {/* Upload panel (accordion) */}
      {uploadOpen && (
        <UploadForm
          onSuccess={onRefresh}
          onClose={() => setUploadOpen(false)}
        />
      )}

      {/* Search bar */}
      {recordings.length > 0 && (
        <div className="relative">
          <svg viewBox="0 0 24 24" fill="none" className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none">
            <path d="M21 21l-4.35-4.35M17 11A6 6 0 111 11a6 6 0 0116 0z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search by camera name or filename…"
            className="block w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 py-2 text-sm text-slate-900
              placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
              transition-colors"
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 p-0.5 text-slate-400 hover:text-slate-600 rounded"
              aria-label="Clear search"
            >
              <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5">
                <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
            </button>
          )}
        </div>
      )}

      {/* Stats bar */}
      {recordings.length > 0 && (
        <div className="flex items-center gap-1.5 text-xs text-slate-500">
          <span className="font-medium text-slate-700">{recordings.length}</span>
          <span>{recordings.length === 1 ? 'recording' : 'recordings'}</span>
          {indexedCount > 0 && (
            <>
              <span className="text-slate-300">·</span>
              <span className="font-medium text-green-600">{indexedCount}</span>
              <span>indexed</span>
            </>
          )}
          {processingCount > 0 && (
            <>
              <span className="text-slate-300">·</span>
              <span className="font-medium text-blue-600">{processingCount}</span>
              <span>processing</span>
            </>
          )}
          {search && filtered.length !== recordings.length && (
            <>
              <span className="text-slate-300">·</span>
              <span>{filtered.length} shown</span>
            </>
          )}
        </div>
      )}

      {/* Grid */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : recordings.length === 0 ? (
        /* Empty state */
        <div className="flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-200 bg-white py-16 px-6 text-center">
          <div className="w-14 h-14 rounded-full bg-slate-100 flex items-center justify-center mb-4">
            <VideoIcon className="w-7 h-7 text-slate-400" />
          </div>
          <h3 className="text-sm font-semibold text-slate-700 mb-1">No recordings yet</h3>
          <p className="text-sm text-slate-400 mb-5 max-w-xs">
            Upload your first security footage to start investigating incidents.
          </p>
          <button
            onClick={() => setUploadOpen(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium
              hover:bg-blue-700 transition-colors"
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
              <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M12 12V4m0 0L8 8m4-4l4 4"
                stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            Upload footage
          </button>
        </div>
      ) : filtered.length === 0 ? (
        /* Search empty state */
        <div className="flex flex-col items-center justify-center rounded-xl border border-slate-200 bg-white py-12 px-6 text-center">
          <svg viewBox="0 0 24 24" fill="none" className="w-8 h-8 text-slate-300 mb-3">
            <path d="M21 21l-4.35-4.35M17 11A6 6 0 111 11a6 6 0 0116 0z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          <p className="text-sm font-medium text-slate-600">No recordings match &ldquo;{search}&rdquo;</p>
          <button onClick={() => setSearch('')} className="mt-3 text-sm text-blue-600 hover:underline">
            Clear search
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map(r => (
            <RecordingCard
              key={r.recording_id}
              recording={r}
              onSelect={onRecordingSelect}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </div>
  )
}
