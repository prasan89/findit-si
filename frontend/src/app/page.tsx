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

type AppView = 'home' | 'results' | 'workspace' | 'library' | 'guardian' | 'investigate'
type Mode = 'standard' | 'multi-agent'

const EXAMPLE_QUERIES: string[] = [
  'Chain snatch or bag theft',
  'Assault or physical confrontation',
  'Suspicious approach and departure',
  'Person entering or exiting',
  'Unattended object left behind',
]

const PAGE_TITLES: Record<string, string> = {
  home:        'Overview',
  library:     'Recordings',
  investigate: 'Investigate',
  results:     'Investigation Results',
  workspace:   'Evidence Workspace',
  guardian:    'Guardian',
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
    if (v === 'investigate') setView('investigate')
    else setView(v as AppView)
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
            onNewInvestigation={() => setView('investigate')}
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
        <div className="max-w-4xl mx-auto px-6 py-6">
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

  // ── Investigate form ───────────────────────────────────────────────────────
  if (view === 'investigate') {
    return (
      <AppShell
        activeView="investigate"
        onNavigate={navigate}
        recordingCount={recordings.length}
        indexedCount={indexedCount}
        pageTitle="Investigate"
        topbarRight={<UsageBadge />}
      >
        <div className="flex items-center justify-center min-h-full px-4 py-12">
          <div className="w-full max-w-lg space-y-5">

            {/* Status chips */}
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
                {EXAMPLE_QUERIES.map(label => (
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
      </AppShell>
    )
  }

  // ── Overview / Home ────────────────────────────────────────────────────────
  const processingCount = recordings.filter(r => r.processing_status === 'PROCESSING').length
  const recentRecordings = [...recordings]
    .sort((a, b) => new Date(b.upload_timestamp).getTime() - new Date(a.upload_timestamp).getTime())
    .slice(0, 4)

  return (
    <AppShell
      activeView="home"
      onNavigate={navigate}
      recordingCount={recordings.length}
      indexedCount={indexedCount}
      pageTitle="Overview"
      topbarRight={<UsageBadge />}
    >
      <div className="max-w-4xl mx-auto px-6 py-8 space-y-8">

        {/* Welcome / CTA */}
        <div className="rt-card p-8 flex flex-col sm:flex-row items-start sm:items-center gap-6">
          <div className="w-14 h-14 rounded-2xl bg-[#2563EB] flex items-center justify-center shadow-md shrink-0">
            <svg viewBox="0 0 24 24" fill="none" style={{ width: 28, height: 28 }}>
              <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <div className="flex-1">
            <h2 className="text-xl font-bold text-[#0F172A] tracking-tight">Evidence-backed CCTV Investigation</h2>
            <p className="text-sm text-[#64748B] mt-1">
              Upload CCTV recordings, then describe incidents in plain language. RealityTrace retrieves
              evidence, maps timelines, and surfaces corroborated facts vs inferences.
            </p>
          </div>
          <div className="flex gap-2 shrink-0">
            <button onClick={() => setView('library')} className="rt-btn-secondary text-sm">
              Upload Recording
            </button>
            <button
              onClick={() => setView('investigate')}
              disabled={indexedCount === 0}
              className="rt-btn-primary text-sm"
            >
              Investigate
            </button>
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[
            { label: 'Total Recordings', value: recordings.length, color: 'text-[#0F172A]' },
            { label: 'Indexed', value: indexedCount, color: 'text-[#16A34A]' },
            { label: 'Processing', value: processingCount, color: processingCount > 0 ? 'text-[#2563EB]' : 'text-[#94A3B8]' },
            { label: 'Cameras', value: new Set(recordings.map(r => r.camera_name).filter(Boolean)).size, color: 'text-[#0F172A]' },
          ].map(({ label, value, color }) => (
            <div key={label} className="rt-card p-4">
              <p className="text-xs text-[#64748B] font-medium mb-1">{label}</p>
              <p className={`text-2xl font-bold ${color}`}>{value}</p>
            </div>
          ))}
        </div>

        {/* Recent recordings */}
        {recentRecordings.length > 0 && (
          <div>
            <div className="flex items-center justify-between mb-3">
              <h3 className="rt-section-title">Recent Recordings</h3>
              <button onClick={() => setView('library')} className="text-xs text-[#2563EB] hover:text-[#1D4ED8] font-medium transition-colors">
                View all →
              </button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {recentRecordings.map(r => {
                const isIndexed = r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL'
                return (
                  <div key={r.recording_id} className="rt-card-hover p-4 flex gap-3">
                    <div className="w-16 h-11 rounded-lg bg-[#F1F5F9] flex items-center justify-center shrink-0 overflow-hidden">
                      {r.thumbnail_path ? (
                        <img src={`/thumbnails/${r.recording_id}.jpg`} alt="" className="w-full h-full object-cover" />
                      ) : (
                        <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5 text-[#94A3B8]">
                          <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5"/>
                        </svg>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-[#0F172A] truncate">{r.camera_name ?? r.original_filename}</p>
                      <p className="text-xs text-[#64748B] truncate">{r.camera_location ?? r.original_filename}</p>
                      <div className="flex items-center gap-1.5 mt-1">
                        <span className={`w-1.5 h-1.5 rounded-full ${isIndexed ? 'bg-[#16A34A]' : 'bg-[#94A3B8]'}`} />
                        <span className="text-[10px] text-[#94A3B8] font-medium">{r.processing_status}</span>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Empty state */}
        {recordings.length === 0 && (
          <div className="rt-card rt-empty py-20">
            <svg viewBox="0 0 24 24" fill="none" className="w-12 h-12 text-[#E2E8F0] mb-4">
              <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
            </svg>
            <p className="text-sm font-medium text-[#64748B]">No recordings yet</p>
            <p className="text-xs text-[#94A3B8] mt-1 mb-4">Upload a CCTV recording to get started</p>
            <button onClick={() => setView('library')} className="rt-btn-primary">
              Upload Recording
            </button>
          </div>
        )}

        {/* Quick actions (investigate shortcuts) */}
        {indexedCount > 0 && (
          <div>
            <h3 className="rt-section-title mb-3">Quick Investigate</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {EXAMPLE_QUERIES.map(label => (
                <button
                  key={label}
                  onClick={() => { setQuery(label); setView('investigate') }}
                  className="rt-card-hover p-3 flex items-center gap-3 text-left"
                >
                  <div className="w-7 h-7 rounded-lg bg-[#EFF6FF] flex items-center justify-center shrink-0">
                    <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4 text-[#2563EB]">
                      <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
                      <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                    </svg>
                  </div>
                  <span className="text-sm text-[#0F172A] font-medium">{label}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  )
}
