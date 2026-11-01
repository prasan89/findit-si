'use client'
import { useState } from 'react'
import EventCard from './EventCard'
import AgentStatusPanel from './AgentStatusPanel'
import VideoPlayerModal from './VideoPlayerModal'
import type { Recording, InvestigationResult, MultiAgentInvestigationResult } from '@/types'

type Mode = 'standard' | 'multi-agent'

interface Props {
  recordings: Recording[]
  investigationResult: InvestigationResult | null
  onInvestigate: (result: InvestigationResult) => void
}

const EXAMPLE_QUERIES = [
  'What happened before the person entered the building?',
  'Find when the red bag was left near the door',
  'Show events between 00:10 and 01:00',
  'What happened before and after the object disappeared?',
]

export default function InvestigationPanel({ recordings, investigationResult, onInvestigate }: Props) {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('standard')
  const [multiAgentResult, setMultiAgentResult] = useState<MultiAgentInvestigationResult | null>(null)
  const [videoModal, setVideoModal] = useState<{ recordingId: string; timestamp: number } | null>(null)

  const videoModalRecording = videoModal
    ? recordings.find(r => r.recording_id === videoModal.recordingId) ?? null
    : null

  const indexedCount = recordings.filter(r =>
    r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL'
  ).length

  const handleInvestigate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    setMultiAgentResult(null)
    try {
      if (mode === 'multi-agent') {
        const res = await fetch('/api/investigations/multi-agent', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: query.trim() }),
        })
        if (!res.ok) {
          const err = await res.json()
          setError(err.detail || 'Multi-agent investigation failed')
        } else {
          setMultiAgentResult(await res.json())
        }
      } else {
        const res = await fetch('/api/investigations/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: query.trim() }),
        })
        if (!res.ok) {
          const err = await res.json()
          setError(err.detail || 'Investigation failed')
        } else {
          onInvestigate(await res.json())
        }
      }
    } catch {
      setError('Network error. Is the backend running?')
    }
    setLoading(false)
  }

  return (
    <div className="space-y-5">
      <div className="rt-card p-6 space-y-4">
        <div>
          <h2 className="rt-section-title">Investigate an Incident</h2>
          <p className="rt-section-subtitle">
            {indexedCount === 0
              ? 'No indexed recordings yet — upload videos first.'
              : `Investigating across ${indexedCount} indexed recording${indexedCount === 1 ? '' : 's'}.`}
          </p>
        </div>
        <div className="flex gap-1 p-1 bg-[#F1F5F9] rounded-lg w-fit">
          {(['standard', 'multi-agent'] as Mode[]).map(m => (
            <button key={m} type="button" onClick={() => setMode(m)}
              className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
                mode === m ? 'bg-white text-[#0F172A] shadow-sm' : 'text-[#64748B] hover:text-[#0F172A]'
              }`}>
              {m === 'standard' ? 'Standard' : 'Multi-Agent'}
              {m === 'multi-agent' && <span className="ml-1.5 text-[10px] text-[#94A3B8]">8 specialists</span>}
            </button>
          ))}
        </div>
        {mode === 'multi-agent' && (
          <div className="rt-banner-info text-xs">
            Runs 8 specialist agents: evidence retrieval, visual intelligence, temporal reasoning,
            cross-camera correlation, contradiction detection, evidence audit, and report synthesis.
          </div>
        )}
        <form onSubmit={handleInvestigate} className="space-y-3">
          <div className="flex gap-2">
            <input type="text" value={query} onChange={e => setQuery(e.target.value)}
              placeholder="e.g. What happened before the person left the building?"
              className="rt-input flex-1" disabled={loading} />
            <button type="submit" disabled={loading || !query.trim()} className="rt-btn-primary whitespace-nowrap">
              {loading ? (mode === 'multi-agent' ? 'Running…' : 'Investigating…')
                : (mode === 'multi-agent' ? 'Run 8 Agents' : 'Investigate')}
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLE_QUERIES.map(q => (
              <button key={q} type="button" onClick={() => setQuery(q)}
                className="text-xs px-3 py-1.5 rounded-full bg-[#F1F5F9] text-[#64748B] hover:bg-[#EFF6FF] hover:text-[#2563EB] border border-[#E2E8F0] transition-colors">
                {q}
              </button>
            ))}
          </div>
        </form>
        {error && <div className="rt-banner-danger">{error}</div>}
      </div>

      {multiAgentResult && (
        <div className="space-y-4">
          <div>
            <h3 className="rt-section-title">Multi-Agent: <span className="text-[#2563EB]">&ldquo;{multiAgentResult.query}&rdquo;</span></h3>
            <p className="rt-section-subtitle">{multiAgentResult.agents.filter(a => a.status === 'completed').length}/{multiAgentResult.agents.length} agents · ${multiAgentResult.total_cost_usd.toFixed(5)} · {(multiAgentResult.total_duration_ms / 1000).toFixed(2)}s</p>
          </div>
          {multiAgentResult.final_report && (
            <div className="rt-card p-4">
              <p className="text-xs font-semibold text-[#64748B] uppercase tracking-wide mb-2">Investigation Narrative</p>
              <p className="text-sm text-[#0F172A] whitespace-pre-wrap leading-relaxed">{multiAgentResult.final_report.narrative}</p>
            </div>
          )}
          <AgentStatusPanel result={multiAgentResult} />
        </div>
      )}

      {investigationResult && mode === 'standard' && (
        <div className="space-y-4">
          <div>
            <h3 className="rt-section-title">Investigation: <span className="text-[#2563EB]">&ldquo;{investigationResult.query}&rdquo;</span></h3>
            <p className="rt-section-subtitle">{investigationResult.total_events_found} event(s) across {investigationResult.searched_recordings} recording(s)</p>
          </div>
          <div className={investigationResult.timestamp_ordering_reliable ? 'rt-banner-success' : 'rt-banner-warning'}>
            <span className="font-semibold">{investigationResult.timestamp_ordering_reliable ? '✓ Reliable timeline' : '⚠ Approximate timeline'}</span>
            {' — '}{investigationResult.ordering_explanation}
          </div>
          {(investigationResult.summary || investigationResult.narrative) && (
            <div className="rt-card p-4 space-y-2">
              <p className="text-xs font-semibold text-[#64748B] uppercase tracking-wide">Incident Summary</p>
              {investigationResult.summary && <p className="text-sm font-medium text-[#0F172A]">{investigationResult.summary}</p>}
              {investigationResult.narrative && (
                <details>
                  <summary className="text-xs text-[#64748B] cursor-pointer hover:text-[#0F172A]">Full evidence narrative</summary>
                  <p className="text-sm text-[#64748B] whitespace-pre-wrap mt-2 leading-relaxed">{investigationResult.narrative}</p>
                </details>
              )}
            </div>
          )}
          {investigationResult.events.length > 0 ? (
            <div className="space-y-3">
              <p className="text-xs font-semibold text-[#64748B] uppercase tracking-wide">Event Timeline</p>
              {investigationResult.events.map((ev, i) => (
                <EventCard key={ev.event_id} event={ev} index={i + 1} highlight={i === 0}
                  onPlayEvidence={(rid, ts) => setVideoModal({ recordingId: rid, timestamp: ts })} />
              ))}
            </div>
          ) : (
            <div className="rt-card rt-empty py-12">
              <p className="text-sm font-medium text-[#64748B]">No events found</p>
              <p className="text-xs text-[#94A3B8] mt-0.5">&ldquo;{investigationResult.query}&rdquo;</p>
            </div>
          )}
          {investigationResult.evidence_gaps.length > 0 && (
            <div className="rt-banner-warning">
              <p className="font-semibold mb-1">Evidence Gaps</p>
              <ul className="space-y-1">{investigationResult.evidence_gaps.map((g, i) => <li key={i} className="text-xs">· {g}</li>)}</ul>
            </div>
          )}
          {investigationResult.uncertainty_summary && (
            <div className="rt-card p-4">
              <p className="text-xs font-semibold text-[#64748B] uppercase tracking-wide mb-1">Uncertainty Notes</p>
              <p className="text-sm text-[#64748B]">{investigationResult.uncertainty_summary}</p>
            </div>
          )}
        </div>
      )}
      {videoModalRecording && videoModal && (
        <VideoPlayerModal recording={videoModalRecording} initialTimestamp={videoModal.timestamp} onClose={() => setVideoModal(null)} />
      )}
    </div>
  )
}
