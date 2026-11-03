'use client'
import { useState } from 'react'
import type { GuardianAlert, Event, Recording } from '@/types'
import VideoPlayerModal from './VideoPlayerModal'

// ── Types ─────────────────────────────────────────────────────────────────────

type ChatTab = 'police' | 'owner'

interface ChatMessage {
  id: string
  timestamp_seconds: number
  body: string
  isSerious: boolean       // drives red left-border for police tab
  evidenceTs?: number      // opens VideoPlayerModal at this second
  source: 'alert' | 'event' | 'digest'
}

interface Props {
  alerts: GuardianAlert[]
  events: Event[]
  recording: Recording
}

// ── Police relevance policy ───────────────────────────────────────────────────

function isPoliceRelevantAlert(alert: GuardianAlert): boolean {
  const { hazard_category: cat, severity: sev } = alert
  if (cat === 'weapon_visible') return true
  if (cat === 'fire_smoke' && (sev === 'critical' || sev === 'high')) return true
  if (cat === 'dangerous_animal' && (sev === 'critical' || sev === 'high')) return true
  if (cat === 'other_hazard' && (sev === 'critical' || sev === 'high')) return true
  if (cat === 'possible-assault' as string) return true
  if (cat === 'possible-theft' as string) return true
  return false
}

function isPoliceRelevantEvent(ev: Event): boolean {
  const serious = ['possible-theft', 'possible-assault', 'confrontation',
                   'suspicious-approach-departure']
  return serious.includes(ev.event_type) && ev.confidence >= 0.6
}

function isDuplicatedByAlert(ev: Event, alerts: GuardianAlert[]): boolean {
  return alerts.some(a => Math.abs(a.timestamp_seconds - ev.timestamp_seconds) < 5)
}

// ── Formatting helpers ────────────────────────────────────────────────────────

function fmtTs(secs: number): string {
  if (!isFinite(secs)) return '0:00'
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function fmtTime(secs: number, recDatetime?: string): string {
  if (recDatetime) {
    try {
      const base = new Date(recDatetime).getTime()
      const d = new Date(base + secs * 1000)
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    } catch { /* fall through */ }
  }
  return fmtTs(secs)
}

const CATEGORY_LABELS: Record<string, string> = {
  fire_smoke:            'Fire / Smoke Detected',
  person_fall_injury:    'Person Fall / Injury',
  vehicle_collision:     'Vehicle Collision',
  dangerous_animal:      'Dangerous Animal Detected',
  weapon_visible:        'Weapon Visible',
  restricted_area_entry: 'Restricted Area Entry',
  other_hazard:          'Hazard Detected',
  'possible-theft':      'Possible Theft / Snatching',
  'possible-assault':    'Possible Assault',
  confrontation:         'Confrontation Detected',
  'suspicious-approach-departure': 'Suspicious Approach / Departure',
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  person_detected:               'Person detected',
  vehicle_detected:              'Vehicle detected',
  'possible-theft':              'Possible theft / snatching',
  'possible-assault':            'Possible assault',
  confrontation:                 'Confrontation',
  'suspicious-approach-departure': 'Suspicious activity',
  'sudden-fall':                 'Sudden fall',
  'other-anomaly':               'Unusual activity',
  narrative_step:                'Activity recorded',
}

// ── Message builders ──────────────────────────────────────────────────────────

function buildPoliceMessages(
  alerts: GuardianAlert[],
  events: Event[],
  recording: Recording,
): ChatMessage[] {
  const msgs: ChatMessage[] = []
  const cameraLabel = recording.camera_name || recording.original_filename

  // From guardian alerts
  for (const alert of alerts) {
    if (!isPoliceRelevantAlert(alert)) continue
    const catLabel = CATEGORY_LABELS[alert.hazard_category] ?? 'Hazard Detected'
    const emoji = alert.hazard_category === 'fire_smoke' ? '🔥'
      : alert.hazard_category === 'weapon_visible' ? '⚠️'
      : alert.hazard_category === 'dangerous_animal' ? '🐾'
      : '🚨'
    const timeLabel = fmtTime(alert.timestamp_seconds, recording.recording_datetime)
    const rangeLabel = `${fmtTs(alert.time_range_start)}–${fmtTs(alert.time_range_end)}`
    const body = [
      `${emoji} *${catLabel}*`,
      '',
      `📍 Camera: ${cameraLabel}`,
      `🕐 Time: ${timeLabel}`,
      '',
      alert.description,
      '',
      `Evidence: ${rangeLabel}`,
      '',
      '_Simulation alert — please review the footage._',
    ].join('\n')

    msgs.push({
      id: `alert-${alert.alert_id}`,
      timestamp_seconds: alert.timestamp_seconds,
      body,
      isSerious: alert.severity === 'critical' || alert.severity === 'high',
      evidenceTs: alert.time_range_start,
      source: 'alert',
    })
  }

  // From events — only if not already covered by an alert
  const policeAlerts = alerts.filter(isPoliceRelevantAlert)
  for (const ev of events) {
    if (!isPoliceRelevantEvent(ev)) continue
    if (isDuplicatedByAlert(ev, policeAlerts)) continue
    const catLabel = CATEGORY_LABELS[ev.event_type] ?? 'Suspicious Activity'
    const timeLabel = fmtTime(ev.timestamp_seconds, recording.recording_datetime)
    const rangeLabel = ev.start_seconds != null && ev.end_seconds != null
      ? `${fmtTs(ev.start_seconds)}–${fmtTs(ev.end_seconds)}`
      : fmtTs(ev.timestamp_seconds)
    const body = [
      `🚨 *${catLabel}*`,
      '',
      `📍 Camera: ${cameraLabel}`,
      `🕐 Time: ${timeLabel}`,
      '',
      ev.action_description || ev.observed_facts.slice(0, 2).join('. '),
      '',
      `Evidence: ${rangeLabel}`,
      '',
      '_Simulation alert — please review the footage._',
    ].join('\n')

    msgs.push({
      id: `event-${ev.event_id}`,
      timestamp_seconds: ev.timestamp_seconds,
      body,
      isSerious: ev.confidence >= 0.75,
      evidenceTs: ev.start_seconds ?? ev.timestamp_seconds,
      source: 'event',
    })
  }

  return msgs.sort((a, b) => a.timestamp_seconds - b.timestamp_seconds)
}

function buildOwnerMessages(
  alerts: GuardianAlert[],
  events: Event[],
  recording: Recording,
): ChatMessage[] {
  const msgs: ChatMessage[] = []

  // Build a unified event list tagged with seriousness
  type RawItem = { ts: number; label: string; isAlert: boolean; evidenceTs: number }
  const raw: RawItem[] = []

  for (const ev of events) {
    const label = EVENT_TYPE_LABELS[ev.event_type] ?? ev.event_type.replace(/-/g, ' ')
    raw.push({ ts: ev.timestamp_seconds, label, isAlert: false, evidenceTs: ev.timestamp_seconds })
  }
  // Guardian alerts override same-ts events in display but are additive for owner
  for (const alert of alerts) {
    const catLabel = CATEGORY_LABELS[alert.hazard_category] ?? 'Hazard'
    raw.push({ ts: alert.timestamp_seconds, label: `⚠️ ${catLabel}`, isAlert: true, evidenceTs: alert.time_range_start })
  }

  raw.sort((a, b) => a.ts - b.ts)

  // Group into 60-second digest windows
  const windows: RawItem[][] = []
  let current: RawItem[] = []
  let windowStart = -Infinity
  for (const item of raw) {
    if (item.ts - windowStart > 60) {
      if (current.length) windows.push(current)
      current = [item]
      windowStart = item.ts
    } else {
      current.push(item)
    }
  }
  if (current.length) windows.push(current)

  for (const window of windows) {
    const firstTs = window[0].ts
    const hasAlert = window.some(i => i.isAlert)
    const timeLabel = fmtTime(firstTs, recording.recording_datetime)
    const lines = window.map(i => `${fmtTime(i.ts, recording.recording_datetime)} — ${i.label}`)
    const body = [
      `📹 *Camera activity — ${timeLabel}*`,
      '',
      ...lines,
    ].join('\n')

    msgs.push({
      id: `digest-${firstTs}`,
      timestamp_seconds: firstTs,
      body,
      isSerious: hasAlert,
      evidenceTs: window[0].evidenceTs,
      source: 'digest',
    })
  }

  return msgs
}

// ── Bubble renderer ───────────────────────────────────────────────────────────

function MessageBubble({
  msg,
  onEvidenceClick,
}: {
  msg: ChatMessage
  onEvidenceClick: (ts: number) => void
}) {
  const lines = msg.body.split('\n')
  return (
    <div className="flex justify-end mb-3">
      <div
        className={`max-w-[85%] rounded-2xl rounded-tr-sm px-4 py-3 shadow-sm relative
          ${msg.isSerious
            ? 'bg-[#FFF0F0] border-l-4 border-[#DC2626]'
            : 'bg-[#DCF8C6]'
          }`}
      >
        {lines.map((line, i) => {
          if (line.startsWith('_') && line.endsWith('_')) {
            return (
              <p key={i} className="text-[11px] text-[#64748B] italic mt-1">
                {line.slice(1, -1)}
              </p>
            )
          }
          if (line.startsWith('*') && line.endsWith('*')) {
            return (
              <p key={i} className="text-sm font-semibold text-[#0F172A] leading-snug">
                {line.slice(1, -1)}
              </p>
            )
          }
          if (line.startsWith('Evidence:') && msg.evidenceTs != null) {
            const parts = line.replace('Evidence: ', '')
            return (
              <p key={i} className="text-xs mt-1">
                <span className="text-[#64748B]">Evidence: </span>
                <button
                  onClick={() => onEvidenceClick(msg.evidenceTs!)}
                  className="text-[#2563EB] underline underline-offset-2 hover:text-[#1D4ED8]"
                >
                  {parts}
                </button>
              </p>
            )
          }
          if (line === '') return <div key={i} className="h-1" />
          return (
            <p key={i} className="text-sm text-[#1E293B] leading-relaxed">
              {line}
            </p>
          )
        })}
        {/* WhatsApp-style tail */}
        <div className="text-[10px] text-[#94A3B8] text-right mt-1">{fmtTs(msg.timestamp_seconds)}</div>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function GuardianWhatsApp({ alerts, events, recording }: Props) {
  const [tab, setTab] = useState<ChatTab>('police')
  const [activeTs, setActiveTs] = useState<number | null>(null)

  const policeMessages = buildPoliceMessages(alerts, events, recording)
  const ownerMessages = buildOwnerMessages(alerts, events, recording)
  const messages = tab === 'police' ? policeMessages : ownerMessages

  const handleEvidenceClick = (ts: number) => setActiveTs(ts)

  return (
    <div className="rt-card overflow-hidden">
      {/* Simulation disclaimer */}
      <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 flex items-center gap-2">
        <span className="text-amber-600 text-xs font-medium">⚠️ Simulation Only</span>
        <span className="text-amber-700 text-xs">No real messages are sent. This demonstrates how Guardian would notify responders.</span>
      </div>

      {/* Tab bar */}
      <div className="flex border-b border-[#E2E8F0]">
        {(['police', 'owner'] as ChatTab[]).map(t => {
          const label = t === 'police' ? '🚔 Police' : '📱 Owner'
          const count = t === 'police' ? policeMessages.length : ownerMessages.length
          return (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 text-xs font-medium py-3 transition-colors border-b-2 ${
                tab === t
                  ? 'border-[#2563EB] text-[#2563EB] bg-[#EFF6FF]/40'
                  : 'border-transparent text-[#64748B] hover:text-[#0F172A] hover:bg-[#F8FAFC]'
              }`}
            >
              {label}
              {count > 0 && (
                <span className={`ml-1.5 inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] font-bold
                  ${tab === t ? 'bg-[#2563EB] text-white' : 'bg-[#E2E8F0] text-[#64748B]'}`}>
                  {count}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Chat header */}
      <div className="flex items-center gap-3 px-4 py-3 bg-[#075E54] text-white">
        <div className="w-8 h-8 rounded-full bg-white/20 flex items-center justify-center text-base">
          {tab === 'police' ? '🚔' : '👤'}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold leading-tight">
            {tab === 'police' ? 'Police Control Room' : 'Property Owner'}
          </div>
          <div className="text-[11px] text-green-200 leading-tight">● Simulation</div>
        </div>
        <div className="text-[11px] text-white/60 shrink-0">
          {tab === 'police' ? 'Serious incidents only' : 'All camera activity'}
        </div>
      </div>

      {/* Chat area */}
      <div
        className="px-4 py-4 overflow-y-auto min-h-[280px] max-h-[520px]"
        style={{ background: '#ECE5DD' }}
      >
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 gap-2">
            <div className="w-10 h-10 rounded-full bg-white/60 flex items-center justify-center text-2xl">
              {tab === 'police' ? '🛡️' : '📭'}
            </div>
            <p className="text-sm text-[#64748B] font-medium">
              {tab === 'police'
                ? 'No serious incidents to report'
                : 'No camera activity recorded yet'}
            </p>
            <p className="text-xs text-[#94A3B8] text-center max-w-52">
              {tab === 'police'
                ? 'Guardian found no police-relevant incidents in this recording.'
                : 'Events will appear here once the recording is indexed.'}
            </p>
          </div>
        ) : (
          messages.map(msg => (
            <MessageBubble
              key={msg.id}
              msg={msg}
              onEvidenceClick={handleEvidenceClick}
            />
          ))
        )}
      </div>

      {/* Evidence modal */}
      {activeTs != null && (
        <VideoPlayerModal
          recording={recording}
          initialTimestamp={activeTs}
          onClose={() => setActiveTs(null)}
        />
      )}
    </div>
  )
}
