'use client'
import type { FeedItem } from '@/lib/guardianRouting'
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

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Tab bar */}
      <div className="flex border-b border-[#E2E8F0] bg-white shrink-0">
        {/* Police / 911 tab */}
        <button
          onClick={() => onTabChange('police')}
          className={`flex-1 flex items-center justify-center gap-2 px-4 py-3.5 text-sm font-semibold border-b-2 transition-colors ${
            tab === 'police'
              ? 'border-[#2563EB] text-[#2563EB] bg-[#EFF6FF]/40'
              : 'border-transparent text-[#64748B] hover:text-[#0F172A] hover:bg-[#F8FAFC]'
          }`}
          aria-label="Police / 911 feed"
        >
          <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-lg shrink-0">
            🚔
          </div>
          <div className="text-left">
            <div className="text-sm font-semibold">Police / 911</div>
            <div className="text-xs text-[#64748B] font-normal">Serious incidents requiring police attention</div>
          </div>
          {policeItems.length > 0 && (
            <span className={`ml-auto text-xs font-bold px-2 py-0.5 rounded-full ${
              tab === 'police' ? 'bg-red-500 text-white' : 'bg-[#E2E8F0] text-[#64748B]'
            }`}>
              {policeItems.filter(i => i.review_status === 'new').length} urgent
            </span>
          )}
        </button>

        {/* Property Owner tab */}
        <button
          onClick={() => onTabChange('owner')}
          className={`flex-1 flex items-center justify-center gap-2 px-4 py-3.5 text-sm font-semibold border-b-2 transition-colors ${
            tab === 'owner'
              ? 'border-[#2563EB] text-[#2563EB] bg-[#EFF6FF]/40'
              : 'border-transparent text-[#64748B] hover:text-[#0F172A] hover:bg-[#F8FAFC]'
          }`}
          aria-label="Property Owner feed"
        >
          <div className="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center text-lg shrink-0">
            👤
          </div>
          <div className="text-left">
            <div className="text-sm font-semibold">Property Owner</div>
            <div className="text-xs text-[#64748B] font-normal">All events (motion, people, vehicles, hazards)</div>
          </div>
          {ownerItems.length > 0 && (
            <span className={`ml-auto text-xs font-bold px-2 py-0.5 rounded-full ${
              tab === 'owner' ? 'bg-[#2563EB] text-white' : 'bg-[#E2E8F0] text-[#64748B]'
            }`}>
              {ownerItems.length} events
            </span>
          )}
        </button>
      </div>

      {/* Police simulation banner */}
      {tab === 'police' && (
        <div className="flex items-center gap-2 px-4 py-2 bg-amber-50 border-b border-amber-200 shrink-0">
          <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4 text-amber-600 shrink-0">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.5"/>
            <path d="M12 8v4M12 16h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          <span className="text-xs text-amber-700">
            <strong>Simulation only</strong> — no real emergency call placed. This is a demonstration of what Guardian would report.
          </span>
        </div>
      )}

      {/* Police chat header */}
      {tab === 'police' && (
        <div className="flex items-center gap-3 px-4 py-2.5 bg-[#075E54] shrink-0">
          <div className="w-8 h-8 rounded-full bg-white/20 flex items-center justify-center text-sm">🚔</div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-white">Police Dispatch (Simulation)</div>
            <div className="text-[11px] text-green-200">RealityTrace Guardian ● Simulation</div>
          </div>
          <div className="text-[11px] text-white/60">Simulation only — no real emergency call placed</div>
        </div>
      )}

      {/* Owner chat header */}
      {tab === 'owner' && (
        <div className="flex items-center gap-3 px-4 py-2.5 bg-[#128C7E] shrink-0">
          <div className="w-8 h-8 rounded-full bg-white/20 flex items-center justify-center text-sm">👤</div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-white">Property Owner</div>
            <div className="text-[11px] text-green-200">All camera activity</div>
          </div>
        </div>
      )}

      {/* Feed content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3" style={{ background: '#F8FAFC' }}>
        {visibleItems.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 gap-3">
            <div className="w-12 h-12 rounded-full bg-white border border-[#E2E8F0] flex items-center justify-center text-2xl shadow-sm">
              {tab === 'police' ? '🛡️' : '📭'}
            </div>
            <p className="text-sm font-medium text-[#64748B]">
              {tab === 'police'
                ? 'No serious incidents to report'
                : 'No camera activity recorded yet'}
            </p>
            <p className="text-xs text-[#94A3B8] text-center max-w-60">
              {tab === 'police'
                ? 'Guardian found no police-relevant incidents matching the current filters.'
                : 'Events will appear here once recordings are indexed.'}
            </p>
          </div>
        ) : (
          <>
            {/* Date separator */}
            <div className="flex items-center gap-2 py-1">
              <div className="flex-1 h-px bg-[#E2E8F0]" />
              <span className="text-[11px] text-[#94A3B8] bg-white px-2 py-0.5 rounded-full border border-[#E2E8F0]">
                Today
              </span>
              <div className="flex-1 h-px bg-[#E2E8F0]" />
            </div>

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

            {/* Incident report summary at bottom of police feed */}
            {tab === 'police' && visibleItems.length > 0 && (
              <div className="rt-card p-3 flex items-start gap-2 bg-blue-50 border border-blue-100">
                <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4 text-blue-500 mt-0.5 shrink-0">
                  <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.5"/>
                  <path d="M12 8v4M12 16h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-blue-700">Automatic Incident Report</p>
                  <p className="text-xs text-blue-600 mt-0.5">
                    An incident report has been assembled from video evidence
                    ({visibleItems.length} event{visibleItems.length !== 1 ? 's' : ''}) and is ready for review.
                  </p>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Simulated chat input bar */}
      <div className="flex items-center gap-3 px-4 py-2.5 bg-white border-t border-[#E2E8F0] shrink-0">
        <div className="flex-1 rounded-full bg-[#F1F5F9] px-4 py-2 text-xs text-[#94A3B8]">
          Automated alerts appear here (no manual reply)
        </div>
        <div className="w-8 h-8 rounded-full bg-[#E2E8F0] flex items-center justify-center opacity-40">
          <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4 text-[#64748B]">
            <path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </div>
      </div>
    </div>
  )
}
