'use client'
import { useState } from 'react'
import EventCard from './EventCard'
import type { Recording, InvestigationResult } from '@/types'

interface Props {
  recordings: Recording[]
  investigationResult: InvestigationResult | null
  onInvestigate: (result: InvestigationResult) => void
}

export default function InvestigationPanel({ recordings, investigationResult, onInvestigate }: Props) {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
    try {
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
            {loading ? 'Investigating…' : 'Investigate'}
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

      {/* Results */}
      {investigationResult && (
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
                <EventCard key={ev.event_id} event={ev} index={i + 1} highlight={i === 0} />
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
    </div>
  )
}
