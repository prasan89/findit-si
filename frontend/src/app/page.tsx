'use client'
import { useState, useEffect, useCallback } from 'react'
import VideoLibrary from '@/components/VideoLibrary'
import UsageBadge from '@/components/UsageBadge'
import RealityTraceWorkspace from '@/components/RealityTraceWorkspace'
import InvestigationVideoList from '@/components/InvestigationVideoList'
import GuardianPanel from '@/components/GuardianPanel'
import type {
  Recording,
  SearchResult,
  InvestigationResult,
  MultiAgentInvestigationResult,
  VideoAnalysisSummary,
} from '@/types'

type AppView = 'home' | 'results' | 'workspace' | 'library' | 'guardian'
type Mode = 'standard' | 'multi-agent'

const EXAMPLE_QUERIES: { label: string }[] = [
  { label: 'Chain snatch or bag theft' },
  { label: 'Assault or physical confrontation' },
  { label: 'Suspicious approach and departure' },
  { label: 'Person entering or exiting' },
  { label: 'Unattended object left behind' },
]

export default function Home() {
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null) // kept for future use
  const [view, setView] = useState<AppView>('home')
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<Mode>('standard')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [investigationResult, setInvestigationResult] = useState<InvestigationResult | null>(null)
  const [multiAgentResult, setMultiAgentResult] = useState<MultiAgentInvestigationResult | null>(null)
  // The specific video the user opened from the results list
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
        // Submit job — returns immediately with job_id
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

        // Kick off standard investigation in parallel (fast, non-blocking)
        const stdPromise = fetch('/api/investigations/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: query.trim() }),
        }).then(r => r.ok ? r.json() : null).catch(() => null)

        // Poll for multi-agent job completion
        const POLL_INTERVAL_MS = 2500
        const POLL_TIMEOUT_MS = 300_000 // 5 min hard cap
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
          if (poll.status === 'completed') {
            maResult = poll.result as MultiAgentInvestigationResult
            break
          }
          if (poll.status === 'failed') {
            setError(poll.error || 'Multi-agent investigation failed')
            setLoading(false)
            return
          }
          // status === 'running': keep polling
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

  // Open a specific video from the results list
  function handleOpenVideo(summary: VideoAnalysisSummary) {
    setActiveVideoSummary(summary)
    setView('workspace')
  }

  // Back from workspace to results list
  function handleBackToResults() {
    setActiveVideoSummary(null)
    setView('results')
  }

  // Investigation results list view
  if (view === 'results' && investigationResult) {
    return (
      <InvestigationVideoList
        result={investigationResult}
        recordings={recordings}
        onOpenVideo={handleOpenVideo}
        onNewInvestigation={() => setView('home')}
      />
    )
  }

  // Video detail workspace view — filtered to the selected recording
  if (view === 'workspace' && investigationResult && activeVideoSummary) {
    // Filter events for this recording only
    const filteredResult = {
      ...investigationResult,
      events: investigationResult.events.filter(
        e => e.recording_id === activeVideoSummary.recording_id
      ),
    }
    return (
      <RealityTraceWorkspace
        recordings={recordings}
        result={filteredResult}
        multiAgentResult={multiAgentResult}
        onNewInvestigation={handleBackToResults}
        activeRecordingId={activeVideoSummary.recording_id}
        videoSummary={activeVideoSummary}
      />
    )
  }

  // ── Shared header for secondary views ─────────────────────────────────────
  const SecondaryHeader = () => (
    <header className="flex items-center justify-between px-6 py-3 bg-white border-b border-[#E2E8F0] shadow-sm">
      <div className="flex items-center gap-3">
        <button
          onClick={() => setView('home')}
          className="rt-btn-ghost text-sm gap-1.5"
        >
          <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
            <path d="M19 12H5M5 12l7-7M5 12l7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          Investigate
        </button>
        <div className="w-px h-5 bg-[#E2E8F0]" />
        <nav className="flex gap-1">
          {(['library', 'guardian'] as AppView[]).map(v => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize transition-colors ${
                view === v
                  ? 'bg-[#EFF6FF] text-[#2563EB]'
                  : 'text-[#64748B] hover:text-[#0F172A] hover:bg-[#F1F5F9]'
              }`}
            >
              {v === 'library' ? `Cameras (${recordings.length})` : v.charAt(0).toUpperCase() + v.slice(1)}
            </button>
          ))}
        </nav>
      </div>
      <UsageBadge />
    </header>
  )

  if (view === 'library') {
    return (
      <div className="min-h-screen bg-[#F8FAFC]">
        <SecondaryHeader />
        <div className="max-w-5xl mx-auto px-6 py-6">
          <VideoLibrary recordings={recordings} onUploadComplete={fetchRecordings} onDelete={fetchRecordings} />
        </div>
      </div>
    )
  }

  if (view === 'guardian') {
    return (
      <div className="flex flex-col" style={{ height: '100dvh' }}>
        <SecondaryHeader />
        <div className="flex-1 min-h-0 overflow-hidden">
          <GuardianPanel recordings={recordings} />
        </div>
      </div>
    )
  }

  // ── Home / investigation form ──────────────────────────────────────────────
  return (
    <div className="min-h-screen flex flex-col bg-[#F8FAFC]">

      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 bg-white border-b border-[#E2E8F0] shadow-sm">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-[#2563EB] flex items-center justify-center">
            <svg viewBox="0 0 24 24" fill="none" style={{ width: 18, height: 18 }}>
              <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <span className="font-bold text-[#0F172A] text-base tracking-tight">RealityTrace</span>
          <span className="text-xs text-[#94A3B8] font-medium hidden sm:block">CCTV Investigation</span>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => setView('library')} className="rt-btn-ghost text-sm">
            Cameras ({recordings.length})
          </button>
          <button onClick={() => setView('guardian')} className="rt-btn-ghost text-sm">Guardian</button>
          <div className="w-px h-5 bg-[#E2E8F0] mx-1" />
          <UsageBadge />
        </div>
      </header>

      {/* Hero + form */}
      <div className="flex-1 flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-lg space-y-6">

          {/* Hero text */}
          <div className="text-center space-y-2">
            <div className="mx-auto w-14 h-14 rounded-2xl bg-[#2563EB] flex items-center justify-center shadow-md">
              <svg viewBox="0 0 24 24" fill="none" style={{ width: 28, height: 28 }}>
                <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
            <h1 className="text-3xl font-bold text-[#0F172A] tracking-tight">RealityTrace</h1>
            <p className="text-[#64748B] text-sm">Evidence-backed CCTV investigation workspace</p>
          </div>

          {/* Status row */}
          <div className="flex items-center justify-center gap-3 text-xs">
            <div className="flex items-center gap-1.5 bg-white px-3 py-1.5 rounded-full border border-[#E2E8F0] shadow-sm">
              <span className={`w-2 h-2 rounded-full ${indexedCount > 0 ? 'bg-[#16A34A]' : 'bg-[#94A3B8]'}`} />
              <span className="text-[#64748B] font-medium">{indexedCount} camera{indexedCount !== 1 ? 's' : ''} indexed</span>
            </div>
            <div className="flex items-center gap-1.5 bg-white px-3 py-1.5 rounded-full border border-[#E2E8F0] shadow-sm">
              <span className="w-2 h-2 rounded-full bg-[#94A3B8]" />
              <span className="text-[#94A3B8]">{recordings.length} recordings</span>
            </div>
          </div>

          {/* Investigation card */}
          <div className="rt-card p-6 shadow-md space-y-4">

            {/* Mode toggle */}
            <div className="flex gap-1 p-1 bg-[#F1F5F9] rounded-xl">
              <button
                type="button"
                onClick={() => setMode('standard')}
                className={`flex-1 py-2 rounded-lg text-sm font-semibold transition-all ${
                  mode === 'standard'
                    ? 'bg-white shadow-sm text-[#0F172A]'
                    : 'text-[#64748B] hover:text-[#0F172A]'
                }`}
              >
                Standard
              </button>
              <button
                type="button"
                onClick={() => setMode('multi-agent')}
                className={`flex-1 py-2 rounded-lg text-sm font-semibold transition-all ${
                  mode === 'multi-agent'
                    ? 'bg-white shadow-sm text-[#0F172A]'
                    : 'text-[#64748B] hover:text-[#0F172A]'
                }`}
              >
                Multi-Agent
                <span className="ml-1.5 text-[11px] font-normal text-[#94A3B8]">8 agents</span>
              </button>
            </div>

            {mode === 'multi-agent' && (
              <p className="rt-banner-info text-xs">
                Runs 8 specialist agents: evidence retrieval, visual intelligence, temporal reasoning,
                cross-camera correlation, contradiction detection, evidence audit, and report synthesis.
              </p>
            )}

            {indexedCount === 0 && (
              <div className="rt-banner-warning text-xs">
                No indexed recordings yet.{' '}
                <button onClick={() => setView('library')} className="underline font-medium hover:opacity-80">
                  Upload videos
                </button>{' '}
                to get started.
              </div>
            )}

            {/* Query input */}
            <form onSubmit={handleInvestigate} className="space-y-3">
              <div className="relative">
                <svg viewBox="0 0 24 24" fill="none" className="absolute left-3.5 top-3.5 w-4 h-4 text-[#94A3B8] pointer-events-none">
                  <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
                  <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
                <textarea
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                      e.preventDefault()
                      if (query.trim() && !loading) handleInvestigate(e as unknown as React.FormEvent)
                    }
                  }}
                  placeholder={'Describe the incident to investigate...\ne.g. Chain snatch near the entrance around midday'}
                  rows={3}
                  className="w-full rounded-lg border border-[#E2E8F0] bg-white pl-10 pr-4 py-3 text-sm text-[#0F172A] placeholder-[#94A3B8] focus:outline-none focus:ring-2 focus:ring-[#93C5FD] focus:border-[#2563EB] resize-none transition-colors"
                  disabled={loading}
                />
              </div>
              <button
                type="submit"
                disabled={loading || !query.trim() || indexedCount === 0}
                className="rt-btn-primary w-full py-2.5 justify-center"
              >
                <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
                  <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
                  <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
                {loading
                  ? (mode === 'multi-agent' ? 'Agents running… polling for results' : 'Investigating…')
                  : (mode === 'multi-agent' ? 'Investigate with 8 Agents' : 'Investigate')
                }
              </button>
            </form>

            {error && <div className="rt-banner-danger text-sm">{error}</div>}
          </div>

          {/* Quick query chips */}
          <div className="space-y-2">
            <p className="text-[11px] text-[#94A3B8] text-center font-semibold uppercase tracking-widest">
              Quick Queries
            </p>
            <div className="flex flex-wrap gap-2 justify-center">
              {EXAMPLE_QUERIES.map(({ label }) => (
                <button
                  key={label}
                  onClick={() => setQuery(label)}
                  className="text-xs px-3 py-1.5 rounded-lg bg-white border border-[#E2E8F0] text-[#64748B] font-medium hover:bg-[#EFF6FF] hover:text-[#2563EB] hover:border-[#BFDBFE] transition-colors shadow-sm"
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
