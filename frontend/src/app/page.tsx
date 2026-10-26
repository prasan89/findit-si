'use client'
import { useState, useEffect, useCallback } from 'react'
import VideoLibrary from '@/components/VideoLibrary'
import SearchPanel from '@/components/SearchPanel'
import type { Recording, SearchResult } from '@/types'

export default function Home() {
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null)
  const [activeTab, setActiveTab] = useState<'library' | 'search'>('library')

  const fetchRecordings = useCallback(async () => {
    try {
      const res = await fetch('/api/recordings/')
      if (res.ok) setRecordings(await res.json())
    } catch {}
  }, [])

  useEffect(() => {
    fetchRecordings()
    const interval = setInterval(fetchRecordings, 3000)
    return () => clearInterval(interval)
  }, [fetchRecordings])

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <header className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900">FindIt</h1>
        <p className="text-gray-500 mt-1">Search across your video recordings to find objects</p>
      </header>

      <div className="flex gap-2 mb-6">
        {(['library', 'search'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 rounded-lg font-medium transition-colors ${
              activeTab === tab
                ? 'bg-blue-600 text-white'
                : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50'
            }`}
          >
            {tab === 'library' ? `Video Library (${recordings.length})` : 'Search'}
          </button>
        ))}
      </div>

      {activeTab === 'library' && (
        <VideoLibrary
          recordings={recordings}
          onUploadComplete={fetchRecordings}
          onDelete={fetchRecordings}
        />
      )}
      {activeTab === 'search' && (
        <SearchPanel
          recordings={recordings}
          searchResult={searchResult}
          onSearch={setSearchResult}
        />
      )}
    </div>
  )
}
