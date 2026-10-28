'use client'
import { useState, useEffect, useCallback } from 'react'
import VideoLibrary from '@/components/VideoLibrary'
import SearchPanel from '@/components/SearchPanel'
import InvestigationPanel from '@/components/InvestigationPanel'
import UsageBadge from '@/components/UsageBadge'
import type { Recording, SearchResult, InvestigationResult } from '@/types'

type Tab = 'library' | 'investigate' | 'search'

export default function Home() {
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null)
  const [investigationResult, setInvestigationResult] = useState<InvestigationResult | null>(null)
  const [activeTab, setActiveTab] = useState<Tab>('library')

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

  const tabs: { id: Tab; label: string }[] = [
    { id: 'library', label: `Cameras (${recordings.length})` },
    { id: 'investigate', label: 'Investigate' },
    { id: 'search', label: 'Search' },
  ]

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <header className="mb-8">
        <div className="flex items-baseline justify-between gap-3">
          <div className="flex items-baseline gap-3">
            <h1 className="text-3xl font-bold text-gray-900">RealityTrace</h1>
            <span className="text-sm text-gray-400 font-medium">Evidence-Backed CCTV Investigation</span>
          </div>
          <UsageBadge />
        </div>
        <p className="text-gray-500 mt-1">
          Upload CCTV recordings and investigate incidents with structured, evidence-backed event timelines.
        </p>
      </header>

      <div className="flex gap-2 mb-6">
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 rounded-lg font-medium transition-colors ${
              activeTab === tab.id
                ? 'bg-indigo-600 text-white'
                : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50'
            }`}
          >
            {tab.label}
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
      {activeTab === 'investigate' && (
        <InvestigationPanel
          recordings={recordings}
          investigationResult={investigationResult}
          onInvestigate={setInvestigationResult}
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
