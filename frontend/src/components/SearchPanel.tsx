'use client'
import { useState } from 'react'
import SightingCard from './SightingCard'
import type { Recording, SearchResult } from '@/types'

interface Props {
  recordings: Recording[]
  searchResult: SearchResult | null
  onSearch: (result: SearchResult) => void
}

export default function SearchPanel({ recordings, searchResult, onSearch }: Props) {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const indexedCount = recordings.filter(r => ['INDEXED', 'PARTIAL'].includes(r.processing_status)).length

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/search/?q=${encodeURIComponent(query.trim())}`)
      if (!res.ok) {
        const err = await res.json()
        setError(err.detail || 'Search failed')
      } else {
        onSearch(await res.json())
      }
    } catch {
      setError('Network error. Is the backend running?')
    }
    setLoading(false)
  }

  return (
    <div className="space-y-5">
      {/* Search form */}
      <div className="rt-card p-6">
        <div className="mb-4">
          <h2 className="rt-section-title">Search Across Recordings</h2>
          <p className="rt-section-subtitle">
            {indexedCount === 0
              ? 'No indexed recordings yet. Upload and wait for processing.'
              : `Searching across ${indexedCount} indexed recording${indexedCount === 1 ? '' : 's'}.`}
          </p>
        </div>
        <form onSubmit={handleSearch} className="flex gap-2">
          <div className="relative flex-1">
            <svg viewBox="0 0 24 24" fill="none" className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#94A3B8] pointer-events-none">
              <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
              <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="e.g. Where did I last see my blue notebook?"
              className="rt-input pl-9"
              disabled={loading}
            />
          </div>
          <button type="submit" disabled={loading || !query.trim()} className="rt-btn-primary whitespace-nowrap">
            {loading ? 'Searching…' : 'Search'}
          </button>
        </form>
        {error && <div className="rt-banner-danger mt-3">{error}</div>}
      </div>

      {/* Results */}
      {searchResult && (
        <div className="space-y-4">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div>
              <h3 className="rt-section-title">
                Results for <span className="text-[#2563EB]">&ldquo;{searchResult.query}&rdquo;</span>
              </h3>
              <p className="rt-section-subtitle mt-0">
                {searchResult.total_candidates} candidate(s) across {searchResult.searched_recordings} recording(s)
              </p>
            </div>
          </div>

          <div className={searchResult.timestamp_ordering_reliable ? 'rt-banner-success' : 'rt-banner-warning'}>
            {searchResult.timestamp_ordering_reliable ? '✓ ' : '⚠ '}
            {searchResult.ordering_explanation}
          </div>

          {searchResult.best_sighting ? (
            <>
              <div>
                <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest mb-2">Most recent confirmed sighting</p>
                <SightingCard sighting={searchResult.best_sighting} highlight />
              </div>
              {searchResult.other_sightings.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest mb-2">Other sightings</p>
                  <div className="space-y-2">
                    {searchResult.other_sightings.map(s => (
                      <SightingCard key={s.observation_id} sighting={s} />
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="rt-card rt-empty">
              <svg viewBox="0 0 24 24" fill="none" className="w-8 h-8 text-[#E2E8F0] mb-2">
                <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
                <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
              </svg>
              <p className="text-sm font-medium text-[#64748B]">No reliable sighting found</p>
              <p className="text-xs text-[#94A3B8] mt-0.5">&ldquo;{searchResult.query}&rdquo;</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
