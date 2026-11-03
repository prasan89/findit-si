'use client'
import type { Recording } from '@/types'
import type { FeedFilter, FeedItem, TimeRange } from '@/lib/guardianRouting'

interface Props {
  recordings: Recording[]
  selectedCameraId: string | null
  onSelectCamera: (id: string | null) => void
  filter: FeedFilter
  onFilterChange: (f: FeedFilter) => void
  timeRange: TimeRange
  onTimeRangeChange: (r: TimeRange) => void
  items: FeedItem[]
  search: string
  onSearchChange: (s: string) => void
}

const FILTER_OPTIONS: { value: FeedFilter; label: string }[] = [
  { value: 'all', label: 'All Events' },
  { value: 'police_urgent', label: 'Police / Urgent' },
  { value: 'unusual_review', label: 'Unusual / Review' },
  { value: 'routine', label: 'Routine Activity' },
]

const TIME_OPTIONS: { value: TimeRange; label: string }[] = [
  { value: '24h', label: 'Last 24 Hours' },
  { value: '7d', label: 'Last 7 Days' },
  { value: 'all', label: 'All Time' },
]

export default function GuardianSidebar({
  recordings,
  selectedCameraId,
  onSelectCamera,
  filter,
  onFilterChange,
  timeRange,
  onTimeRangeChange,
  items,
  search,
  onSearchChange,
}: Props) {
  const indexedRecordings = recordings.filter(
    r => r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL'
  )

  const filtered = search.trim()
    ? indexedRecordings.filter(r =>
        (r.camera_name ?? r.original_filename).toLowerCase().includes(search.toLowerCase())
      )
    : indexedRecordings

  function countForRecording(recordingId: string): number {
    return items.filter(i => i.recording_id === recordingId).length
  }

  const filterCounts: Record<FeedFilter, number> = {
    all: items.length,
    police_urgent: items.filter(i => i.feeds.includes('police')).length,
    unusual_review: items.filter(i => i.severityTag === 'Review').length,
    routine: items.filter(i => i.severityTag === 'Routine').length,
  }

  return (
    <aside className="w-56 shrink-0 flex flex-col border-r border-[#E2E8F0] bg-white overflow-hidden">
      {/* Cameras section */}
      <div className="px-3 pt-4 pb-2">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-[#0F172A] uppercase tracking-wider">Cameras</span>
          <span className="text-xs text-[#64748B] bg-[#F1F5F9] px-1.5 py-0.5 rounded-full">
            {indexedRecordings.length}
          </span>
        </div>
        <input
          type="text"
          placeholder="Search cameras..."
          value={search}
          onChange={e => onSearchChange(e.target.value)}
          className="rt-input text-xs py-1.5"
          aria-label="Search cameras"
        />
      </div>

      {/* Camera list */}
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {/* "All cameras" row */}
        <button
          onClick={() => onSelectCamera(null)}
          className={`w-full flex items-center gap-2 px-2 py-2 rounded-lg text-xs transition-colors mb-0.5
            ${selectedCameraId === null
              ? 'bg-[#EFF6FF] text-[#2563EB] font-medium'
              : 'text-[#475569] hover:bg-[#F8FAFC]'
            }`}
        >
          <span className="w-2 h-2 rounded-full bg-[#94A3B8] shrink-0" />
          <span className="flex-1 text-left truncate">All cameras</span>
          <span className={`text-[10px] px-1 rounded-full ${selectedCameraId === null ? 'bg-[#DBEAFE] text-[#2563EB]' : 'bg-[#F1F5F9] text-[#94A3B8]'}`}>
            {items.length}
          </span>
        </button>

        {filtered.map(rec => {
          const isOnline = rec.processing_status === 'INDEXED' || rec.processing_status === 'PARTIAL'
          const isSelected = selectedCameraId === rec.recording_id
          const count = countForRecording(rec.recording_id)
          return (
            <button
              key={rec.recording_id}
              onClick={() => onSelectCamera(rec.recording_id)}
              className={`w-full flex items-center gap-2 px-2 py-2 rounded-lg text-xs transition-colors mb-0.5
                ${isSelected
                  ? 'bg-[#EFF6FF] text-[#2563EB] font-medium'
                  : 'text-[#475569] hover:bg-[#F8FAFC]'
                }`}
            >
              <span className={`w-2 h-2 rounded-full shrink-0 ${isOnline ? 'bg-green-500' : 'bg-[#94A3B8]'}`} />
              <div className="flex-1 min-w-0 text-left">
                <div className="truncate">{rec.camera_name ?? rec.original_filename}</div>
                {rec.camera_location && (
                  <div className="text-[10px] text-[#94A3B8] truncate">{rec.camera_location}</div>
                )}
              </div>
              {count > 0 && (
                <span className={`text-[10px] px-1 rounded-full shrink-0 ${isSelected ? 'bg-[#DBEAFE] text-[#2563EB]' : 'bg-[#F1F5F9] text-[#94A3B8]'}`}>
                  {count}
                </span>
              )}
            </button>
          )
        })}

        {filtered.length === 0 && search && (
          <p className="text-xs text-[#94A3B8] px-2 py-2">No cameras match "{search}"</p>
        )}

        {indexedRecordings.length === 0 && !search && (
          <p className="text-xs text-[#94A3B8] px-2 py-4 text-center">
            No indexed recordings yet.
          </p>
        )}
      </div>

      {/* Filters section */}
      <div className="border-t border-[#E2E8F0] px-3 py-3">
        <p className="text-xs font-semibold text-[#0F172A] uppercase tracking-wider mb-2">Filters</p>
        <div className="space-y-1">
          {FILTER_OPTIONS.map(opt => (
            <label key={opt.value} className="flex items-center gap-2 cursor-pointer group">
              <input
                type="radio"
                name="guardian-filter"
                value={opt.value}
                checked={filter === opt.value}
                onChange={() => onFilterChange(opt.value)}
                className="w-3.5 h-3.5 accent-[#2563EB]"
              />
              <span className="flex-1 text-xs text-[#475569] group-hover:text-[#0F172A] transition-colors">
                {opt.label}
              </span>
              <span className="text-[10px] text-[#94A3B8] bg-[#F1F5F9] px-1 rounded-full">
                {filterCounts[opt.value]}
              </span>
            </label>
          ))}
        </div>
      </div>

      {/* Time range section */}
      <div className="border-t border-[#E2E8F0] px-3 py-3">
        <p className="text-xs font-semibold text-[#0F172A] uppercase tracking-wider mb-2">Time Range</p>
        <select
          value={timeRange}
          onChange={e => onTimeRangeChange(e.target.value as TimeRange)}
          className="rt-input text-xs py-1.5"
          aria-label="Time range"
        >
          {TIME_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>
    </aside>
  )
}
