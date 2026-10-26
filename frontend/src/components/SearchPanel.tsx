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
    <div className="space-y-6">
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h2 className="text-lg font-semibold mb-1">Search Across Videos</h2>
        <p className="text-sm text-gray-500 mb-4">
          {indexedCount === 0
            ? 'No indexed recordings yet. Upload and wait for processing.'
            : `Searching across ${indexedCount} indexed recording${indexedCount === 1 ? '' : 's'}.`}
        </p>
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="e.g. Where did I last see my blue notebook?"
            className="flex-1 rounded-lg border border-gray-300 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="px-5 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? 'Searching…' : 'Search'}
          </button>
        </form>
        {error && <p className="text-sm text-red-600 mt-2">{error}</p>}
      </div>

      {searchResult && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-gray-900">
              Results for: <span className="text-blue-700">&ldquo;{searchResult.query}&rdquo;</span>
            </h3>
            <span className="text-sm text-gray-500">
              {searchResult.total_candidates} candidate(s) across {searchResult.searched_recordings} recording(s)
            </span>
          </div>

          <div className={`rounded-lg px-4 py-3 text-sm ${
            searchResult.timestamp_ordering_reliable
              ? 'bg-green-50 text-green-800 border border-green-200'
              : 'bg-amber-50 text-amber-800 border border-amber-200'
          }`}>
            {searchResult.ordering_explanation}
          </div>

          {searchResult.best_sighting ? (
            <>
              <h4 className="font-medium text-gray-700">Most recent confirmed sighting</h4>
              <SightingCard sighting={searchResult.best_sighting} highlight />
              {searchResult.other_sightings.length > 0 && (
                <>
                  <h4 className="font-medium text-gray-700 mt-6">Other sightings</h4>
                  {searchResult.other_sightings.map(s => (
                    <SightingCard key={s.observation_id} sighting={s} />
                  ))}
                </>
              )}
            </>
          ) : (
            <div className="bg-gray-50 rounded-xl border border-gray-200 p-8 text-center text-gray-500">
              No reliable sighting found for &ldquo;{searchResult.query}&rdquo;
            </div>
          )}
        </div>
      )}
    </div>
  )
}
