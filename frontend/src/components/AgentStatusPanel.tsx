'use client'
import type { AgentRunResult, MultiAgentInvestigationResult } from '@/types'

const STATUS_CONFIG: Record<string, { cls: string; label: string }> = {
  completed: { cls: 'rt-badge-green',  label: 'completed' },
  failed:    { cls: 'rt-badge-red',    label: 'failed'    },
  skipped:   { cls: 'rt-badge-amber',  label: 'skipped'   },
  timed_out: { cls: 'rt-badge-amber',  label: 'timed_out' },
  running:   { cls: 'rt-badge-blue',   label: 'running'   },
  pending:   { cls: 'rt-badge-gray',   label: 'pending'   },
  mocked:    { cls: 'rt-badge-gray',   label: 'mocked'    },
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

interface Props {
  result: MultiAgentInvestigationResult
}

function AgentRow({ agent }: { agent: AgentRunResult }) {
  const cfg = STATUS_CONFIG[agent.status] ?? STATUS_CONFIG.pending
  const ms = agent.duration_ms > 0 ? `${(agent.duration_ms / 1000).toFixed(2)}s` : '—'
  const cost = agent.cost_usd > 0 ? `$${agent.cost_usd.toFixed(5)}` : '—'
  const reason = agent.error ?? (
    agent.output && typeof agent.output.skipped_reason === 'string'
      ? agent.output.skipped_reason : null
  )

  return (
    <div className="flex items-start gap-3 py-2.5 border-b border-[#E2E8F0] last:border-0">
      <span className={cfg.cls}>{cfg.label}</span>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-[#0F172A]">{agent.agent_name}</div>
        <div className="text-xs text-[#94A3B8]">{AGENT_DESCRIPTIONS[agent.agent_name] ?? ''}</div>
        {reason && (
          <div className={`mt-1 text-xs rounded px-2 py-1 ${
            agent.status === 'failed' || agent.status === 'timed_out'
              ? 'rt-banner-danger'
              : 'rt-banner-warning'
          }`} title={reason}>
            {reason}
          </div>
        )}
      </div>
      <div className="text-xs text-[#94A3B8] text-right shrink-0 tabular-nums space-y-0.5">
        <div>{ms}</div>
        <div>{cost}</div>
      </div>
    </div>
  )
}

export default function AgentStatusPanel({ result }: Props) {
  const completed = result.agents.filter(a => a.status === 'completed').length
  const failed    = result.agents.filter(a => a.status === 'failed').length
  const skipped   = result.agents.filter(a => ['skipped', 'timed_out'].includes(a.status)).length

  return (
    <div className="rt-card p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="rt-section-title">Investigation Agents</h3>
        <div className="flex items-center gap-3 text-xs text-[#64748B]">
          <span>{completed}/{result.agents.length} completed</span>
          {failed > 0 && <span className="text-[#DC2626]">{failed} failed</span>}
          {skipped > 0 && <span className="text-[#D97706]">{skipped} skipped</span>}
          <span className="tabular-nums">${result.total_cost_usd.toFixed(5)}</span>
          <span className="tabular-nums">{(result.total_duration_ms / 1000).toFixed(2)}s</span>
        </div>
      </div>

      {/* Agent rows */}
      <div>
        {result.agents.map((agent, i) => <AgentRow key={i} agent={agent} />)}
      </div>

      {/* Key findings */}
      {result.final_report && (
        <div className="pt-2 border-t border-[#E2E8F0] space-y-3">
          {result.final_report.key_findings.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-[#64748B] uppercase tracking-wide mb-2">Key Findings</p>
              <ul className="space-y-1.5">
                {result.final_report.key_findings.map((f, i) => (
                  <li key={i} className="text-sm text-[#0F172A] flex gap-2">
                    <span className="text-[#2563EB] shrink-0 mt-0.5">▸</span>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {result.final_report.uncertainty_notes.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-[#D97706] uppercase tracking-wide mb-2">Uncertainty Notes</p>
              {result.final_report.uncertainty_notes.map((n, i) => (
                <div key={i} className="rt-banner-warning text-xs mb-1.5">{n}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
