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

  const indexedRecordings = recordings.filter(r =>
    r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL'
  )

  const EXAMPLE_QUERIES = [
    'What happened before the person entered the building?',
    'Find when the red bag was left near the door',
    'Show events between 00:10 and 01:00',
    'What happened before and after the object disappeared?',
  ]

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
    <div className="space-y-6">
      {/* Query input */}
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h2 className="text-lg font-semibold mb-1">Investigate an Incident</h2>
        <p className="text-sm text-gray-500 mb-4">
          {indexedRecordings.length === 0
            ? 'No indexed recordings yet — upload videos first.'
            : `Investigating across ${indexedRecordings.length} indexed recording${indexedRecordings.length === 1 ? '' : 's'}.`}
        </p>

        {/* Mode selector */}
        <div className="flex gap-2 mb-4">
          <button
            type="button"
            onClick={() => setMode('standard')}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              mode === 'standard'
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            Standard
          </button>
          <button
            type="button"
            onClick={() => setMode('multi-agent')}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              mode === 'multi-agent'
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            Multi-Agent (8 specialists)
          </button>
        </div>

        {mode === 'multi-agent' && (
          <p className="text-xs text-indigo-700 bg-indigo-50 rounded-lg px-3 py-2 mb-3">
            Runs 8 specialist agents: evidence retrieval, visual intelligence, object analysis,
            temporal reasoning, cross-camera correlation, contradiction hunting, evidence audit,
            and report synthesis.
          </p>
        )}

        <form onSubmit={handleInvestigate} className="flex gap-2 mb-3">
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="e.g. What happened before the person left the building?"
            className="flex-1 rounded-lg border border-gray-300 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="px-5 py-2 bg-indigo-600 text-white rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors whitespace-nowrap"
          >
            {loading ? 'Investigating…' : mode === 'multi-agent' ? 'Investigate (8 agents)' : 'Investigate'}
          </button>
        </form>

        {/* Example queries */}
        <div className="flex flex-wrap gap-2">
          {EXAMPLE_QUERIES.map(q => (
            <button
              key={q}
              onClick={() => setQuery(q)}
              className="text-xs px-3 py-1.5 rounded-full bg-gray-100 text-gray-600 hover:bg-indigo-50 hover:text-indigo-700 transition-colors"
            >
              {q}
            </button>
          ))}
        </div>

        {error && <p className="text-sm text-red-600 mt-3">{error}</p>}
      </div>

      {/* Multi-agent results */}
      {multiAgentResult && (
        <div className="space-y-4">
          <div>
            <h3 className="font-semibold text-gray-900">
              Multi-Agent Investigation:{' '}
              <span className="text-indigo-700">&ldquo;{multiAgentResult.query}&rdquo;</span>
            </h3>
            <p className="text-sm text-gray-500">
              {multiAgentResult.agents.filter(a => a.status === 'completed').length} of{' '}
              {multiAgentResult.agents.length} agents completed
              {' · '}${multiAgentResult.total_cost_usd.toFixed(5)}
              {' · '}{(multiAgentResult.total_duration_ms / 1000).toFixed(2)}s
            </p>
          </div>

          {multiAgentResult.final_report && (
            <div className="bg-gray-50 border border-gray-200 rounded-xl p-4">
              <h4 className="text-sm font-semibold text-gray-700 mb-2">Investigation Narrative</h4>
              <pre className="text-sm text-gray-700 whitespace-pre-wrap font-sans">
                {multiAgentResult.final_report.narrative}
              </pre>
            </div>
          )}

          <AgentStatusPanel result={multiAgentResult} />
        </div>
      )}

      {/* Standard results */}
      {investigationResult && mode === 'standard' && (
        <div className="space-y-4">
          {/* Summary bar */}
          <div className="flex items-start justify-between gap-2 flex-wrap">
            <div>
              <h3 className="font-semibold text-gray-900">
                Investigation:{' '}
                <span className="text-indigo-700">&ldquo;{investigationResult.query}&rdquo;</span>
              </h3>
              <p className="text-sm text-gray-500">
                {investigationResult.total_events_found} event(s) across{' '}
                {investigationResult.searched_recordings} recording(s)
              </p>
            </div>
          </div>

          {/* Ordering indicator */}
          <div className={`rounded-lg px-4 py-3 text-sm ${
            investigationResult.timestamp_ordering_reliable
              ? 'bg-green-50 text-green-800 border border-green-200'
              : 'bg-amber-50 text-amber-800 border border-amber-200'
          }`}>
            <span className="font-medium">
              {investigationResult.timestamp_ordering_reliable ? '✓ Reliable timeline' : '⚠ Approximate timeline'}
            </span>
            {' — '}
            {investigationResult.ordering_explanation}
          </div>

          {/* Narrative */}
          {investigationResult.narrative && (
            <div className="bg-gray-50 border border-gray-200 rounded-xl p-4">
              <h4 className="text-sm font-semibold text-gray-700 mb-2">Incident Summary</h4>
              <pre className="text-sm text-gray-700 whitespace-pre-wrap font-sans">
                {investigationResult.narrative}
              </pre>
            </div>
          )}

          {/* Event timeline */}
          {investigationResult.events.length > 0 ? (
            <div className="space-y-3">
              <h4 className="font-medium text-gray-700">Event Timeline</h4>
              {investigationResult.events.map((ev, i) => (
                <EventCard
                  key={ev.event_id}
                  event={ev}
                  index={i + 1}
                  highlight={i === 0}
                  onPlayEvidence={(rid, ts) => setVideoModal({ recordingId: rid, timestamp: ts })}
                />
              ))}
            </div>
          ) : (
            <div className="bg-gray-50 rounded-xl border border-gray-200 p-8 text-center text-gray-500">
              No events found for &ldquo;{investigationResult.query}&rdquo;
            </div>
          )}

          {/* Evidence gaps */}
          {investigationResult.evidence_gaps.length > 0 && (
            <div className="bg-orange-50 border border-orange-200 rounded-xl p-4">
              <h4 className="text-sm font-semibold text-orange-800 mb-2">Evidence Gaps</h4>
              <ul className="text-sm text-orange-700 space-y-1">
                {investigationResult.evidence_gaps.map((gap, i) => (
                  <li key={i} className="flex gap-2">
                    <span>·</span>
                    <span>{gap}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Uncertainty summary */}
          {investigationResult.uncertainty_summary && (
            <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3">
              <h4 className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-1">
                Uncertainty Notes
              </h4>
              <p className="text-sm text-gray-600">{investigationResult.uncertainty_summary}</p>
            </div>
          )}
        </div>
      )}
      {videoModalRecording && videoModal && (
        <VideoPlayerModal
          recording={videoModalRecording}
          initialTimestamp={videoModal.timestamp}
          onClose={() => setVideoModal(null)}
        />
      )}
    </div>
  )
}
