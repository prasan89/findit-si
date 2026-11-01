'use client'
import { useState, useRef, useCallback } from 'react'
import type {
  Recording,
  InvestigationResult,
  MultiAgentInvestigationResult,
  Event,
  EvidenceFrame,
  AgentRunResult,
  VideoAnalysisSummary,
} from '@/types'

// ── Helpers ────────────────────────────────────────────────────────────────────

function formatTs(secs: number): string {
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = Math.floor(secs % 60)
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`
}

function confidencePct(score: number) { return `${(score * 100).toFixed(0)}%` }

function confidenceColor(score: number): string {
  if (score >= 0.75) return 'text-[#16A34A]'
  if (score >= 0.5) return 'text-[#D97706]'
  return 'text-[#DC2626]'
}

function confidenceBadge(score: number): string {
  if (score >= 0.75) return 'bg-[#F0FDF4] text-[#16A34A] border border-[#BBF7D0]'
  if (score >= 0.5) return 'bg-[#FFFBEB] text-[#D97706] border border-[#FDE68A]'
  return 'bg-[#FEF2F2] text-[#DC2626] border border-[#FECACA]'
}

function eventTypeBadge(type: string): string {
  if (type.includes('theft') || type.includes('assault') || type.includes('fall'))
    return 'bg-[#FEF2F2] text-[#DC2626] border border-[#FECACA]'
  if (type.includes('confrontation') || type.includes('suspicious'))
    return 'bg-[#FFFBEB] text-[#D97706] border border-[#FDE68A]'
  return 'bg-[#F1F5F9] text-[#64748B] border border-[#E2E8F0]'
}

function isIncidentType(type: string): boolean {
  return type.includes('theft') || type.includes('assault') || type.includes('fall') ||
    type.includes('confrontation') || type.includes('suspicious')
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  person_detected:                 'Person',
  vehicle_detected:                'Vehicle',
  bird_detected:                   'Bird',
  unattended_object:               'Object',
  entry_exit_detected:             'Entry/Exit',
  scene_change:                    'Scene Change',
  'possible-theft':                'Possible Theft',
  'possible-assault':              'Possible Assault',
  confrontation:                   'Confrontation',
  'sudden-fall':                   'Sudden Fall',
  'suspicious-approach-departure': 'Suspicious Activity',
  'other-anomaly':                 'Anomaly',
}

const STATUS_BADGE: Record<string, string> = {
  completed: 'bg-[#F0FDF4] text-[#16A34A]',
  failed:    'bg-[#FEF2F2] text-[#DC2626]',
  skipped:   'bg-[#FFFBEB] text-[#D97706]',
  timed_out: 'bg-[#FFFBEB] text-[#D97706]',
  running:   'bg-[#EFF6FF] text-[#2563EB]',
  pending:   'bg-[#F1F5F9] text-[#64748B]',
  mocked:    'bg-[#F1F5F9] text-[#64748B]',
}

const AGENT_DESCRIPTIONS: Record<string, string> = {
  VideoIntelligenceAgent:         'Visual summary per event',
  ObjectActionAnalystAgent:       'Objects & actions identification',
  TemporalReasoningAgent:         'Timeline normalization & gap detection',
  CrossCameraCorrelationAgent:    'Cross-camera candidate links',
  QueryInvestigationAgent:        'Evidence retrieval & ranking',
  ContradictionHunterAgent:       'Inconsistency & contradiction detection',
  EvidenceAuditorAgent:           'Claim verification against source evidence',
  ReportSynthesisAgent:           'Final narrative assembly',
}

// ── Sub-components ─────────────────────────────────────────────────────────────

interface VideoPlayerProps {
  recording: Recording | null
  seekTo?: number
  onTimeUpdate?: (t: number) => void
}

function VideoPlayer({ recording, seekTo, onTimeUpdate }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [error, setError] = useState(false)
  const prevSeek = useRef<number | undefined>(undefined)

  const handleLoaded = useCallback(() => {
    if (seekTo != null && videoRef.current) videoRef.current.currentTime = seekTo
  }, [seekTo])

  if (seekTo != null && prevSeek.current !== seekTo && videoRef.current) {
    videoRef.current.currentTime = seekTo
    prevSeek.current = seekTo
  }

  if (!recording) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400 text-sm">
        Select a video to preview
      </div>
    )
  }

  return (
    <div className="relative h-full">
      {error ? (
        <div className="flex items-center justify-center h-full text-slate-400 text-sm">Video unavailable</div>
      ) : (
        <video
          ref={videoRef}
          key={recording.recording_id}
          src={`/api/recordings/${recording.recording_id}/video`}
          controls
          className="w-full h-full object-contain"
          crossOrigin="anonymous"
          onLoadedMetadata={handleLoaded}
          onError={() => setError(true)}
          onTimeUpdate={() => { if (onTimeUpdate && videoRef.current) onTimeUpdate(videoRef.current.currentTime) }}
        />
      )}
    </div>
  )
}

interface EventMarkerProps {
  events: Event[]
  duration: number
  activeEventId: string | null
  onSelect: (ev: Event) => void
}

function EventMarkers({ events, duration, activeEventId, onSelect }: EventMarkerProps) {
  if (!duration) return null
  return (
    <>
      {events.map(ev => {
        const pct = Math.min(100, (ev.timestamp_seconds / duration) * 100)
        const incident = isIncidentType(ev.event_type)
        const isActive = ev.event_id === activeEventId
        return (
          <button
            key={ev.event_id}
            title={`${EVENT_TYPE_LABELS[ev.event_type] ?? ev.event_type} @ ${formatTs(ev.timestamp_seconds)}`}
            onClick={() => onSelect(ev)}
            style={{ left: `${pct}%` }}
            className={`absolute top-1/2 -translate-y-1/2 -translate-x-1/2 rounded-full transition-all z-10
              ${isActive ? 'w-4 h-4 ring-2 ring-white shadow-md' : 'w-2.5 h-2.5'}
              ${incident ? 'bg-[#DC2626]' : 'bg-[#D97706]'}`}
          />
        )
      })}
    </>
  )
}

function cleanFacts(facts: string[]): string[] {
  return facts.filter(f => !f.startsWith('Candidate ') && !f.startsWith('Observed sequence span'))
}

interface EvidenceGroupProps {
  label: string
  color: 'green' | 'amber' | 'slate'
  items: string[]
}

function EvidenceGroup({ label, color, items }: EvidenceGroupProps) {
  if (!items.length) return null
  const dotCls = { green: 'bg-[#16A34A]', amber: 'bg-[#D97706]', slate: 'bg-[#94A3B8]' }[color]
  const labelCls = { green: 'text-[#16A34A]', amber: 'text-[#D97706]', slate: 'text-[#94A3B8]' }[color]
  return (
    <div className="space-y-1.5">
      <div className={`text-[10px] font-semibold uppercase tracking-widest ${labelCls}`}>{label}</div>
      {items.map((item, i) => (
        <div key={i} className="flex gap-2 items-start">
          <span className={`mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotCls}`} />
          <span className="text-xs text-[#64748B] leading-snug">{item}</span>
        </div>
      ))}
    </div>
  )
}

interface EventTimelineProps {
  events: Event[]
  activeEventId: string | null
  onSelect: (ev: Event) => void
}

function EventTimeline({ events, activeEventId, onSelect }: EventTimelineProps) {
  if (!events.length) return null
  return (
    <div className="overflow-x-auto">
      <div className="flex gap-2 pb-1 min-w-max">
        {events.map((ev, i) => {
          const isActive = ev.event_id === activeEventId
          const incident = isIncidentType(ev.event_type)
          return (
            <button
              key={ev.event_id}
              onClick={() => onSelect(ev)}
              className={`flex-shrink-0 rounded-lg border px-3 py-2 text-left transition-all max-w-[160px]
                ${isActive
                  ? 'border-[#2563EB] bg-[#EFF6FF] ring-1 ring-[#2563EB]'
                  : incident
                    ? 'border-[#FECACA] bg-[#FEF2F2] hover:bg-[#FEE2E2]'
                    : 'border-[#E2E8F0] bg-white hover:bg-[#F8FAFC]'
                }`}
            >
              <div className={`text-[10px] font-semibold mb-0.5 ${incident ? 'text-[#DC2626]' : 'text-[#94A3B8]'}`}>
                {String(i + 1).padStart(2, '0')} · {formatTs(ev.timestamp_seconds)}
              </div>
              <div className="text-xs text-[#0F172A] truncate">
                {EVENT_TYPE_LABELS[ev.event_type] ?? ev.event_type}
              </div>
              <div className={`text-[10px] font-mono mt-0.5 ${confidenceColor(ev.confidence)}`}>
                {confidencePct(ev.confidence)}
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── Main workspace ─────────────────────────────────────────────────────────────

type RightTab = 'summary' | 'evidence' | 'multi-agent'
type EvidenceFilter = 'all' | 'observed' | 'inferred' | 'uncertain'

interface Props {
  recordings: Recording[]
  result: InvestigationResult
  multiAgentResult?: MultiAgentInvestigationResult | null
  onNewInvestigation: () => void
  activeRecordingId?: string
  videoSummary?: VideoAnalysisSummary | null
}

export default function RealityTraceWorkspace({ recordings, result, multiAgentResult, onNewInvestigation, activeRecordingId, videoSummary }: Props) {
  const [activeEventId, setActiveEventId] = useState<string | null>(result.events[0]?.event_id ?? null)
  const [seekTarget, setSeekTarget] = useState<number | undefined>(result.events[0]?.timestamp_seconds)
  const [rightTab, setRightTab] = useState<RightTab>('summary')
  const [evidenceFilter, setEvidenceFilter] = useState<EvidenceFilter>('all')
  const [activeFrameMap, setActiveFrameMap] = useState<Record<string, EvidenceFrame>>({})

  const activeEvent = result.events.find(e => e.event_id === activeEventId) ?? null
  const activeRecording = activeRecordingId
    ? recordings.find(r => r.recording_id === activeRecordingId) ?? null
    : activeEvent
      ? recordings.find(r => r.recording_id === activeEvent.recording_id) ?? null
      : recordings[0] ?? null

  function selectEvent(ev: Event) {
    setActiveEventId(ev.event_id)
    setSeekTarget(ev.timestamp_seconds)
  }

  function getActiveFrame(ev: Event): EvidenceFrame | null {
    if (activeFrameMap[ev.event_id]) return activeFrameMap[ev.event_id]
    return ev.evidence_frames.find(f => f.role === 'during') ?? ev.evidence_frames[0] ?? null
  }

  function setFrameForEvent(evId: string, frame: EvidenceFrame) {
    setActiveFrameMap(prev => ({ ...prev, [evId]: frame }))
  }

  const duration = activeRecording?.video_duration_seconds ?? 0
  const indexedCount = recordings.filter(r => r.processing_status === 'INDEXED' || r.processing_status === 'PARTIAL').length

  const incidentEvents = result.events.filter(e => isIncidentType(e.event_type))
  const primaryEvent = result.events.find(e => e.is_primary_finding) ?? result.events[0]

  const observed = activeEvent ? cleanFacts(activeEvent.observed_facts) : []
  const inferred = activeEvent?.inferred_claims ?? []
  const uncertain = activeEvent?.uncertainty_notes ?? []

  const visibleObserved = evidenceFilter === 'all' || evidenceFilter === 'observed' ? observed : []
  const visibleInferred = evidenceFilter === 'all' || evidenceFilter === 'inferred' ? inferred : []
  const visibleUncertain = evidenceFilter === 'all' || evidenceFilter === 'uncertain' ? uncertain : []

  return (
    <div className="flex flex-col min-h-screen bg-[#F8FAFC]">

      {/* ── Header bar ────────────────────────────────────────────── */}
      <header className="flex items-center justify-between px-5 py-3 bg-white border-b border-[#E2E8F0] shadow-sm shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={onNewInvestigation}
            className="rt-btn-ghost text-sm gap-1.5 shrink-0"
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
              <path d="M19 12H5M5 12l7-7M5 12l7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            {videoSummary ? 'Back to results' : 'Back'}
          </button>
          <div className="w-px h-5 bg-[#E2E8F0] shrink-0" />
          <div className="flex items-center gap-2 min-w-0">
            <span className={`rt-badge shrink-0 ${incidentEvents.length > 0 ? 'bg-[#FEF2F2] text-[#DC2626] border border-[#FECACA]' : 'bg-[#EFF6FF] text-[#2563EB] border border-[#BFDBFE]'}`}>
              {incidentEvents.length > 0 ? 'INCIDENT' : 'INVESTIGATION'}
            </span>
            <span className="text-sm font-medium text-[#0F172A] truncate">
              {videoSummary
                ? videoSummary.filename
                : `"${result.query}"`
              }
            </span>
          </div>
        </div>
        <div className="flex items-center gap-5 shrink-0">
          <div className="flex items-center gap-4 text-xs text-[#64748B]">
            <span>{indexedCount} camera{indexedCount !== 1 ? 's' : ''}</span>
            <span className="text-[#E2E8F0]">·</span>
            <span>{result.total_events_found} event{result.total_events_found !== 1 ? 's' : ''}</span>
            {incidentEvents.length > 0 && (
              <>
                <span className="text-[#E2E8F0]">·</span>
                <span className="text-[#DC2626] font-medium">{incidentEvents.length} incident{incidentEvents.length !== 1 ? 's' : ''}</span>
              </>
            )}
          </div>
          <button className="rt-btn-secondary text-xs py-1.5 px-3">
            <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5">
              <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M7 10l5 5m0 0l5-5m-5 5V4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            Export
          </button>
        </div>
      </header>

      {/* ── Main content ──────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Left: Video + Timeline ─────────────────────────────── */}
        <div className="flex flex-col flex-1 min-w-0 border-r border-[#E2E8F0]">

          {/* Video player — dark media surface */}
          <div className="relative bg-black" style={{ height: '52vh' }}>
            <VideoPlayer recording={activeRecording} seekTo={seekTarget} />

            {/* Camera + timestamp overlay */}
            {activeRecording && (
              <div className="absolute top-3 left-3 flex items-center gap-2 pointer-events-none">
                <span className="text-[11px] font-mono bg-black/60 text-white/90 px-2 py-1 rounded">
                  {activeRecording.camera_name ?? activeRecording.original_filename}
                </span>
                {activeEvent && (
                  <span className="text-[11px] font-mono bg-black/60 text-white/70 px-2 py-1 rounded">
                    t={formatTs(activeEvent.timestamp_seconds)}
                  </span>
                )}
              </div>
            )}

            {/* Event markers on scrubber */}
            {duration > 0 && (
              <div className="absolute bottom-10 left-3 right-3 h-1 bg-white/20 rounded-full">
                <div className="relative h-full">
                  <EventMarkers
                    events={result.events}
                    duration={duration}
                    activeEventId={activeEventId}
                    onSelect={selectEvent}
                  />
                </div>
              </div>
            )}
          </div>

          {/* Event timeline strip */}
          <div className="bg-white border-t border-[#E2E8F0] px-4 py-3 shrink-0">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[11px] font-semibold uppercase tracking-widest text-[#94A3B8]">
                Event Timeline — {result.events.length} event{result.events.length !== 1 ? 's' : ''}
              </span>
              <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                result.timestamp_ordering_reliable
                  ? 'bg-[#F0FDF4] text-[#16A34A]'
                  : 'bg-[#FFFBEB] text-[#D97706]'
              }`}>
                {result.timestamp_ordering_reliable ? '✓ Reliable order' : '⚠ Approx. order'}
              </span>
            </div>
            <EventTimeline events={result.events} activeEventId={activeEventId} onSelect={selectEvent} />
          </div>

          {/* Evidence frames */}
          {activeEvent && activeEvent.evidence_frames.length > 0 && (
            <div className="bg-[#F8FAFC] border-t border-[#E2E8F0] px-4 py-3 shrink-0">
              <div className="text-[11px] font-semibold uppercase tracking-widest text-[#94A3B8] mb-2">
                Evidence Frames
              </div>
              <div className="flex gap-3">
                {(['before', 'during', 'after'] as const).map(role => {
                  const frame = activeEvent.evidence_frames.find(f => f.role === role)
                  if (!frame) return null
                  const af = getActiveFrame(activeEvent)
                  const isActive = af?.frame_path === frame.frame_path
                  return (
                    <button
                      key={role}
                      onClick={() => {
                        setFrameForEvent(activeEvent.event_id, frame)
                        setSeekTarget(frame.timestamp_seconds)
                      }}
                      className={`flex-shrink-0 rounded-lg overflow-hidden border-2 transition-all ${
                        isActive ? 'border-[#2563EB] shadow-md' : 'border-[#E2E8F0] hover:border-[#93C5FD]'
                      }`}
                    >
                      <img
                        src={`/${frame.frame_path}`}
                        alt={`${role} frame`}
                        className="w-32 h-20 object-cover bg-[#F1F5F9]"
                        onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                      />
                      <div className={`text-[10px] text-center py-1 font-medium ${
                        isActive ? 'bg-[#EFF6FF] text-[#2563EB]' : 'bg-white text-[#94A3B8]'
                      }`}>
                        {role} · {formatTs(frame.timestamp_seconds)}
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        {/* ── Right panel ───────────────────────────────────────────── */}
        <div className="w-[360px] flex-shrink-0 flex flex-col bg-white overflow-hidden">

          {/* Tab bar */}
          <div className="flex border-b border-[#E2E8F0] shrink-0">
            {(['summary', 'evidence', 'multi-agent'] as RightTab[]).map(tab => {
              const labels: Record<RightTab, string> = { summary: 'Summary', evidence: 'Evidence', 'multi-agent': 'Multi-Agent' }
              const hasMA = tab === 'multi-agent' && multiAgentResult
              return (
                <button
                  key={tab}
                  onClick={() => setRightTab(tab)}
                  className={`flex-1 text-xs font-medium py-3 transition-colors border-b-2 ${
                    rightTab === tab
                      ? 'border-[#2563EB] text-[#2563EB] bg-[#EFF6FF]/40'
                      : 'border-transparent text-[#64748B] hover:text-[#0F172A] hover:bg-[#F8FAFC]'
                  }`}
                >
                  {labels[tab]}
                  {hasMA && (
                    <span className="ml-1.5 text-[9px] bg-[#EFF6FF] text-[#2563EB] px-1.5 py-0.5 rounded-full">
                      {multiAgentResult.agents.filter(a => a.status === 'completed').length}/{multiAgentResult.agents.length}
                    </span>
                  )}
                </button>
              )
            })}
          </div>

          {/* Panel content */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">

            {/* ── Summary tab ── */}
            {rightTab === 'summary' && (
              <>
                {primaryEvent && (
                  <div className="rt-card p-3 space-y-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`rt-badge text-xs font-semibold ${eventTypeBadge(primaryEvent.event_type)}`}>
                        {EVENT_TYPE_LABELS[primaryEvent.event_type] ?? primaryEvent.event_type}
                      </span>
                      <span className={`text-xs font-mono font-medium ${confidenceColor(primaryEvent.confidence)}`}>
                        {confidencePct(primaryEvent.confidence)} confidence
                      </span>
                    </div>
                    {primaryEvent.primary_match && (
                      <p className="text-xs text-[#0284C7] bg-[#F0F9FF] rounded px-2 py-1.5 border border-[#BAE6FD]">
                        {primaryEvent.primary_match}
                      </p>
                    )}
                    <p className="text-sm text-[#0F172A] font-medium">{primaryEvent.action_description}</p>
                  </div>
                )}

                {(result.summary || result.narrative) && (
                  <div className="space-y-2">
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-[#94A3B8]">
                      Incident Summary
                    </div>
                    {result.summary && (
                      <p className="text-sm text-[#0F172A] leading-relaxed">{result.summary}</p>
                    )}
                    {result.narrative && (
                      <details className="group">
                        <summary className="text-xs text-[#64748B] cursor-pointer hover:text-[#0F172A] transition-colors">
                          Full narrative
                        </summary>
                        <p className="mt-2 text-xs text-[#64748B] leading-relaxed whitespace-pre-wrap">
                          {result.narrative}
                        </p>
                      </details>
                    )}
                  </div>
                )}

                <div className={`text-xs rounded-lg px-3 py-2 flex items-start gap-2 ${
                  result.timestamp_ordering_reliable ? 'rt-banner-success' : 'rt-banner-warning'
                }`}>
                  <span className="shrink-0 mt-0.5">{result.timestamp_ordering_reliable ? '✓' : '⚠'}</span>
                  <span>{result.ordering_explanation}</span>
                </div>

                {result.evidence_gaps.length > 0 && (
                  <div className="space-y-1.5">
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-[#D97706]">
                      Evidence Gaps
                    </div>
                    {result.evidence_gaps.map((gap, i) => (
                      <div key={i} className="rt-banner-warning text-xs">{gap}</div>
                    ))}
                  </div>
                )}

                {result.uncertainty_summary && (
                  <div className="space-y-1">
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-[#94A3B8]">
                      Uncertainty Notes
                    </div>
                    <p className="text-xs text-[#64748B] leading-relaxed">{result.uncertainty_summary}</p>
                  </div>
                )}
              </>
            )}

            {/* ── Evidence tab ── */}
            {rightTab === 'evidence' && activeEvent && (
              <>
                <div className="space-y-1">
                  <div className="text-[10px] font-semibold uppercase tracking-widest text-[#94A3B8]">Selected Event</div>
                  <select
                    value={activeEventId ?? ''}
                    onChange={e => { const ev = result.events.find(ev => ev.event_id === e.target.value); if (ev) selectEvent(ev) }}
                    className="rt-input text-xs"
                  >
                    {result.events.map((ev, i) => (
                      <option key={ev.event_id} value={ev.event_id}>
                        {String(i + 1).padStart(2, '0')} · {formatTs(ev.timestamp_seconds)} · {EVENT_TYPE_LABELS[ev.event_type] ?? ev.event_type}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="rt-card p-3 space-y-1.5">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`rt-badge text-xs font-semibold ${eventTypeBadge(activeEvent.event_type)}`}>
                      {EVENT_TYPE_LABELS[activeEvent.event_type] ?? activeEvent.event_type}
                    </span>
                    <span className={`rt-badge text-xs font-mono ${confidenceBadge(activeEvent.confidence)}`}>
                      {confidencePct(activeEvent.confidence)}
                    </span>
                    {activeEvent.is_primary_finding && (
                      <span className="rt-badge bg-[#EFF6FF] text-[#2563EB] border border-[#BFDBFE]">Primary</span>
                    )}
                  </div>
                  <p className="text-xs text-[#64748B]">
                    {activeEvent.camera_name ?? `Camera ${activeEvent.recording_id.slice(0, 8)}`}
                    {activeEvent.camera_location ? ` · ${activeEvent.camera_location}` : ''}
                    {' · t='}{formatTs(activeEvent.timestamp_seconds)}
                  </p>
                  <p className="text-sm text-[#0F172A] font-medium">{activeEvent.action_description}</p>
                </div>

                <div className="flex gap-1.5">
                  {(['all', 'observed', 'inferred', 'uncertain'] as EvidenceFilter[]).map(f => (
                    <button
                      key={f}
                      onClick={() => setEvidenceFilter(f)}
                      className={`text-[10px] px-2 py-1 rounded-lg capitalize transition-colors border ${
                        evidenceFilter === f
                          ? 'bg-[#EFF6FF] text-[#2563EB] border-[#BFDBFE]'
                          : 'bg-white text-[#94A3B8] border-[#E2E8F0] hover:text-[#64748B]'
                      }`}
                    >
                      {f}
                    </button>
                  ))}
                </div>

                <div className="space-y-4">
                  <EvidenceGroup label="Observed" color="green" items={visibleObserved} />
                  <EvidenceGroup label="Inferred" color="amber" items={visibleInferred} />
                  <EvidenceGroup label="Uncertain" color="slate" items={visibleUncertain} />
                  {!visibleObserved.length && !visibleInferred.length && !visibleUncertain.length && (
                    <p className="text-xs text-[#94A3B8] text-center py-4">No evidence for this filter.</p>
                  )}
                </div>

                {activeEvent.action_sequence && activeEvent.action_sequence.length > 0 && (
                  <div className="space-y-2">
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-[#94A3B8]">
                      Action Sequence
                    </div>
                    <div className="space-y-1">
                      {activeEvent.action_sequence.map(step => (
                        <div key={step.step} className="flex gap-2 items-baseline text-xs">
                          <span className="text-[#94A3B8] font-mono w-5 shrink-0">{step.step}.</span>
                          <span className="text-[#64748B] flex-1">{step.description}</span>
                          {step.start_seconds != null && step.end_seconds != null && (
                            <span className="text-[#94A3B8] font-mono shrink-0">{formatTs(step.start_seconds)}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {activeEvent.cross_camera_links.length > 0 && (
                  <div className="space-y-2">
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-[#0284C7]">
                      Cross-Camera Links
                    </div>
                    {activeEvent.cross_camera_links.map((lnk, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className={`rt-badge ${lnk.link_type === 'confirmed' ? 'rt-badge-green' : 'rt-badge-amber'}`}>
                          {lnk.link_type}
                        </span>
                        <span className="text-[#64748B] flex-1">
                          {lnk.target_camera_name ?? 'Camera'} · {(lnk.confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}

            {rightTab === 'evidence' && !activeEvent && (
              <p className="text-xs text-[#94A3B8] text-center py-8">
                Select an event from the timeline to view evidence.
              </p>
            )}

            {/* ── Multi-agent tab ── */}
            {rightTab === 'multi-agent' && (
              multiAgentResult ? (
                <>
                  <div className="flex items-center gap-3 text-xs text-[#64748B] bg-[#F8FAFC] rounded-lg px-3 py-2 border border-[#E2E8F0]">
                    <span>{multiAgentResult.agents.filter(a => a.status === 'completed').length}/{multiAgentResult.agents.length} completed</span>
                    <span className="text-[#E2E8F0]">·</span>
                    <span className="tabular-nums">${multiAgentResult.total_cost_usd.toFixed(5)}</span>
                    <span className="text-[#E2E8F0]">·</span>
                    <span className="tabular-nums">{(multiAgentResult.total_duration_ms / 1000).toFixed(2)}s</span>
                  </div>

                  {multiAgentResult.final_report && (
                    <div className="space-y-2">
                      <div className="text-[10px] font-semibold uppercase tracking-widest text-[#94A3B8]">
                        Investigation Narrative
                      </div>
                      <p className="text-xs text-[#0F172A] whitespace-pre-wrap leading-relaxed">
                        {multiAgentResult.final_report.narrative}
                      </p>
                    </div>
                  )}

                  {multiAgentResult.final_report && multiAgentResult.final_report.key_findings.length > 0 && (
                    <div className="space-y-2">
                      <div className="text-[10px] font-semibold uppercase tracking-widest text-[#2563EB]">
                        Key Findings
                      </div>
                      {multiAgentResult.final_report.key_findings.map((f, i) => (
                        <div key={i} className="flex gap-2 items-start">
                          <span className="text-[#2563EB] mt-0.5 shrink-0 text-xs">▸</span>
                          <span className="text-xs text-[#0F172A]">{f}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="space-y-1">
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-[#94A3B8] mb-2">
                      Agent Status
                    </div>
                    {multiAgentResult.agents.map((agent: AgentRunResult, i) => (
                      <div key={i} className="flex items-start gap-2 py-2 border-b border-[#F1F5F9] last:border-0">
                        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium shrink-0 ${STATUS_BADGE[agent.status] ?? STATUS_BADGE.pending}`}>
                          {agent.status}
                        </span>
                        <div className="flex-1 min-w-0">
                          <div className="text-xs text-[#0F172A] truncate">{agent.agent_name}</div>
                          <div className="text-[10px] text-[#94A3B8]">{AGENT_DESCRIPTIONS[agent.agent_name] ?? ''}</div>
                          {agent.error && (
                            <div className="text-[10px] text-[#DC2626] mt-0.5 truncate" title={agent.error}>
                              {agent.error}
                            </div>
                          )}
                        </div>
                        <div className="text-[10px] text-[#94A3B8] text-right shrink-0 tabular-nums">
                          {agent.duration_ms > 0 ? `${(agent.duration_ms / 1000).toFixed(1)}s` : '—'}
                        </div>
                      </div>
                    ))}
                  </div>

                  {multiAgentResult.final_report && multiAgentResult.final_report.uncertainty_notes.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-[10px] font-semibold uppercase tracking-widest text-[#D97706]">
                        Uncertainty Notes
                      </div>
                      {multiAgentResult.final_report.uncertainty_notes.map((n, i) => (
                        <div key={i} className="rt-banner-warning text-xs">{n}</div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <div className="py-8 text-center space-y-2">
                  <p className="text-sm text-[#64748B]">Multi-Agent investigation not run.</p>
                  <p className="text-xs text-[#94A3B8]">
                    Use the Multi-Agent mode in the query form to run 8 specialist agents.
                  </p>
                </div>
              )
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
