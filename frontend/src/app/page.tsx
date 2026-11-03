'use client'
import { useState, useEffect, useCallback } from 'react'
import AppShell from '@/components/AppShell'
import VideoLibrary from '@/components/VideoLibrary'
import UsageBadge from '@/components/UsageBadge'
import RealityTraceWorkspace from '@/components/RealityTraceWorkspace'
import InvestigationVideoList from '@/components/InvestigationVideoList'
import GuardianPanel from '@/components/GuardianPanel'
import VideoPlayerModal from '@/components/VideoPlayerModal'
import type {
  Recording,
  InvestigationResult,
  MultiAgentInvestigationResult,
  VideoAnalysisSummary,
} from '@/types'

type AppView = 'home' | 'results' | 'workspace' | 'library' | 'guardian'
type Mode = 'standard' | 'multi-agent'

const PAGE_TITLES: Record<string, string> = {
  home:      'Overview',
  library:   'Recordings',
  results:   'Investigation Results',
  workspace: 'Evidence Workspace',
  guardian:  'Guardian',
}

const EXAMPLE_QUERIES = [
  'Person in red jacket',
  'Vehicle at gate',
  'Package left unattended',
  'Suspicious activity near exit',
]

function CameraIcon({ size = 16 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" width={size} height={size} aria-hidden="true">
      <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

function SearchIconSm() {
  return (
    <svg viewBox="0 0 24 24" fill="none" width="16" height="16" aria-hidden="true">
      <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  )
}

function SpinnerIcon() {
  return (
    <svg className="animate-spin" viewBox="0 0 24 24" fill="none" width="15" height="15" aria-hidden="true">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.25"/>
      <path d="M12 2a10 10 0 0110 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round"/>
    </svg>
  )
}

function ShieldIconSm() {
  return (
    <svg viewBox="0 0 24 24" fill="none" width="16" height="16" aria-hidden="true">
      <path d="M12 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016A11.955 11.955 0 0112 2.944z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M9 12l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

function UploadIconSm() {
  return (
    <svg viewBox="0 0 24 24" fill="none" width="16" height="16" aria-hidden="true">
      <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M12 12V4m0 0l-3 3m3-3l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

function getStatusBadge(status: string) {
  if (status === 'INDEXED') return { label: 'Indexed', cls: 'bg-green-100 text-green-700' }
  if (status === 'PARTIAL') return { label: 'Partial', cls: 'bg-yellow-100 text-yellow-700' }
  if (status === 'PROCESSING') return { label: 'Processing', cls: 'bg-blue-100 text-blue-700' }
  return { label: status, cls: 'bg-slate-100 text-slate-500' }
}

function formatTimestamp(ts: string | undefined | null): string {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return ts
  }
}

export default function Home() {
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [view, setView] = useState<AppView>('home')
  const [query, setQuery] = useState('')
  const [cameraFilter, setCameraFilter] = useState('')
  const [mode, setMode] = useState<Mode>('standard')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [investigationResult, setInvestigationResult] = useState<InvestigationResult | null>(null)
  const [multiAgentResult, setMultiAgentResult] = useState<MultiAgentInvestigationResult | null>(null)
  const [activeVideoSummary, setActiveVideoSummary] = useState<VideoAnalysisSummary | null>(null)
  const [previewRecording, setPreviewRecording] = useState<Recording | null>(null)

  const fetchRecordings = useCallback(async () => {
    try {
      const res = await fetch('/api/recordings/')
      if (res.ok) setRecordings(await res.json())
    } catch {}
  }, [])

  useEffect(() => {
    fetchRecordings()
    const interval = setInterval(fetchRecordings, 5000)
    return () => clearInterval(interval)
  }, [fetchRecordings])

  const indexedCount = recordings.filter(
    r => r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL'
  ).length

  const recordingCount = recordings.length

  const handleInvestigate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim() || loading) return
    setLoading(true)
    setError(null)
    setInvestigationResult(null)
    setMultiAgentResult(null)
    setActiveVideoSummary(null)

    try {
      if (mode === 'multi-agent') {
        const submitRes = await fetch('/api/investigations/multi-agent/jobs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: query.trim() }),
        })
        if (!submitRes.ok) {
          const err = await submitRes.json().catch(() => ({}))
          setError(err.detail || `Investigation submission failed (${submitRes.status})`)
          setLoading(false)
          return
        }
        const { job_id } = await submitRes.json()

        const stdPromise = fetch('/api/investigations/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: query.trim() }),
        }).then(r => r.ok ? r.json() : null).catch(() => null)

        const POLL_INTERVAL_MS = 2500
        const POLL_TIMEOUT_MS = 300_000
        const deadline = Date.now() + POLL_TIMEOUT_MS
        let maResult: MultiAgentInvestigationResult | null = null

        while (Date.now() < deadline) {
          await new Promise(res => setTimeout(res, POLL_INTERVAL_MS))
          let pollRes: Response
          try {
            pollRes = await fetch(`/api/investigations/multi-agent/jobs/${job_id}`)
          } catch {
            setError('Lost connection to backend while waiting for investigation results.')
            setLoading(false)
            return
          }
          if (!pollRes.ok) {
            setError(`Failed to poll investigation status (${pollRes.status})`)
            setLoading(false)
            return
          }
          const poll = await pollRes.json()
          if (poll.status === 'completed') { maResult = poll.result as MultiAgentInvestigationResult; break }
          if (poll.status === 'failed') {
            setError(poll.error || 'Multi-agent investigation failed')
            setLoading(false)
            return
          }
        }

        if (!maResult) {
          setError('Investigation timed out. The pipeline may still be running — try again shortly.')
          setLoading(false)
          return
        }

        setMultiAgentResult(maResult)
        const stdResult = await stdPromise
        if (stdResult) {
          setInvestigationResult(stdResult)
        } else {
          setInvestigationResult({
            investigation_id: maResult.investigation_id,
            query: maResult.query,
            events: [],
            narrative: maResult.final_report?.narrative ?? '',
            summary: maResult.final_report?.key_findings[0] ?? '',
            timestamp_ordering_reliable: false,
            ordering_explanation: 'Multi-agent mode — no event stream available',
            searched_recordings: 0,
            total_events_found: 0,
            evidence_gaps: [],
            uncertainty_summary: maResult.final_report?.uncertainty_notes.join(' ') ?? '',
            video_summaries: [],
          })
        }
        setView('results')
      } else {
        const res = await fetch('/api/investigations/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: query.trim() }),
        })
        if (!res.ok) {
          const err = await res.json().catch(() => ({}))
          setError(err.detail || `Investigation failed (${res.status})`)
        } else {
          setInvestigationResult(await res.json())
          setView('results')
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(`Network error: ${msg}. Is the backend running?`)
    }
    setLoading(false)
  }

  function handleOpenVideo(summary: VideoAnalysisSummary) {
    setActiveVideoSummary(summary)
    setView('workspace')
  }

  function handleBackToResults() {
    setActiveVideoSummary(null)
    setView('results')
  }

  function navigate(v: string) {
    setView(v === 'investigate' ? 'home' : v as AppView)
  }

  // ── Investigation results list ─────────────────────────────────────────────
  if (view === 'results' && investigationResult) {
    return (
      <AppShell
        activeView="investigate"
        onNavigate={navigate}
        recordingCount={recordingCount}
        indexedCount={indexedCount}
        pageTitle="Investigation Results"
        topbarRight={<UsageBadge />}
        guardianAlertCount={0}
      >
        <div className="h-full overflow-y-auto">
          <InvestigationVideoList
            results={investigationResult}
            onBack={() => setView('home')}
            onSelectVideo={(id, ts) => {
              const summary = investigationResult.video_summaries?.find(s => s.recording_id === id)
              if (summary) handleOpenVideo(summary)
            }}
            query={investigationResult.query}
          />
        </div>
      </AppShell>
    )
  }

  // ── Video detail workspace ─────────────────────────────────────────────────
  if (view === 'workspace' && investigationResult && activeVideoSummary) {
    const filteredResult = {
      ...investigationResult,
      events: investigationResult.events.filter(
        e => e.recording_id === activeVideoSummary.recording_id
      ),
    }
    return (
      <AppShell
        activeView="investigate"
        onNavigate={navigate}
        recordingCount={recordingCount}
        indexedCount={indexedCount}
        pageTitle="Evidence Workspace"
        topbarRight={<UsageBadge />}
        fullHeight
        guardianAlertCount={0}
      >
        <RealityTraceWorkspace
          recordings={recordings}
          result={filteredResult}
          multiAgentResult={multiAgentResult}
          onNewInvestigation={handleBackToResults}
          activeRecordingId={activeVideoSummary.recording_id}
          videoSummary={activeVideoSummary}
        />
      </AppShell>
    )
  }

  // ── Recordings library ─────────────────────────────────────────────────────
  if (view === 'library') {
    return (
      <AppShell
        activeView="library"
        onNavigate={navigate}
        recordingCount={recordingCount}
        indexedCount={indexedCount}
        pageTitle={`Recordings (${recordingCount})`}
        topbarRight={<UsageBadge />}
        guardianAlertCount={0}
      >
        <div className="px-6 py-5">
          <VideoLibrary recordings={recordings} onRecordingSelect={setPreviewRecording} onRefresh={fetchRecordings} />
        </div>
        {previewRecording && (
          <VideoPlayerModal
            recording={previewRecording}
            initialTimestamp={0}
            onClose={() => setPreviewRecording(null)}
          />
        )}
      </AppShell>
    )
  }

  // ── Guardian ───────────────────────────────────────────────────────────────
  if (view === 'guardian') {
    return (
      <AppShell
        activeView="guardian"
        onNavigate={navigate}
        recordingCount={recordingCount}
        indexedCount={indexedCount}
        pageTitle="Guardian"
        topbarRight={<UsageBadge />}
        fullHeight
        guardianAlertCount={0}
      >
        <GuardianPanel recordings={recordings} />
      </AppShell>
    )
  }

  // ── Overview / Home ────────────────────────────────────────────────────────
  const activeAlerts = 0
  const totalIndexed = recordings.filter(r => r.processing_status === 'INDEXED').length

  const recentRecordings = [...recordings]
    .sort((a, b) => {
      const ta = a.upload_timestamp ?? ''
      const tb = b.upload_timestamp ?? ''
      return tb.localeCompare(ta)
    })
    .slice(0, 5)

  const cameraNames = Array.from(
    new Set(recordings.map(r => r.camera_name).filter((c): c is string => Boolean(c)))
  )

  return (
    <AppShell
      activeView="home"
      onNavigate={navigate}
      recordingCount={recordingCount}
      indexedCount={indexedCount}
      pageTitle="Overview"
      topbarRight={<UsageBadge />}
      guardianAlertCount={0}
    >
      <div className="px-6 py-6">
        {/* Page header */}
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-slate-900">Your security overview</h1>
          <p className="text-sm text-slate-500 mt-1">Monitor, investigate, and respond to security events</p>
        </div>

        {/* 2-column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

          {/* LEFT COLUMN — 2/3 width */}
          <div className="lg:col-span-2 space-y-5">

            {/* KPI row */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {/* Total Recordings */}
              <div className="bg-white border border-slate-200 rounded-lg p-4 flex flex-col gap-1">
                <div className="text-slate-400">
                  <svg viewBox="0 0 24 24" fill="none" width="18" height="18" aria-hidden="true">
                    <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </div>
                <p className="text-2xl font-bold text-slate-900">{recordingCount}</p>
                <p className="text-xs text-slate-500 font-medium">Total Recordings</p>
              </div>

              {/* Indexed */}
              <div className="bg-white border border-slate-200 rounded-lg p-4 flex flex-col gap-1">
                <div className="text-green-500">
                  <svg viewBox="0 0 24 24" fill="none" width="18" height="18" aria-hidden="true">
                    <path d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </div>
                <p className="text-2xl font-bold text-green-600">{totalIndexed}</p>
                <p className="text-xs text-slate-500 font-medium">Indexed</p>
              </div>

              {/* Active Alerts */}
              <div className="bg-white border border-slate-200 rounded-lg p-4 flex flex-col gap-1">
                <div className="text-amber-400">
                  <svg viewBox="0 0 24 24" fill="none" width="18" height="18" aria-hidden="true">
                    <path d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </div>
                <p className="text-2xl font-bold text-slate-900">{activeAlerts}</p>
                <p className="text-xs text-slate-500 font-medium">Active Alerts</p>
              </div>

              {/* Investigations */}
              <div className="bg-white border border-slate-200 rounded-lg p-4 flex flex-col gap-1">
                <div className="text-blue-400">
                  <svg viewBox="0 0 24 24" fill="none" width="18" height="18" aria-hidden="true">
                    <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
                    <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                  </svg>
                </div>
                <p className="text-2xl font-bold text-slate-900">0</p>
                <p className="text-xs text-slate-500 font-medium">Investigations</p>
              </div>
            </div>

            {/* Investigation search panel */}
            <div className="rt-search-panel bg-white border border-slate-200 rounded-xl p-5 space-y-4">
              <h2 className="text-base font-semibold text-slate-900">Investigate your footage</h2>

              {indexedCount === 0 && (
                <div className="rt-banner-warning text-xs">
                  No indexed recordings yet.{' '}
                  <button onClick={() => setView('library')} className="underline font-medium hover:opacity-80">
                    Upload videos
                  </button>{' '}
                  to get started.
                </div>
              )}

              {/* Mode toggle */}
              <div className="flex gap-0.5 p-0.5 bg-slate-100 rounded-lg self-start w-fit">
                <button
                  type="button"
                  onClick={() => setMode('standard')}
                  className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all ${
                    mode === 'standard' ? 'bg-white shadow-sm text-slate-900' : 'text-slate-500 hover:text-slate-900'
                  }`}
                >
                  Standard
                </button>
                <button
                  type="button"
                  onClick={() => setMode('multi-agent')}
                  className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all ${
                    mode === 'multi-agent' ? 'bg-white shadow-sm text-slate-900' : 'text-slate-500 hover:text-slate-900'
                  }`}
                >
                  Multi-Agent
                </button>
              </div>

              {mode === 'multi-agent' && (
                <p className="rt-banner-info text-xs">
                  Runs 8 specialist agents: evidence retrieval, visual intelligence, temporal reasoning,
                  cross-camera correlation, contradiction detection, evidence audit, and report synthesis.
                </p>
              )}

              <form onSubmit={handleInvestigate} className="space-y-3">
                {/* Textarea */}
                <textarea
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      if (query.trim() && !loading) handleInvestigate(e as unknown as React.FormEvent)
                    }
                  }}
                  placeholder="Describe what you're looking for... e.g. 'Someone in a red jacket near the entrance around 3pm'"
                  rows={3}
                  className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-200 focus:border-blue-400 transition-colors resize-none"
                  disabled={loading}
                />

                {/* Example query chips */}
                <div className="flex flex-wrap gap-2">
                  {EXAMPLE_QUERIES.map(q => (
                    <button
                      key={q}
                      type="button"
                      onClick={() => setQuery(q)}
                      className="px-3 py-1 rounded-full border border-slate-200 bg-slate-50 text-xs text-slate-600 hover:bg-slate-100 hover:border-slate-300 transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>

                {/* Camera filter */}
                {cameraNames.length > 0 && (
                  <select
                    value={cameraFilter}
                    onChange={e => setCameraFilter(e.target.value)}
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-200 focus:border-blue-400"
                    disabled={loading}
                  >
                    <option value="">All cameras</option>
                    {cameraNames.map(name => (
                      <option key={name} value={name}>{name}</option>
                    ))}
                  </select>
                )}

                {/* Submit button */}
                <button
                  type="submit"
                  disabled={loading || !query.trim() || indexedCount === 0}
                  className="rt-btn-primary w-full py-2.5 justify-center"
                >
                  {loading ? <SpinnerIcon /> : <SearchIconSm />}
                  {loading
                    ? (mode === 'multi-agent' ? 'Running…' : 'Investigating…')
                    : (mode === 'multi-agent' ? 'Investigate with 8 Agents' : 'Investigate')
                  }
                </button>
              </form>

              {error && <div className="rt-banner-danger text-sm">{error}</div>}
            </div>
          </div>

          {/* RIGHT COLUMN — 1/3 width */}
          <div className="space-y-4">

            {/* Recent recordings */}
            <div className="bg-white border border-slate-200 rounded-xl p-4">
              <h2 className="text-sm font-semibold text-slate-900 mb-3">Recent recordings</h2>

              {recentRecordings.length === 0 ? (
                <div className="text-center py-8 text-slate-400">
                  <CameraIcon size={28} />
                  <p className="mt-2 text-xs">No recordings yet</p>
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    {recentRecordings.map(rec => {
                      const badge = getStatusBadge(rec.processing_status ?? '')
                      const ts = formatTimestamp(rec.upload_timestamp)
                      return (
                        <div
                          key={rec.recording_id}
                          className="group rounded-lg border border-slate-100 overflow-hidden hover:border-slate-300 transition-colors cursor-default"
                        >
                          {/* Thumbnail */}
                          <div className="aspect-video bg-slate-100 overflow-hidden">
                            <img
                              src={`/thumbnails/${rec.recording_id}.jpg`}
                              alt={rec.original_filename}
                              className="w-full h-full object-cover"
                              onError={e => {
                                const t = e.currentTarget
                                t.style.display = 'none'
                                const parent = t.parentElement
                                if (parent) {
                                  parent.classList.add('flex', 'items-center', 'justify-center', 'text-slate-300')
                                  parent.innerHTML = '<svg viewBox="0 0 24 24" fill="none" width="20" height="20"><path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>'
                                }
                              }}
                            />
                          </div>
                          <div className="p-2 space-y-0.5">
                            <p className="text-xs font-medium text-slate-800 truncate" title={rec.original_filename}>
                              {rec.original_filename}
                            </p>
                            {rec.camera_name && (
                              <p className="text-xs text-slate-500 truncate">{rec.camera_name}</p>
                            )}
                            {ts && <p className="text-xs text-slate-400">{ts}</p>}
                            <span className={`inline-block text-xs font-medium px-1.5 py-0.5 rounded ${badge.cls}`}>
                              {badge.label}
                            </span>
                          </div>
                        </div>
                      )
                    })}
                  </div>

                  <button
                    onClick={() => navigate('library')}
                    className="mt-3 w-full text-xs text-blue-600 hover:text-blue-700 font-medium text-right transition-colors"
                  >
                    View all recordings →
                  </button>
                </>
              )}
            </div>

            {/* Quick actions */}
            <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-2">
              <h2 className="text-sm font-semibold text-slate-900 mb-3">Quick actions</h2>

              <button
                onClick={() => navigate('library')}
                className="w-full flex items-center gap-2 px-3 py-2.5 rounded-lg border border-slate-200 text-sm text-slate-700 font-medium hover:bg-slate-50 hover:border-slate-300 transition-colors"
              >
                <UploadIconSm />
                Upload recording
              </button>

              <button
                onClick={() => navigate('guardian')}
                className="w-full flex items-center gap-2 px-3 py-2.5 rounded-lg border border-slate-200 text-sm text-slate-700 font-medium hover:bg-slate-50 hover:border-slate-300 transition-colors"
              >
                <ShieldIconSm />
                View Guardian →
              </button>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  )
}
