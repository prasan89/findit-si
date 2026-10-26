import type { Sighting } from '@/types'

interface Props {
  sighting: Sighting
  highlight?: boolean
}

function formatTimestamp(secs: number): string {
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = Math.floor(secs % 60)
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`
}

function confidenceLabel(score: number): { text: string; cls: string } {
  if (score >= 0.7) return { text: 'High confidence', cls: 'text-green-700 bg-green-50' }
  if (score >= 0.5) return { text: 'Medium confidence', cls: 'text-yellow-700 bg-yellow-50' }
  return { text: 'Low confidence', cls: 'text-red-700 bg-red-50' }
}

export default function SightingCard({ sighting: s, highlight }: Props) {
  const conf = confidenceLabel(s.detection_score)
  // source_frame_path is like "frames/{recording_id}/{frame_id}.jpg"
  const frameUrl = `/${s.source_frame_path}`

  return (
    <div className={`rounded-xl border p-4 ${highlight ? 'border-blue-300 bg-blue-50' : 'border-gray-200 bg-white'}`}>
      <div className="flex gap-4">
        <div className="flex-shrink-0">
          <img
            src={frameUrl}
            alt="Evidence frame"
            className={`rounded-lg object-cover bg-gray-100 ${highlight ? 'w-48 h-32' : 'w-32 h-24'}`}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div>
              <span className="font-semibold text-gray-900 capitalize">{s.object_label}</span>
              {s.object_attributes.color && (
                <span className="ml-2 text-sm text-gray-600">· {s.object_attributes.color}</span>
              )}
            </div>
            <span className={`text-xs px-2 py-1 rounded-full font-medium flex-shrink-0 ${conf.cls}`}>
              {conf.text}
            </span>
          </div>

          <div className="text-sm text-gray-700 mt-1">
            <span className="font-medium">{s.recording_filename}</span>
            <span className="text-gray-400 mx-2">·</span>
            <span>{formatTimestamp(s.video_timestamp_seconds)}</span>
          </div>

          {s.has_reliable_timestamp && s.recording_datetime ? (
            <div className="text-sm text-gray-600 mt-0.5">
              Recorded: {new Date(s.recording_datetime).toLocaleString()}
              {s.recording_timezone && ` (${s.recording_timezone})`}
            </div>
          ) : (
            <div className="text-xs text-orange-600 mt-0.5">Recording date unknown</div>
          )}

          {s.scene_description && (
            <p className="text-sm text-gray-600 mt-1 line-clamp-2">{s.scene_description}</p>
          )}

          <p className="text-xs text-gray-500 mt-2 italic">{s.match_explanation}</p>
        </div>
      </div>
    </div>
  )
}
