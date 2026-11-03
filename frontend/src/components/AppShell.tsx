'use client'

type AppView = 'home' | 'results' | 'workspace' | 'library' | 'guardian'

interface Props {
  activeView: AppView | string
  onNavigate: (view: string) => void
  recordingCount: number
  indexedCount: number
  pageTitle: string
  children: React.ReactNode
  topbarRight?: React.ReactNode
  fullHeight?: boolean
  guardianAlertCount?: number
}

function GridIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" width="16" height="16" aria-hidden="true">
      <rect x="3" y="3" width="7" height="7" rx="1" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="14" y="3" width="7" height="7" rx="1" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="3" y="14" width="7" height="7" rx="1" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="14" y="14" width="7" height="7" rx="1" stroke="currentColor" strokeWidth="1.5"/>
    </svg>
  )
}

function VideoCameraIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" width="16" height="16" aria-hidden="true">
      <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" width="16" height="16" aria-hidden="true">
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M20 20l-3.5-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  )
}

function ShieldIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" width="16" height="16" aria-hidden="true">
      <path d="M12 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016A11.955 11.955 0 0112 2.944z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M9 12l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

export default function AppShell({
  activeView,
  onNavigate,
  recordingCount,
  indexedCount,
  pageTitle,
  children,
  topbarRight,
  fullHeight = false,
  guardianAlertCount,
}: Props) {
  const visibleNavView =
    activeView === 'results' || activeView === 'workspace' ? 'results' : activeView

  const NAV_ITEMS = [
    { id: 'home',     label: 'Overview',       icon: <GridIcon />,        badge: undefined },
    { id: 'library',  label: 'Recordings',     icon: <VideoCameraIcon />, badge: recordingCount > 0 ? recordingCount : undefined },
    { id: 'results',  label: 'Investigations', icon: <SearchIcon />,      badge: undefined },
    { id: 'guardian', label: 'Guardian',       icon: <ShieldIcon />,      badge: guardianAlertCount && guardianAlertCount > 0 ? guardianAlertCount : undefined },
  ]

  return (
    <div className="flex flex-col" style={{ height: '100dvh', overflow: 'hidden' }}>
      {/* Top navigation bar */}
      <header
        className="flex items-center bg-white shrink-0"
        style={{ height: 56, borderBottom: '1px solid #E2E8F0', paddingLeft: 20, paddingRight: 20 }}
      >
        {/* Logo */}
        <div className="flex items-center gap-2.5 shrink-0 mr-8">
          <div
            className="flex items-center justify-center rounded-md font-bold text-white text-sm select-none shrink-0"
            style={{ width: 32, height: 32, backgroundColor: '#2563EB', letterSpacing: '-0.02em' }}
          >
            RT
          </div>
          <div className="hidden sm:block">
            <p className="font-semibold text-sm leading-tight" style={{ color: '#0F172A' }}>RealityTrace</p>
            <p className="text-xs leading-tight" style={{ color: '#94A3B8' }}>AI Video Intelligence</p>
          </div>
        </div>

        {/* Nav tabs */}
        <nav className="flex items-center gap-1 flex-1 min-w-0">
          {NAV_ITEMS.map(item => {
            const isActive = visibleNavView === item.id
            return (
              <button
                key={item.id}
                onClick={() => onNavigate(item.id)}
                className="flex items-center gap-1.5 px-3 rounded-md text-sm font-medium transition-colors duration-150 relative whitespace-nowrap"
                style={{
                  height: 36,
                  color: isActive ? '#2563EB' : '#64748B',
                  backgroundColor: isActive ? '#EFF6FF' : 'transparent',
                }}
                onMouseEnter={e => {
                  if (!isActive) (e.currentTarget as HTMLButtonElement).style.backgroundColor = '#F8FAFC'
                }}
                onMouseLeave={e => {
                  if (!isActive) (e.currentTarget as HTMLButtonElement).style.backgroundColor = 'transparent'
                }}
              >
                <span className="shrink-0" style={{ color: isActive ? '#2563EB' : '#94A3B8' }}>{item.icon}</span>
                <span>{item.label}</span>
                {item.badge !== undefined && (
                  <span
                    className="text-xs font-semibold rounded-full px-1.5 py-0.5 leading-none"
                    style={{
                      backgroundColor: isActive ? '#BFDBFE' : '#F1F5F9',
                      color: isActive ? '#1D4ED8' : '#64748B',
                      minWidth: '1.25rem',
                      textAlign: 'center',
                    }}
                  >
                    {item.badge}
                  </span>
                )}
              </button>
            )
          })}
        </nav>

        {/* Right slot + indexed status */}
        <div className="flex items-center gap-3 shrink-0 ml-4">
          {indexedCount > 0 && (
            <span className="hidden md:flex items-center gap-1.5 text-xs" style={{ color: '#64748B' }}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: '#16A34A' }} />
              {indexedCount} indexed
            </span>
          )}
          {topbarRight}
        </div>
      </header>

      {/* Page content */}
      <main
        className={fullHeight ? 'flex-1 min-h-0 overflow-hidden' : 'flex-1 overflow-y-auto'}
        style={fullHeight ? undefined : { backgroundColor: '#F8FAFC' }}
      >
        {children}
      </main>
    </div>
  )
}
