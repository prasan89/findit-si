'use client'
import { useCallback, useEffect, useState } from 'react'
import type { GuardianAlert, Event, Recording } from '@/types'
import {
  deduplicateItems,
  matchesFilter,
  withinTimeRange,
  FILTER_OPTIONS,
  type FeedItem,
  type FeedFilter,
  type TimeRange,
} from '@/lib/guardianRouting'
import GuardianFeed from './GuardianFeed'
import GuardianEvidencePanel from './GuardianEvidencePanel'

type FeedTab = 'police' | 'owner'

interface Props {
  recordings: Recording[]
}

export default function GuardianDashboard({ recordings }: Props) {
  const [alerts, setAlerts] = useState<GuardianAlert[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // UI state
  const [tab, setTab] = useState<FeedTab>('police')
  const [selectedItem, setSelectedItem] = useState<FeedItem | null>(null)
  const [selectedCameraId, setSelectedCameraId] = useState<string | null>(null)
  const [filter, setFilter] = useState<FeedFilter>('all')
  const [timeRange, setTimeRange] = useState<TimeRange>('all')

  const indexedRecordings = recordings.filter(
    r => r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL'
  )

  const loadData = useCallback(async () => {
    if (indexedRecordings.length === 0) return
    setLoading(true)
    setError(null)
    try {
      const alertRes = await fetch('/api/guardian-alerts?limit=200')
      if (!alertRes.ok) throw new Error(`Guardian alerts failed (${alertRes.status})`)
      const alertData = await alertRes.json()
      setAlerts(alertData.alerts ?? [])

      const evResults = await Promise.allSettled(
        indexedRecordings.map(r =>
          fetch(`/api/recordings/${r.recording_id}/events`).then(r => r.json())
        )
      )
      const allEvents: Event[] = evResults.flatMap(res =>
        res.status === 'fulfilled' ? (res.value.events ?? []) : []
      )
      setEvents(allEvents)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data')
    }
    setLoading(false)
  }, [indexedRecordings.map(r => r.recording_id).join(',')])

  useEffect(() => { loadData() }, [loadData])

  const allItems = deduplicateItems(alerts, events, recordings)

  const cameraFiltered = selectedCameraId
    ? allItems.filter(i => i.recording_id === selectedCameraId)
    : allItems

  const visibleItems = cameraFiltered
    .filter(i => matchesFilter(i, filter))
    .filter(i => withinTimeRange(i, timeRange))

  async function handleAcknowledge(item: FeedItem, status: 'acknowledged' | 'dismissed') {
    if (!item.alert) return
    setAlerts(prev =>
      prev.map(a =>
        a.alert_id === item.alert!.alert_id ? { ...a, review_status: status } : a
      )
    )
    if (selectedItem?.id === item.id) {
      setSelectedItem(prev => prev ? { ...prev, review_status: status } : null)
    }
    try {
      const res = await fetch(
        `/api/recordings/${item.recording_id}/guardian-alerts/${item.alert.alert_id}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ review_status: status }),
        }
      )
      if (!res.ok) {
        setAlerts(prev =>
          prev.map(a =>
            a.alert_id === item.alert!.alert_id
              ? { ...a, review_status: item.alert!.review_status }
              : a
          )
        )
      }
    } catch {
      setAlerts(prev =>
        prev.map(a =>
          a.alert_id === item.alert!.alert_id
            ? { ...a, review_status: item.alert!.review_status }
            : a
        )
      )
    }
  }

  function handleSelectItem(item: FeedItem) {
    setSelectedItem(prev => prev?.id === item.id ? null : item)
  }

  const selectedRecording = selectedItem
    ? recordings.find(r => r.recording_id === selectedItem.recording_id)
    : undefined

  // ── Derived stats ────────────────────────────────────────────────────────────
  const totalAlerts = allItems.length
  const criticalCount = allItems.filter(i => i.severityTag === 'Critical').length
  const pendingCount = allItems.filter(
    i => i.review_status === 'new'
  ).length

  const todayCutoff = Date.now() - 86_400_000
  const todayCount = allItems.filter(i => {
    if (i.alert?.created_at) return new Date(i.alert.created_at).getTime() >= todayCutoff
    if (i.event?.timestamp_absolute) return new Date(i.event.timestamp_absolute).getTime() >= todayCutoff
    return false
  }).length

  return (
    <div className="flex flex-col h-full bg-[#F8FAFC]">

      {/* ── Page header ─────────────────────────────────────────────────────── */}
      <div className="shrink-0 px-6 pt-5 pb-4 bg-white border-b border-[#E2E8F0]">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-[#2563EB] flex items-center justify-center shrink-0">
              <svg viewBox="0 0 24 24" fill="none" width={18} height={18}>
                <path
                  d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"
                  stroke="white"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <div>
              <h1 className="text-base font-bold text-[#0F172A] leading-tight">Guardian</h1>
              <p className="text-xs text-[#64748B] leading-tight mt-0.5">Real-time security monitoring</p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {alerts.some(a => a.is_mocked) && (
              <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-blue-50 text-blue-600 border border-blue-200 flex items-center gap-1">
                <svg viewBox="0 0 24 24" fill="none" className="w-3 h-3">
                  <path d="M19.7 14A7.3 7.3 0 1012 6.7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                  <path d="M12 10v4M12 18h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
                Simulation
              </span>
            )}
            <div className="flex items-center gap-1.5">
              <span className={`w-2 h-2 rounded-full ${indexedRecordings.length > 0 ? 'bg-green-500' : 'bg-[#94A3B8]'}`} />
              <span className="text-xs text-[#64748B]">
                {indexedRecordings.length} camera{indexedRecordings.length !== 1 ? 's' : ''}
              </span>
            </div>
            <button
              onClick={loadData}
              disabled={loading}
              className="text-xs py-1 px-2.5 rounded border border-[#E2E8F0] bg-white text-[#475569] hover:border-[#CBD5E1] hover:text-[#0F172A] disabled:opacity-50 transition-colors"
              aria-label="Refresh"
            >
              {loading ? '↻ Loading…' : '↻ Refresh'}
            </button>
          </div>
        </div>

        {/* ── Stats row ──────────────────────────────────────────────────────── */}
        {!loading || allItems.length > 0 ? (
          <div className="grid grid-cols-4 gap-3 mt-4">
            <StatCard
              label="Total Events"
              value={totalAlerts}
              color="blue"
            />
            <StatCard
              label="Critical"
              value={criticalCount}
              color="red"
            />
            <StatCard
              label="Pending Review"
              value={pendingCount}
              color="amber"
            />
            <StatCard
              label="Today"
              value={todayCount}
              color="slate"
            />
          </div>
        ) : null}
      </div>

      {error && (
        <div className="shrink-0 px-6 py-2 bg-red-50 border-b border-red-200 text-xs text-red-700">
          {error}
        </div>
      )}

      {/* ── No indexed recordings ────────────────────────────────────────────── */}
      {!loading && indexedRecordings.length === 0 && (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center px-6">
          <div className="w-14 h-14 rounded-2xl bg-[#F1F5F9] flex items-center justify-center">
            <svg viewBox="0 0 24 24" fill="none" width={28} height={28}>
              <rect x="2" y="4" width="20" height="16" rx="2" stroke="#94A3B8" strokeWidth="1.5"/>
              <path d="M10 9l5 3-5 3V9z" fill="#94A3B8"/>
            </svg>
          </div>
          <p className="text-sm font-semibold text-[#475569]">No indexed recordings</p>
          <p className="text-xs text-[#94A3B8] max-w-xs">
            Upload and index video recordings to see Guardian alerts and events here.
          </p>
        </div>
      )}

      {/* ── Main content ─────────────────────────────────────────────────────── */}
      {(loading || indexedRecordings.length > 0) && (
        <div className="flex flex-col flex-1 min-h-0">

          {/* ── Filter bar ─────────────────────────────────────────────────── */}
          <div className="shrink-0 flex items-center gap-3 px-5 py-2.5 bg-white border-b border-[#E2E8F0] flex-wrap">
            {/* Camera selector */}
            <select
              value={selectedCameraId ?? ''}
              onChange={e => setSelectedCameraId(e.target.value || null)}
              className="text-xs py-1.5 px-2.5 rounded border border-[#E2E8F0] bg-white text-[#475569] focus:outline-none focus:border-[#2563EB] w-48"
              aria-label="Filter by camera"
            >
              <option value="">All cameras ({indexedRecordings.length})</option>
              {indexedRecordings.map(r => (
                <option key={r.recording_id} value={r.recording_id}>
                  {r.camera_name ?? r.original_filename}
                </option>
              ))}
            </select>

            {/* Filter pills */}
            <div className="flex gap-1">
              {FILTER_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setFilter(opt.value)}
                  className={`text-xs px-3 py-1 rounded-full border font-medium transition-colors ${
                    filter === opt.value
                      ? 'bg-[#2563EB] text-white border-[#2563EB]'
                      : 'bg-white text-[#64748B] border-[#E2E8F0] hover:border-[#2563EB] hover:text-[#2563EB]'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            {/* Time range */}
            <select
              value={timeRange}
              onChange={e => setTimeRange(e.target.value as TimeRange)}
              className="text-xs py-1.5 px-2.5 rounded border border-[#E2E8F0] bg-white text-[#475569] focus:outline-none focus:border-[#2563EB] w-32 ml-auto"
              aria-label="Time range"
            >
              <option value="24h">Last 24h</option>
              <option value="7d">Last 7 days</option>
              <option value="all">All time</option>
            </select>
          </div>

          {/* ── Feed + panel ───────────────────────────────────────────────── */}
          <div className="flex flex-1 min-h-0">

            {/* Left: feed */}
            <div className="flex-1 flex flex-col min-h-0 min-w-0">
              {loading && allItems.length === 0 ? (
                <div className="flex-1 flex flex-col items-center justify-center gap-3">
                  <div className="w-6 h-6 border-2 border-[#2563EB] border-t-transparent rounded-full animate-spin" />
                  <p className="text-sm text-[#94A3B8]">Loading alerts...</p>
                </div>
              ) : visibleItems.length === 0 ? (
                <div className="flex-1 flex flex-col items-center justify-center gap-2 text-center px-6">
                  <div className="w-10 h-10 rounded-xl bg-[#F1F5F9] flex items-center justify-center">
                    <svg viewBox="0 0 24 24" fill="none" width={20} height={20}>
                      <circle cx="11" cy="11" r="8" stroke="#94A3B8" strokeWidth="1.5"/>
                      <path d="M21 21l-4.35-4.35" stroke="#94A3B8" strokeWidth="1.5" strokeLinecap="round"/>
                    </svg>
                  </div>
                  <p className="text-sm font-medium text-[#475569]">No alerts in this time range</p>
                  <p className="text-xs text-[#94A3B8]">Try adjusting the filters or time range above.</p>
                </div>
              ) : (
                <GuardianFeed
                  items={visibleItems}
                  tab={tab}
                  onTabChange={setTab}
                  selectedItemId={selectedItem?.id ?? null}
                  onSelectItem={handleSelectItem}
                  onAcknowledge={handleAcknowledge}
                />
              )}
            </div>

            {/* Right: evidence panel */}
            {selectedItem && (
              <div className="w-80 shrink-0 border-l border-[#E2E8F0] bg-white overflow-hidden">
                <GuardianEvidencePanel
                  item={selectedItem}
                  recording={selectedRecording}
                  onClose={() => setSelectedItem(null)}
                />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── StatCard ──────────────────────────────────────────────────────────────────

type StatColor = 'blue' | 'red' | 'amber' | 'slate'

const STAT_COLORS: Record<StatColor, { bg: string; value: string; label: string }> = {
  blue:  { bg: 'bg-blue-50',   value: 'text-blue-700',  label: 'text-blue-500'  },
  red:   { bg: 'bg-red-50',    value: 'text-red-700',   label: 'text-red-400'   },
  amber: { bg: 'bg-amber-50',  value: 'text-amber-700', label: 'text-amber-500' },
  slate: { bg: 'bg-[#F8FAFC]', value: 'text-[#334155]', label: 'text-[#94A3B8]' },
}

function StatCard({
  label,
  value,
  color,
}: {
  label: string
  value: number
  color: StatColor
}) {
  const c = STAT_COLORS[color]
  return (
    <div className={`rounded-xl px-4 py-3 ${c.bg} flex flex-col gap-0.5`}>
      <span className={`text-2xl font-bold tabular-nums ${c.value}`}>{value}</span>
      <span className={`text-xs font-medium ${c.label}`}>{label}</span>
    </div>
  )
}
