'use client'
import { useState, useEffect, useCallback } from 'react'
import AppShell from '@/components/AppShell'
import VideoLibrary from '@/components/VideoLibrary'
import UsageBadge from '@/components/UsageBadge'
import RealityTraceWorkspace from '@/components/RealityTraceWorkspace'
import InvestigationVideoList from '@/components/InvestigationVideoList'
import GuardianPanel from '@/components/GuardianPanel'
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

export default function Home() {
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [view, setView] = useState<AppView>('home')
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<Mode>('standard')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [investigationResult, setInvestigationResult] = useState<InvestigationResult | null>(null)
  const [multiAgentResult, setMultiAgentResult] = useState<MultiAgentInvestigationResult | null>(null)
  const [activeVideoSummary, setActiveVideoSummary] = useState<VideoAnalysisSummary | null>(null)

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
    // Clicking "Investigate" goes to home where the form lives
    setView(v === 'investigate' ? 'home' : v as AppView)
  }

  const pageTitle = PAGE_TITLES[view] ?? 'RealityTrace'

  // ── Investigation results list ─────────────────────────────────────────────
  if (view === 'results' && investigationResult) {
    return (
      <AppShell
        activeView="investigate"
        onNavigate={navigate}
        recordingCount={recordings.length}
        indexedCount={indexedCount}
        pageTitle="Investigation Results"
        topbarRight={<UsageBadge />}
      >
        <div className="h-full overflow-y-auto">
          <InvestigationVideoList
            result={investigationResult}
            recordings={recordings}
            onOpenVideo={handleOpenVideo}
            onNewInvestigation={() => setView('home')}
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
        recordingCount={recordings.length}
        indexedCount={indexedCount}
        pageTitle="Evidence Workspace"
        topbarRight={<UsageBadge />}
        fullHeight
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
        recordingCount={recordings.length}
        indexedCount={indexedCount}
        pageTitle={`Recordings (${recordings.length})`}
        topbarRight={<UsageBadge />}
      >
        <div className="px-6 py-5">
          <VideoLibrary recordings={recordings} onUploadComplete={fetchRecordings} onDelete={fetchRecordings} />
        </div>
      </AppShell>
    )
  }

  // ── Guardian ───────────────────────────────────────────────────────────────
  if (view === 'guardian') {
    return (
      <AppShell
        activeView="guardian"
        onNavigate={navigate}
        recordingCount={recordings.length}
        indexedCount={indexedCount}
        pageTitle="Guardian"
        topbarRight={<UsageBadge />}
        fullHeight
      >
        <GuardianPanel recordings={recordings} />
      </AppShell>
    )
  }

  // ── Overview / Home (with inline investigate form) ────────────────────────
  const processingCount = recordings.filter(r => r.processing_status === 'PROCESSING').length
  const recentRecordings = [...recordings]
    .sort((a, b) => new Date(b.upload_timestamp).getTime() - new Date(a.upload_timestamp).getTime())
    .slice(0, 6)

  return (
    <AppShell
      activeView="home"
      onNavigate={navigate}
      recordingCount={recordings.length}
      indexedCount={indexedCount}
      pageTitle="Overview"
      topbarRight={<UsageBadge />}
    >
      <div className="px-6 py-5 space-y-5">

        {/* Stats row — full width, 4 cols */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[
            { label: 'Total Recordings', value: recordings.length, color: 'text-[#0F172A]' },
            { label: 'Indexed', value: indexedCount, color: 'text-[#16A34A]' },
            { label: 'Processing', value: processingCount, color: processingCount > 0 ? 'text-[#2563EB]' : 'text-[#94A3B8]' },
            { label: 'Cameras', value: new Set(recordings.map(r => r.camera_name).filter(Boolean)).size, color: 'text-[#0F172A]' },
          ].map(({ label, value, color }) => (
            <div key={label} className="rt-card px-4 py-3">
              <p className="text-xs text-[#64748B] font-medium">{label}</p>
              <p className={`text-2xl font-bold mt-0.5 ${color}`}>{value}</p>
            </div>
          ))}
        </div>

        {/* Investigate form — compact single row */}
        <div className="rt-card px-4 py-3">
            {indexedCount === 0 && (
              <div className="rt-banner-warning text-xs mb-3">
                No indexed recordings yet.{' '}
                <button onClick={() => setView('library')} className="underline font-medium hover:opacity-80">Upload videos</button>{' '}
                to get started.
              </div>
            )}
            {mode === 'multi-agent' && (
              <p className="rt-banner-info text-xs mb-3">
                Runs 8 specialist agents: evidence retrieval, visual intelligence, temporal reasoning,
                cross-camera correlation, contradiction detection, evidence audit, and report synthesis.
              </p>
            )}
            <form onSubmit={handleInvestigate} className="flex items-center gap-3">
              {/* Mode toggle */}
              <div className="flex gap-0.5 p-0.5 bg-[#F1F5F9] rounded-lg shrink-0">
                <button
                  type="button"
                  onClick={() => setMode('standard')}
                  className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${
                    mode === 'standard' ? 'bg-white shadow-sm text-[#0F172A]' : 'text-[#64748B] hover:text-[#0F172A]'
                  }`}
                >
                  Standard
                </button>
                <button
                  type="button"
                  onClick={() => setMode('multi-agent')}
                  className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${
                    mode === 'multi-agent' ? 'bg-white shadow-sm text-[#0F172A]' : 'text-[#64748B] hover:text-[#0F172A]'
                  }`}
                >
                  Multi-Agent
                </button>
              </div>

              {/* Input */}
              <div className="relative flex-1">
                <svg viewBox="0 0 24 24" fill="none" width="15" height="15" className="absolute left-3 top-1/2 -translate-y-1/2 text-[#94A3B8] pointer-events-none">
                  <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
                  <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
                <input
                  type="text"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="Describe the incident to investigate…"
                  className="w-full rounded-lg border border-[#E2E8F0] bg-white pl-9 pr-4 py-2 text-sm text-[#0F172A] placeholder-[#94A3B8] focus:outline-none focus:ring-2 focus:ring-[#93C5FD] focus:border-[#2563EB] transition-colors"
                  disabled={loading}
                />
              </div>

              {/* Submit */}
              <button
                type="submit"
                disabled={loading || !query.trim() || indexedCount === 0}
                className="rt-btn-primary shrink-0"
              >
                <svg viewBox="0 0 24 24" fill="none" width="15" height="15">
                  <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
                  <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
                {loading
                  ? (mode === 'multi-agent' ? 'Running…' : 'Investigating…')
                  : (mode === 'multi-agent' ? 'Investigate (8 Agents)' : 'Investigate')
                }
              </button>
            </form>
            {error && <div className="rt-banner-danger text-sm mt-3">{error}</div>}
        </div>

        {/* Recent recordings */}
        <div>
            <div className="flex items-center justify-between mb-3">
              <h3 className="rt-section-title">Recent Recordings</h3>
              <button onClick={() => setView('library')} className="text-xs text-[#2563EB] hover:text-[#1D4ED8] font-medium transition-colors">
                View all →
              </button>
            </div>

            {recentRecordings.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                {recentRecordings.map(r => {
                  const isIndexed = r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL'
                  return (
                    <div key={r.recording_id} className="rt-card-hover p-3 flex gap-3">
                      <div className="w-16 h-11 rounded-md bg-[#F1F5F9] flex items-center justify-center shrink-0 overflow-hidden">
                        {r.thumbnail_path ? (
                          <img src={`/thumbnails/${r.recording_id}.jpg`} alt="" className="w-full h-full object-cover" />
                        ) : (
                          <svg viewBox="0 0 24 24" fill="none" width="18" height="18" className="text-[#94A3B8]">
                            <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5"/>
                          </svg>
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-[#0F172A] truncate">{r.camera_name ?? r.original_filename}</p>
                        <p className="text-xs text-[#64748B] truncate">{r.camera_location ?? r.original_filename}</p>
                        <div className="flex items-center gap-1.5 mt-1">
                          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isIndexed ? 'bg-[#16A34A]' : 'bg-[#94A3B8]'}`} />
                          <span className="text-[10px] text-[#94A3B8] font-medium uppercase tracking-wide">{r.processing_status}</span>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="rt-card rt-empty py-12">
                <svg viewBox="0 0 24 24" fill="none" width="40" height="40" className="text-[#E2E8F0] mb-3">
                  <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
                </svg>
                <p className="text-sm font-medium text-[#64748B]">No recordings yet</p>
                <p className="text-xs text-[#94A3B8] mt-1 mb-3">Upload a CCTV recording to get started</p>
                <button onClick={() => setView('library')} className="rt-btn-primary">Upload Recording</button>
              </div>
            )}
          </div>
      </div>
    </AppShell>
  )
}
