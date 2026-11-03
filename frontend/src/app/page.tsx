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

        {/* Investigate form — centered card */}
        <div className="flex justify-center">
        <div className="rt-card px-6 py-5 w-full max-w-2xl space-y-3">
            {indexedCount === 0 && (
              <div className="rt-banner-warning text-xs">
                No indexed recordings yet.{' '}
                <button onClick={() => setView('library')} className="underline font-medium hover:opacity-80">Upload videos</button>{' '}
                to get started.
              </div>
            )}
            {mode === 'multi-agent' && (
              <p className="rt-banner-info text-xs">
                Runs 8 specialist agents: evidence retrieval, visual intelligence, temporal reasoning,
                cross-camera correlation, contradiction detection, evidence audit, and report synthesis.
              </p>
            )}

            {/* Mode toggle */}
            <div className="flex gap-0.5 p-0.5 bg-[#F1F5F9] rounded-lg self-start w-fit">
              <button
                type="button"
                onClick={() => setMode('standard')}
                className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all ${
                  mode === 'standard' ? 'bg-white shadow-sm text-[#0F172A]' : 'text-[#64748B] hover:text-[#0F172A]'
                }`}
              >
                Standard
              </button>
              <button
                type="button"
                onClick={() => setMode('multi-agent')}
                className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all ${
                  mode === 'multi-agent' ? 'bg-white shadow-sm text-[#0F172A]' : 'text-[#64748B] hover:text-[#0F172A]'
                }`}
              >
                Multi-Agent
              </button>
            </div>

            <form onSubmit={handleInvestigate} className="space-y-2">
              {/* Input */}
              <div className="relative">
                <svg viewBox="0 0 24 24" fill="none" width="16" height="16" className="absolute left-3.5 top-1/2 -translate-y-1/2 text-[#94A3B8] pointer-events-none">
                  <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
                  <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
                <input
                  type="text"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      if (query.trim() && !loading) handleInvestigate(e as unknown as React.FormEvent)
                    }
                  }}
                  placeholder="Describe the incident to investigate…"
                  className="w-full rounded-xl border border-[#E2E8F0] bg-white pl-10 pr-4 py-3 text-sm text-[#0F172A] placeholder-[#94A3B8] focus:outline-none focus:ring-2 focus:ring-[#93C5FD] focus:border-[#2563EB] transition-colors"
                  disabled={loading}
                />
              </div>
              <button
                type="submit"
                disabled={loading || !query.trim() || indexedCount === 0}
                className="rt-btn-primary w-full py-2.5 justify-center"
              >
                <svg viewBox="0 0 24 24" fill="none" width="15" height="15">
                  <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
                  <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
                {loading
                  ? (mode === 'multi-agent' ? 'Running…' : 'Investigating…')
                  : (mode === 'multi-agent' ? 'Investigate with 8 Agents' : 'Investigate')
                }
              </button>
            </form>
            {error && <div className="rt-banner-danger text-sm">{error}</div>}
        </div>
        </div>
      </div>
    </AppShell>
  )
}
