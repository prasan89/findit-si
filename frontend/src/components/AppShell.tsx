'use client'
import { useState } from 'react'

type NavItem = {
  id: string
  label: string
  icon: React.ReactNode
  badge?: number
}

interface Props {
  activeView: string
  onNavigate: (view: string) => void
  recordingCount: number
  indexedCount: number
  pageTitle: string
  children: React.ReactNode
  topbarRight?: React.ReactNode
  fullHeight?: boolean
}

function CameraIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className}>
      <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

function ChevronIcon({ collapsed, className }: { collapsed: boolean; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} style={{ transform: collapsed ? 'rotate(180deg)' : undefined, transition: 'transform 0.2s' }}>
      <path d="M15 19l-7-7 7-7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

const NAV_ITEMS: NavItem[] = [
  {
    id: 'home',
    label: 'Overview',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5">
        <path d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
    ),
  },
  {
    id: 'library',
    label: 'Recordings',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5">
        <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
    ),
  },
  {
    id: 'investigate',
    label: 'Investigate',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5">
        <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
        <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      </svg>
    ),
  },
  {
    id: 'guardian',
    label: 'Guardian',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5">
        <path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
    ),
  },
]

export default function AppShell({
  activeView,
  onNavigate,
  recordingCount,
  indexedCount,
  pageTitle,
  children,
  topbarRight,
  fullHeight = false,
}: Props) {
  const [collapsed, setCollapsed] = useState(false)

  const visibleNavView = ['home', 'library', 'investigate', 'guardian'].includes(activeView)
    ? activeView
    : activeView === 'results' || activeView === 'workspace'
      ? 'investigate'
      : activeView

  return (
    <div className="flex" style={{ height: '100dvh', overflow: 'hidden' }}>
      {/* ── Sidebar ─────────────────────────────────── */}
      <aside
        className={`rt-sidebar shrink-0 ${collapsed ? 'rt-sidebar-collapsed' : ''}`}
        style={{ zIndex: 20 }}
      >
        {/* Logo row */}
        <div className="rt-sidebar-logo">
          <div className="rt-sidebar-logo-icon">
            <CameraIcon className="w-[18px] h-[18px] text-white" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <span className="font-bold text-[#0F172A] text-sm tracking-tight block leading-tight">RealityTrace</span>
              <span className="text-[10px] text-[#94A3B8] font-medium block">CCTV Investigation</span>
            </div>
          )}
        </div>

        {/* Nav items */}
        <nav className="flex-1 overflow-y-auto rt-sidebar-section pt-3">
          {!collapsed && (
            <p className="rt-sidebar-section-label">Navigation</p>
          )}
          <ul className="space-y-0.5">
            {NAV_ITEMS.map(item => (
              <li key={item.id}>
                <button
                  onClick={() => onNavigate(item.id)}
                  className={`rt-nav-item ${visibleNavView === item.id ? 'rt-nav-item-active' : ''} ${collapsed ? 'justify-center px-0' : ''}`}
                  title={collapsed ? item.label : undefined}
                >
                  <span className="rt-nav-item-icon">{item.icon}</span>
                  {!collapsed && <span>{item.label}</span>}
                  {!collapsed && item.id === 'library' && recordingCount > 0 && (
                    <span className="ml-auto text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-[#F1F5F9] text-[#94A3B8]">
                      {recordingCount}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        {/* Status + collapse toggle */}
        <div className="shrink-0 border-t border-[#E2E8F0] px-3 py-3 space-y-2">
          {!collapsed && (
            <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-[#F8FAFC]">
              <span className={`w-2 h-2 rounded-full shrink-0 ${indexedCount > 0 ? 'bg-[#16A34A]' : 'bg-[#94A3B8]'}`} />
              <span className="text-xs text-[#64748B] truncate">
                {indexedCount > 0 ? `${indexedCount} camera${indexedCount !== 1 ? 's' : ''} indexed` : 'No cameras indexed'}
              </span>
            </div>
          )}
          <button
            onClick={() => setCollapsed(c => !c)}
            className={`rt-nav-item ${collapsed ? 'justify-center px-0' : ''}`}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <ChevronIcon collapsed={collapsed} className="w-4 h-4 shrink-0" />
            {!collapsed && <span className="text-xs">Collapse</span>}
          </button>
        </div>
      </aside>

      {/* ── Main area ───────────────────────────────── */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        {/* Topbar */}
        <header className="rt-topbar">
          <div className="flex-1 min-w-0">
            <h1 className="text-sm font-semibold text-[#0F172A] truncate">{pageTitle}</h1>
          </div>
          {topbarRight && (
            <div className="flex items-center gap-2 shrink-0">
              {topbarRight}
            </div>
          )}
        </header>

        {/* Content */}
        <main className={fullHeight ? 'flex-1 min-h-0 overflow-hidden' : 'flex-1 overflow-y-auto bg-[#F8FAFC]'}>
          {children}
        </main>
      </div>
    </div>
  )
}
