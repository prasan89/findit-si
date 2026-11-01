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

function confidenceBadge(score: number): string {
  if (score >= 0.7) return 'rt-badge-green'
  if (score >= 0.5) return 'rt-badge-amber'
  return 'rt-badge-red'
}

function confidenceLabel(score: number): string {
  if (score >= 0.7) return 'High confidence'
  if (score >= 0.5) return 'Medium confidence'
  return 'Low confidence'
}

export default function SightingCard({ sighting: s, highlight }: Props) {
  const frameUrl = `/${s.source_frame_path}`

  return (
    <div className={`rt-card p-4 ${highlight ? 'ring-2 ring-[#2563EB] ring-offset-1' : ''}`}>
      <div className="flex gap-4">
        <div className="flex-shrink-0">
          <img
            src={frameUrl}
            alt="Evidence frame"
            className={`rounded-lg object-cover bg-[#F1F5F9] ${highlight ? 'w-48 h-32' : 'w-32 h-24'}`}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2 mb-1">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-[#0F172A] capitalize">{s.object_label}</span>
              {s.object_attributes.color && (
                <span className="text-sm text-[#64748B]">· {s.object_attributes.color}</span>
              )}
            </div>
            <span className={confidenceBadge(s.detection_score)}>
              {confidenceLabel(s.detection_score)}
            </span>
          </div>

          <div className="text-sm text-[#64748B]">
            <span className="font-medium text-[#0F172A]">{s.recording_filename}</span>
            <span className="mx-2 text-[#E2E8F0]">·</span>
            <span>{formatTimestamp(s.video_timestamp_seconds)}</span>
          </div>

          {s.has_reliable_timestamp && s.recording_datetime ? (
            <div className="text-sm text-[#64748B] mt-0.5">
              {new Date(s.recording_datetime).toLocaleString()}
              {s.recording_timezone && ` (${s.recording_timezone})`}
            </div>
          ) : (
            <div className="text-xs text-amber-600 mt-0.5">Recording date unknown</div>
          )}

          {s.scene_description && (
            <p className="text-sm text-[#64748B] mt-1.5 line-clamp-2">{s.scene_description}</p>
          )}

          <p className="text-xs text-[#94A3B8] mt-2 italic">{s.match_explanation}</p>
        </div>
      </div>
    </div>
  )
}
