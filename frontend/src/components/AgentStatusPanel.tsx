'use client'
import type { AgentRunResult, FinalReport, MultiAgentInvestigationResult } from '@/types'

const STATUS_STYLES: Record<string, string> = {
  completed: 'bg-green-100 text-green-800',
  skipped: 'bg-yellow-100 text-yellow-700',
  failed: 'bg-red-100 text-red-700',
  running: 'bg-blue-100 text-blue-700',
  pending: 'bg-gray-100 text-gray-500',
  mocked: 'bg-purple-100 text-purple-700',
  timed_out: 'bg-orange-100 text-orange-700',
}

const AGENT_DESCRIPTIONS: Record<string, string> = {
  VideoIntelligenceAgent: 'Visual summary per event',
  ObjectActionAnalystAgent: 'Objects & actions identification',
  TemporalReasoningAgent: 'Timeline normalization & gap detection',
  CrossCameraCorrelationAgent: 'Cross-camera candidate links',
  QueryInvestigationAgent: 'Evidence retrieval & ranking',
  ContradictionHunterAgent: 'Inconsistency & contradiction detection',
  EvidenceAuditorAgent: 'Claim verification against source evidence',
  ReportSynthesisAgent: 'Final narrative assembly',
}

interface Props {
  result: MultiAgentInvestigationResult
}

function AgentRow({ agent }: { agent: AgentRunResult }) {
  const style = STATUS_STYLES[agent.status] ?? STATUS_STYLES.pending
  const ms = agent.duration_ms > 0 ? `${(agent.duration_ms / 1000).toFixed(2)}s` : '—'
  const cost = agent.cost_usd > 0 ? `$${agent.cost_usd.toFixed(5)}` : '—'

  return (
    <div className="flex items-start gap-3 py-2 border-b border-gray-100 last:border-0">
      <span className={`mt-0.5 inline-block px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${style}`}>
        {agent.status}
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-gray-800">{agent.agent_name}</div>
        <div className="text-xs text-gray-500">
          {AGENT_DESCRIPTIONS[agent.agent_name] ?? ''}
        </div>
        {agent.error && (
          <div className="mt-1 text-xs text-red-600 bg-red-50 rounded px-2 py-1 truncate" title={agent.error}>
            {agent.error}
          </div>
        )}
      </div>
      <div className="text-xs text-gray-400 text-right whitespace-nowrap">
        <div>{ms}</div>
        <div>{cost}</div>
      </div>
    </div>
  )
}

export default function AgentStatusPanel({ result }: Props) {
  const completed = result.agents.filter(a => a.status === 'completed').length
  const failed = result.agents.filter(a => a.status === 'failed').length
  const totalCost = result.total_cost_usd

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-800">Investigation Agents</h3>
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span>{completed}/{result.agents.length} completed</span>
          {failed > 0 && <span className="text-red-500">{failed} failed</span>}
          <span>${totalCost.toFixed(5)}</span>
          <span>{(result.total_duration_ms / 1000).toFixed(2)}s</span>
        </div>
      </div>

      <div className="divide-y divide-gray-50">
        {result.agents.map((agent, i) => (
          <AgentRow key={i} agent={agent} />
        ))}
      </div>

      {result.final_report && (
        <div className="mt-4 pt-4 border-t border-gray-100">
          <div className="text-xs font-semibold text-gray-600 mb-2">Key Findings</div>
          {result.final_report.key_findings.length > 0 ? (
            <ul className="space-y-1">
              {result.final_report.key_findings.map((f, i) => (
                <li key={i} className="text-xs text-gray-700 flex gap-2">
                  <span className="text-indigo-400 shrink-0">▸</span>
                  <span>{f}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-gray-400">No key findings extracted.</p>
          )}

          {result.final_report.uncertainty_notes.length > 0 && (
            <div className="mt-3">
              <div className="text-xs font-semibold text-gray-600 mb-1">Uncertainty Notes</div>
              {result.final_report.uncertainty_notes.map((n, i) => (
                <p key={i} className="text-xs text-amber-700 bg-amber-50 rounded px-2 py-1 mt-1">{n}</p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
