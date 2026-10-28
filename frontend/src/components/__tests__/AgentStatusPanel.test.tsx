import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import AgentStatusPanel from '../AgentStatusPanel'
import type { AgentRunResult, MultiAgentInvestigationResult } from '@/types'

function agent(overrides: Partial<AgentRunResult> & { agent_name: string }): AgentRunResult {
  return {
    status: 'completed',
    duration_ms: 1200,
    tokens_used: 0,
    cost_usd: 0.001,
    ...overrides,
  }
}

function resultWith(agents: AgentRunResult[]): MultiAgentInvestigationResult {
  return {
    trace_id: 'trace-1',
    investigation_id: 'inv-1',
    query: 'person at entrance',
    agents,
    final_report: null,
    total_cost_usd: 0.008,
    total_duration_ms: 9500,
    status: 'COMPLETE',
  }
}

const ALL_EIGHT = [
  'QueryInvestigationAgent',
  'VideoIntelligenceAgent',
  'ObjectActionAnalystAgent',
  'TemporalReasoningAgent',
  'CrossCameraCorrelationAgent',
  'ContradictionHunterAgent',
  'EvidenceAuditorAgent',
  'ReportSynthesisAgent',
]

test('renders truthful per-agent statuses including timed_out and mocked', () => {
  const agents = ALL_EIGHT.map((name, i) =>
    agent({
      agent_name: name,
      status: i === 5 ? 'timed_out' : i === 6 ? 'mocked' : 'completed',
      error: i === 5 ? 'Agent exceeded its 30s execution timeout' : undefined,
    }),
  )
  render(<AgentStatusPanel result={resultWith(agents)} />)

  // Honest counts: only truly completed agents count as completed
  expect(screen.getByText('6/8 completed')).toBeInTheDocument()
  expect(screen.getByText('timed_out')).toBeInTheDocument()
  expect(screen.getByText('mocked')).toBeInTheDocument()
  // Timeout reason is surfaced, not hidden
  expect(screen.getByText(/exceeded its 30s execution timeout/)).toBeInTheDocument()
  // No failed banner when nothing failed
  expect(screen.queryByText(/failed/)).not.toBeInTheDocument()
})

test('shows failed count and error text when an agent fails', () => {
  const agents = ALL_EIGHT.map((name, i) =>
    agent({
      agent_name: name,
      status: i === 2 ? 'failed' : 'completed',
      error: i === 2 ? 'model returned malformed JSON' : undefined,
    }),
  )
  render(<AgentStatusPanel result={resultWith(agents)} />)

  expect(screen.getByText('7/8 completed')).toBeInTheDocument()
  expect(screen.getByText('1 failed')).toBeInTheDocument()
  expect(screen.getByText(/malformed JSON/)).toBeInTheDocument()
})

test('renders key findings and uncertainty notes without asserting prose content', () => {
  const base = resultWith(ALL_EIGHT.map((name) => agent({ agent_name: name })))
  base.final_report = {
    narrative: 'narrative text',
    key_findings: ['finding one (score=0.90, t=12.0s)', 'finding two (score=0.80, t=34.0s)'],
    uncertainty_notes: ['1 claim(s) could not be verified against source evidence'],
    evidence_citations: ['e1@rec-1:12.0s'],
    model_used: 'mock-v1',
    agents_run: ALL_EIGHT,
  }
  render(<AgentStatusPanel result={base} />)

  expect(screen.getByText('Key Findings')).toBeInTheDocument()
  expect(screen.getByText(/finding one/)).toBeInTheDocument()
  expect(screen.getByText(/finding two/)).toBeInTheDocument()
  expect(screen.getByText('Uncertainty Notes')).toBeInTheDocument()
  expect(screen.getByText(/could not be verified/)).toBeInTheDocument()
})

test('handles missing final report without crashing', () => {
  const agents = ALL_EIGHT.map((name) => agent({ agent_name: name, status: 'skipped' as const }))
  render(<AgentStatusPanel result={resultWith(agents)} />)

  expect(screen.getByText('0/8 completed')).toBeInTheDocument()
  expect(screen.queryByText('Key Findings')).not.toBeInTheDocument()
})
