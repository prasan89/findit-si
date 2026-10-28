'use client'
import { useState, useRef } from 'react'
import type { Recording } from '@/types'

const STATUS_COLORS: Record<string, string> = {
  UPLOADED: 'bg-yellow-100 text-yellow-800',
  PENDING: 'bg-yellow-100 text-yellow-800',
  PROCESSING: 'bg-blue-100 text-blue-800',
  INDEXED: 'bg-green-100 text-green-800',
  PARTIAL: 'bg-orange-100 text-orange-800',
  FAILED: 'bg-red-100 text-red-800',
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

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h2 className="text-lg font-semibold mb-4">Upload CCTV Recording</h2>
        <form onSubmit={handleUpload} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Video files (.mp4, .mov, .avi, .mkv, .webm)
            </label>
            <input
              ref={fileRef}
              type="file"
              multiple
              accept=".mp4,.mov,.avi,.mkv,.webm"
              className="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100"
              required
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Camera name <span className="text-gray-400">(optional)</span>
              </label>
              <input
                type="text"
                value={cameraName}
                onChange={e => setCameraName(e.target.value)}
                placeholder="e.g. Entrance Camera"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Camera location <span className="text-gray-400">(optional)</span>
              </label>
              <input
                type="text"
                value={cameraLocation}
                onChange={e => setCameraLocation(e.target.value)}
                placeholder="e.g. Front Door"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Recording date/time <span className="text-gray-400">(optional)</span>
              </label>
              <input
                type="datetime-local"
                value={recordingDatetime}
                onChange={e => setRecordingDatetime(e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Timezone <span className="text-gray-400">(e.g. America/New_York)</span>
              </label>
              <input
                type="text"
                value={recordingTimezone}
                onChange={e => setRecordingTimezone(e.target.value)}
                placeholder="UTC"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
          </div>
          {uploadError && (
            <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{uploadError}</p>
          )}
          <button
            type="submit"
            disabled={uploading}
            className="px-5 py-2 bg-indigo-600 text-white rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
          >
            {uploading ? 'Uploading…' : 'Upload'}
          </button>
        </form>
      </div>

      {recordings.length === 0 ? (
        <p className="text-gray-500 text-center py-8">No videos uploaded yet.</p>
      ) : (
        <div className="space-y-3">
          {recordings.map(r => (
            <div key={r.recording_id} className="bg-white rounded-xl border border-gray-200 p-4 flex items-start gap-4">
              {r.thumbnail_path && (
                <img
                  src={`/thumbnails/${r.recording_id}.jpg`}
                  alt="thumbnail"
                  className="w-20 h-14 object-cover rounded-lg bg-gray-100 flex-shrink-0"
                />
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium text-gray-900 truncate">{r.original_filename}</span>
                  {r.camera_name && (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-800 font-medium">
                      {r.camera_name}
                    </span>
                  )}
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_COLORS[r.processing_status] ?? 'bg-gray-100 text-gray-800'}`}>
                    {r.processing_status}
                  </span>
                  {r.processing_status === 'PROCESSING' && (
                    <span className="animate-pulse text-xs text-blue-600">●</span>
                  )}
                </div>
                <div className="text-sm text-gray-500 mt-1 space-y-0.5">
                  {r.camera_location && <div className="text-indigo-600">{r.camera_location}</div>}
                  {r.recording_datetime ? (
                    <div>Recorded: {new Date(r.recording_datetime).toLocaleString()}{r.recording_timezone ? ` (${r.recording_timezone})` : ''}</div>
                  ) : (
                    <div className="text-orange-600">Recording date unknown</div>
                  )}
                  <div className="flex gap-4">
                    <span>{r.frame_count} frames indexed</span>
                    {r.video_duration_seconds && <span>Duration: {formatDuration(r.video_duration_seconds)}</span>}
                  </div>
                  {r.indexing_status_detail && <div className="text-gray-400">{r.indexing_status_detail}</div>}
                </div>
                {(r.processing_errors ?? []).length > 0 && (
                  <details className="mt-1">
                    <summary className="text-xs text-red-600 cursor-pointer">{r.processing_errors!.length} processing warning(s)</summary>
                    <ul className="text-xs text-red-500 mt-1 space-y-0.5 pl-3">
                      {r.processing_errors!.slice(0, 5).map((e, i) => <li key={i}>{e}</li>)}
                    </ul>
                  </details>
                )}
              </div>
              <button
                onClick={() => handleDelete(r.recording_id, r.original_filename)}
                className="text-gray-400 hover:text-red-600 transition-colors flex-shrink-0 px-2 py-1 text-sm"
                title="Delete recording"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}