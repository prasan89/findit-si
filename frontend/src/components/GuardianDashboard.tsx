'use client'
import { useCallback, useEffect, useState } from 'react'
import type { GuardianAlert, Event, Recording } from '@/types'
import {
  deduplicateItems,
  matchesFilter,
  withinTimeRange,
  type FeedItem,
  type FeedFilter,
  type TimeRange,
} from '@/lib/guardianRouting'
import GuardianSidebar from './GuardianSidebar'
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
  const [search, setSearch] = useState('')

  const indexedRecordings = recordings.filter(
    r => r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL'
  )

  const loadData = useCallback(async () => {
    if (indexedRecordings.length === 0) return
    setLoading(true)
    setError(null)
    try {
      // Fetch all alerts across all recordings in one call
      const alertRes = await fetch('/api/guardian-alerts?limit=200')
      if (!alertRes.ok) throw new Error(`Guardian alerts failed (${alertRes.status})`)
      const alertData = await alertRes.json()
      setAlerts(alertData.alerts ?? [])

      // Batch fetch events for all indexed recordings
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

  // Build deduplicated feed items
  const allItems = deduplicateItems(alerts, events, recordings)

  // Apply camera filter
  const cameraFiltered = selectedCameraId
    ? allItems.filter(i => i.recording_id === selectedCameraId)
    : allItems

  // Apply event filter and time range
  const visibleItems = cameraFiltered
    .filter(i => matchesFilter(i, filter))
    .filter(i => withinTimeRange(i, timeRange))

  // Optimistic acknowledge: update local state immediately, then PATCH
  async function handleAcknowledge(item: FeedItem, status: 'acknowledged' | 'dismissed') {
    if (!item.alert) return
    // Optimistic update
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
        // Revert on error
        setAlerts(prev =>
          prev.map(a =>
            a.alert_id === item.alert!.alert_id
              ? { ...a, review_status: item.alert!.review_status }
              : a
          )
        )
      }
    } catch {
      // Revert silently
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

  const hasMocked = alerts.some(a => a.is_mocked)
  const connectedCount = indexedRecordings.length

  // Find the recording for the selected item
  const selectedRecording = selectedItem
    ? recordings.find(r => r.recording_id === selectedItem.recording_id)
    : undefined

  return (
    <div className="flex flex-col h-full">
      {/* Dashboard header */}
      <div className="flex items-center justify-between px-5 py-3 bg-white border-b border-[#E2E8F0] shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-[#2563EB] flex items-center justify-center">
              <svg viewBox="0 0 24 24" fill="none" style={{ width: 15, height: 15 }}>
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
            <span className="font-bold text-[#0F172A] text-sm">RealityTrace Guardian</span>
          </div>
          <span className="text-xs text-[#64748B] hidden sm:block">AI CCTV Event Notification</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${connectedCount > 0 ? 'bg-green-500' : 'bg-[#94A3B8]'}`} />
            <span className="text-xs text-[#64748B]">
              {connectedCount} camera{connectedCount !== 1 ? 's' : ''} connected
            </span>
          </div>
          <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-blue-100 text-blue-700 border border-blue-200 flex items-center gap-1">
            <svg viewBox="0 0 24 24" fill="none" className="w-3 h-3">
              <path d="M19.7 14A7.3 7.3 0 1012 6.7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
              <path d="M12 10v4M12 18h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            Simulation mode
          </span>
          <button
            onClick={loadData}
            disabled={loading}
            className="rt-btn-ghost text-xs py-1 px-2"
            aria-label="Refresh"
          >
            {loading ? '↻ Loading…' : '↻ Refresh'}
          </button>
        </div>
      </div>

      {/* Mocked-data notice */}
      {hasMocked && (
        <div className="rt-banner-info text-xs px-5 py-2 rounded-none border-0 border-b border-blue-100">
          Some recordings were processed in simulation mode — alerts shown are illustrative only.
        </div>
      )}

      {error && (
        <div className="rt-banner-danger text-xs px-5 py-2 rounded-none border-0 border-b">
          {error}
        </div>
      )}

      {/* Empty state */}
      {!loading && indexedRecordings.length === 0 && (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center px-6">
          <div className="w-14 h-14 rounded-2xl bg-[#F1F5F9] flex items-center justify-center text-3xl">
            📷
          </div>
          <p className="text-sm font-semibold text-[#475569]">No indexed recordings</p>
          <p className="text-xs text-[#94A3B8] max-w-xs">
            Upload and index video recordings to see Guardian alerts and events here.
          </p>
        </div>
      )}

      {/* Three-column layout */}
      {(loading || indexedRecordings.length > 0) && (
        <div className="flex flex-1 min-h-0">
          {/* Left sidebar */}
          <GuardianSidebar
            recordings={recordings}
            selectedCameraId={selectedCameraId}
            onSelectCamera={setSelectedCameraId}
            filter={filter}
            onFilterChange={setFilter}
            timeRange={timeRange}
            onTimeRangeChange={setTimeRange}
            items={allItems}
            search={search}
            onSearchChange={setSearch}
          />

          {/* Main feed */}
          <div className="flex-1 flex flex-col min-h-0 min-w-0">
            {loading && allItems.length === 0 ? (
              <div className="flex-1 flex items-center justify-center">
                <div className="text-sm text-[#94A3B8]">Loading events…</div>
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

          {/* Right evidence panel */}
          {selectedItem && (
            <div className="w-72 shrink-0 border-l border-[#E2E8F0] bg-white overflow-hidden">
              <GuardianEvidencePanel
                item={selectedItem}
                recording={selectedRecording}
                onClose={() => setSelectedItem(null)}
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
