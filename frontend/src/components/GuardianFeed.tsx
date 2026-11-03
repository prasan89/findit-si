'use client'
import type { FeedItem, FeedTarget } from '@/lib/guardianRouting'
import GuardianMessageCard from './GuardianMessageCard'

type FeedTab = 'police' | 'owner'

interface Props {
  items: FeedItem[]
  tab: FeedTab
  onTabChange: (t: FeedTab) => void
  selectedItemId: string | null
  onSelectItem: (item: FeedItem) => void
  onAcknowledge: (item: FeedItem, status: 'acknowledged' | 'dismissed') => void
}

// Shield icon for Police tab
function ShieldIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M12 2L3 6v6c0 5.25 3.75 10.15 9 11.35C17.25 22.15 21 17.25 21 12V6L12 2z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

// Home icon for Owner tab
function HomeIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M3 9.5L12 3l9 6.5V20a1 1 0 01-1 1H5a1 1 0 01-1-1V9.5z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M9 21V12h6v9"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

// Info circle icon for empty state
function InfoIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.5" />
      <path d="M12 8v4M12 16h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

export default function GuardianFeed({
  items,
  tab,
  onTabChange,
  selectedItemId,
  onSelectItem,
  onAcknowledge,
}: Props) {
  const policeItems = items.filter(i => i.feeds.includes('police'))
  const ownerItems = items.filter(i => i.feeds.includes('owner'))
  const visibleItems = tab === 'police' ? policeItems : ownerItems

  const policeNewCount = policeItems.filter(i => i.review_status === 'new').length
  const ownerCount = ownerItems.length

  return (
    <div className="flex flex-col flex-1 min-h-0 bg-white">
      {/* Tab bar */}
      <div className="flex shrink-0 border-b border-slate-200 bg-white">
        {/* Police / 911 tab */}
        <button
          onClick={() => onTabChange('police')}
          className={`relative flex-1 flex items-center justify-center gap-2 px-4 py-3 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-red-500 ${
            tab === 'police'
              ? 'text-red-600 bg-red-50/60'
              : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'
          }`}
          aria-selected={tab === 'police'}
          role="tab"
        >
          {/* Active underline indicator */}
          {tab === 'police' && (
            <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-red-500 rounded-t" />
          )}

          <ShieldIcon
            className={`w-4 h-4 shrink-0 ${tab === 'police' ? 'text-red-500' : 'text-slate-400'}`}
          />
          <span className="font-semibold">Police / 911</span>

          {policeNewCount > 0 ? (
            <span
              className={`text-xs font-bold px-1.5 py-0.5 rounded-full tabular-nums ${
                tab === 'police'
                  ? 'bg-red-500 text-white'
                  : 'bg-red-100 text-red-600'
              }`}
            >
              {policeNewCount}
            </span>
          ) : policeItems.length > 0 ? (
            <span
              className={`text-xs font-medium px-1.5 py-0.5 rounded-full tabular-nums ${
                tab === 'police'
                  ? 'bg-red-100 text-red-600'
                  : 'bg-slate-100 text-slate-500'
              }`}
            >
              {policeItems.length}
            </span>
          ) : null}
        </button>

        {/* Property Owner tab */}
        <button
          onClick={() => onTabChange('owner')}
          className={`relative flex-1 flex items-center justify-center gap-2 px-4 py-3 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 ${
            tab === 'owner'
              ? 'text-blue-600 bg-blue-50/60'
              : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'
          }`}
          aria-selected={tab === 'owner'}
          role="tab"
        >
          {/* Active underline indicator */}
          {tab === 'owner' && (
            <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-blue-500 rounded-t" />
          )}

          <HomeIcon
            className={`w-4 h-4 shrink-0 ${tab === 'owner' ? 'text-blue-500' : 'text-slate-400'}`}
          />
          <span className="font-semibold">Property Owner</span>

          {ownerCount > 0 && (
            <span
              className={`text-xs font-medium px-1.5 py-0.5 rounded-full tabular-nums ${
                tab === 'owner'
                  ? 'bg-blue-100 text-blue-600'
                  : 'bg-slate-100 text-slate-500'
              }`}
            >
              {ownerCount}
            </span>
          )}
        </button>
      </div>

      {/* Simulation notice for police tab */}
      {tab === 'police' && (
        <div className="flex items-center gap-2 px-4 py-2 bg-amber-50 border-b border-amber-100 shrink-0">
          <InfoIcon className="w-3.5 h-3.5 text-amber-500 shrink-0" />
          <span className="text-xs text-amber-700">
            <strong>Simulation only</strong> — no real emergency call placed.
          </span>
        </div>
      )}

      {/* Scrollable feed content */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {visibleItems.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 gap-3 px-6 text-center">
            <div
              className={`w-11 h-11 rounded-full flex items-center justify-center ${
                tab === 'police' ? 'bg-red-50 text-red-400' : 'bg-blue-50 text-blue-400'
              }`}
            >
              {tab === 'police' ? (
                <ShieldIcon className="w-5 h-5" />
              ) : (
                <HomeIcon className="w-5 h-5" />
              )}
            </div>
            <p className="text-sm font-medium text-slate-600">No alerts</p>
            <p className="text-xs text-slate-400 max-w-xs leading-relaxed">
              {tab === 'police'
                ? 'No police-eligible alerts. Theft, assault, and abuse incidents will appear here.'
                : 'No property alerts. Hazard events will appear here.'}
            </p>
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {visibleItems.map(item => (
              <GuardianMessageCard
                key={item.id}
                item={item}
                isPoliceTab={tab === 'police'}
                onViewEvidence={onSelectItem}
                onAcknowledge={onAcknowledge}
                isSelected={selectedItemId === item.id}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
