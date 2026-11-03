'use client'
import { useState } from 'react'
import type {
  InvestigationResult,
  MultiAgentInvestigationResult,
  VideoAnalysisSummary,
  MatchClassification,
  AgentRunResult,
} from '@/types'

// ── Classification config ──────────────────────────────────────────────────────

const MATCH_CONFIG: Record<
  MatchClassification,
  { label: string; badgeCls: string; dotCls: string; barCls: string }
> = {
  relevant_match: {
    label: 'Relevant',
    badgeCls: 'bg-[#F0FDF4] text-[#16A34A] border border-[#BBF7D0]',
    dotCls: 'bg-[#16A34A]',
    barCls: 'bg-[#16A34A]',
  },
  possible_match: {
    label: 'Possible',
    badgeCls: 'bg-[#FFFBEB] text-[#D97706] border border-[#FDE68A]',
    dotCls: 'bg-[#D97706]',
    barCls: 'bg-[#D97706]',
  },
  no_match: {
    label: 'No match',
    badgeCls: 'bg-[#F1F5F9] text-[#64748B] border border-[#E2E8F0]',
    dotCls: 'bg-[#CBD5E1]',
    barCls: 'bg-[#CBD5E1]',
  },
  needs_review: {
    label: 'Needs review',
    badgeCls: 'bg-[#F0F9FF] text-[#0284C7] border border-[#BAE6FD]',
    dotCls: 'bg-[#0284C7]',
    barCls: 'bg-[#0284C7]',
  },
  analysis_failed: {
    label: 'Analysis failed',
    badgeCls: 'bg-[#FEF2F2] text-[#DC2626] border border-[#FECACA]',
    dotCls: 'bg-[#DC2626]',
    barCls: 'bg-[#DC2626]',
  },
}

const SORT_ORDER: Record<MatchClassification, number> = {
  relevant_match: 0,
  possible_match: 1,
  needs_review: 2,
  no_match: 3,
  analysis_failed: 4,
}

const EXCLUDED_CLASSIFICATIONS: ReadonlySet<MatchClassification> = new Set([
  'no_match',
  'analysis_failed',
])

// ── Helpers ────────────────────────────────────────────────────────────────────

function formatTs(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

// ── VideoCard ──────────────────────────────────────────────────────────────────

interface VideoCardProps {
  summary: VideoAnalysisSummary
  onSelect: (id: string, timestamp: number) => void
}

function VideoCard({ summary, onSelect }: VideoCardProps) {
  const cfg = MATCH_CONFIG[summary.match_classification]
  const thumbnail = summary.thumbnail_path
    ? `/thumbnails/${summary.thumbnail_path.split('/').pop()}`
    : null
  const [thumbFailed, setThumbFailed] = useState(false)

  const firstTimestamp =
    summary.relevant_timestamps.length > 0 ? summary.relevant_timestamps[0] : 0

  const isExcluded =
    summary.match_classification === 'no_match' ||
    summary.match_classification === 'analysis_failed'

  return (
    <div className="bg-white border border-[#E2E8F0] rounded-lg hover:shadow-md transition-shadow flex gap-4 p-4">
      {/* Thumbnail */}
      <div className="flex-shrink-0 w-40 aspect-video rounded-lg overflow-hidden bg-[#F1F5F9] relative">
        {thumbnail && !thumbFailed ? (
          <img
            src={thumbnail}
            alt={summary.filename}
            className="w-full h-full object-cover"
            onError={() => setThumbFailed(true)}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              className="w-8 h-8 text-[#CBD5E1]"
              aria-hidden
            >
              <path
                d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
        )}
        {/* Classification dot overlay */}
        <span
          className={`absolute top-1.5 left-1.5 w-2.5 h-2.5 rounded-full border-2 border-white ${cfg.dotCls}`}
        />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 flex flex-col gap-1.5">
        {/* Top row: camera name + badge */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="text-sm font-bold text-[#0F172A] truncate">
              {summary.camera_name ?? 'Unknown camera'}
            </div>
            <div className="text-xs text-[#94A3B8] truncate">{summary.filename}</div>
          </div>
          <span
            className={`text-[11px] font-semibold px-2 py-0.5 rounded-full shrink-0 ${cfg.badgeCls}`}
          >
            {cfg.label}
          </span>
        </div>

        {/* Confidence bar */}
        {summary.confidence != null && (
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-[#F1F5F9] rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${cfg.barCls}`}
                style={{ width: `${Math.round(summary.confidence * 100)}%` }}
              />
            </div>
            <span className="text-[11px] text-[#94A3B8] font-mono shrink-0">
              {Math.round(summary.confidence * 100)}%
            </span>
          </div>
        )}

        {/* Observation */}
        {summary.observation && (
          <p className="text-xs text-[#64748B] italic leading-snug line-clamp-2">
            {summary.observation}
          </p>
        )}

        {/* Timestamps row */}
        {summary.relevant_timestamps.length > 0 && (
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-[11px] text-[#94A3B8]">Timestamps:</span>
            {summary.relevant_timestamps.slice(0, 4).map(ts => (
              <button
                key={ts}
                onClick={() => onSelect(summary.recording_id, ts)}
                className="text-[11px] text-[#2563EB] hover:underline font-mono"
              >
                {formatTs(ts)}
              </button>
            ))}
            {summary.relevant_timestamps.length > 4 && (
              <span className="text-[11px] text-[#94A3B8]">
                +{summary.relevant_timestamps.length - 4} more
              </span>
            )}
          </div>
        )}

        {/* View Evidence button */}
        {!isExcluded && (
          <div className="mt-auto pt-1">
            <button
              onClick={() => onSelect(summary.recording_id, firstTimestamp)}
              className="inline-flex items-center gap-1 text-xs font-medium text-[#2563EB] hover:text-[#1D4ED8] transition-colors"
            >
              View Evidence
              <svg
                viewBox="0 0 24 24"
                fill="none"
                className="w-3.5 h-3.5"
                aria-hidden
              >
                <path
                  d="M9 18l6-6-6-6"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ── AgentFinding row ───────────────────────────────────────────────────────────

function AgentFindingRow({ agent }: { agent: AgentRunResult }) {
  const [expanded, setExpanded] = useState(false)
  const statusColor =
    agent.status === 'completed'
      ? 'text-[#16A34A]'
      : agent.status === 'failed'
      ? 'text-[#DC2626]'
      : agent.status === 'skipped'
      ? 'text-[#94A3B8]'
      : 'text-[#D97706]'

  const hasOutput = agent.output && Object.keys(agent.output).length > 0

  return (
    <div className="border border-[#E2E8F0] rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between gap-3 px-4 py-3 bg-white hover:bg-[#F8FAFC] transition-colors text-left"
        onClick={() => setExpanded(v => !v)}
        disabled={!hasOutput}
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-semibold text-[#0F172A] truncate">
            {agent.agent_name}
          </span>
          <span className={`text-[11px] font-medium ${statusColor}`}>
            {agent.status}
          </span>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-[11px] text-[#94A3B8]">
            {agent.duration_ms > 0
              ? `${(agent.duration_ms / 1000).toFixed(1)}s`
              : null}
          </span>
          {hasOutput && (
            <svg
              viewBox="0 0 24 24"
              fill="none"
              className={`w-4 h-4 text-[#94A3B8] transition-transform ${expanded ? 'rotate-180' : ''}`}
              aria-hidden
            >
              <path
                d="M6 9l6 6 6-6"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          )}
        </div>
      </button>
      {expanded && hasOutput && (
        <div className="px-4 py-3 bg-[#F8FAFC] border-t border-[#E2E8F0]">
          <pre className="text-[11px] text-[#475569] whitespace-pre-wrap break-words font-mono leading-relaxed">
            {JSON.stringify(agent.output, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

// ── MultiAgentPanel ────────────────────────────────────────────────────────────

interface MultiAgentPanelProps {
  multiAgentResult?: MultiAgentInvestigationResult
  multiAgentLoading?: boolean
}

function MultiAgentPanel({ multiAgentResult, multiAgentLoading }: MultiAgentPanelProps) {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div className="bg-white border border-[#E2E8F0] rounded-lg overflow-hidden">
      {/* Panel header */}
      <button
        className="w-full flex items-center justify-between gap-3 px-5 py-4 hover:bg-[#F8FAFC] transition-colors text-left"
        onClick={() => setCollapsed(v => !v)}
        aria-expanded={!collapsed}
      >
        <div className="flex items-center gap-2">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            className="w-4 h-4 text-[#6366F1]"
            aria-hidden
          >
            <path
              d="M9.75 3.104v.536c0 .895.112 1.79.333 2.66a7.003 7.003 0 003.083 4.224M9.75 3.104c.251.023.501.05.75.082m-3.75 8.286c.24.19.49.363.748.518A12.037 12.037 0 003 20.25h18M14.25 3.104c.251.023.501.05.75.082m0 0a12.037 12.037 0 016 16.563M14.25 3.104v.536c0 .895-.112 1.79-.333 2.66a7.003 7.003 0 01-3.083 4.224"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="text-sm font-semibold text-[#0F172A]">
            AI Investigation Report
          </span>
          {multiAgentLoading && (
            <span className="text-xs text-[#6366F1] font-medium animate-pulse">
              analyzing...
            </span>
          )}
          {multiAgentResult && !multiAgentLoading && (
            <span className="text-[11px] text-[#94A3B8]">
              {multiAgentResult.agents.length} agents
            </span>
          )}
        </div>
        <svg
          viewBox="0 0 24 24"
          fill="none"
          className={`w-4 h-4 text-[#94A3B8] transition-transform ${collapsed ? '' : 'rotate-180'}`}
          aria-hidden
        >
          <path
            d="M6 9l6 6 6-6"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {/* Panel body */}
      {!collapsed && (
        <div className="px-5 py-4 border-t border-[#E2E8F0] space-y-4">
          {multiAgentLoading && (
            <div className="flex items-center gap-3 py-6 justify-center">
              {/* Spinner */}
              <svg
                className="animate-spin w-5 h-5 text-[#6366F1]"
                viewBox="0 0 24 24"
                fill="none"
                aria-hidden
              >
                <circle
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="3"
                  opacity="0.25"
                />
                <path
                  d="M22 12a10 10 0 00-10-10"
                  stroke="currentColor"
                  strokeWidth="3"
                  strokeLinecap="round"
                />
              </svg>
              <span className="text-sm text-[#64748B]">Agents analyzing footage...</span>
            </div>
          )}

          {multiAgentResult && !multiAgentLoading && (
            <>
              {/* Final report narrative */}
              {multiAgentResult.final_report?.narrative && (
                <div className="bg-[#F8FAFC] border border-[#E2E8F0] rounded-lg p-4">
                  <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest mb-2">
                    Summary
                  </p>
                  <p className="text-sm text-[#334155] leading-relaxed">
                    {multiAgentResult.final_report.narrative}
                  </p>
                </div>
              )}

              {/* Key findings */}
              {multiAgentResult.final_report?.key_findings &&
                multiAgentResult.final_report.key_findings.length > 0 && (
                  <div>
                    <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest mb-2">
                      Key Findings
                    </p>
                    <ul className="space-y-1.5">
                      {multiAgentResult.final_report.key_findings.map((f, i) => (
                        <li key={i} className="flex items-start gap-2 text-sm text-[#334155]">
                          <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-[#6366F1] shrink-0" />
                          <span>{f}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

              {/* Agent runs */}
              {multiAgentResult.agents.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest mb-2">
                    Agent Findings
                  </p>
                  <div className="space-y-2">
                    {multiAgentResult.agents.map((agent, i) => (
                      <AgentFindingRow key={i} agent={agent} />
                    ))}
                  </div>
                </div>
              )}

              {/* Cost / duration footer */}
              <div className="flex items-center gap-4 pt-2 border-t border-[#E2E8F0] text-[11px] text-[#94A3B8]">
                <span>
                  {(multiAgentResult.total_duration_ms / 1000).toFixed(1)}s total
                </span>
                <span>${multiAgentResult.total_cost_usd.toFixed(4)} cost</span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  results: InvestigationResult
  onBack: () => void
  onSelectVideo: (id: string, timestamp: number) => void
  multiAgentResult?: MultiAgentInvestigationResult
  multiAgentLoading?: boolean
  query?: string
}

export default function InvestigationVideoList({
  results,
  onBack,
  onSelectVideo,
  multiAgentResult,
  multiAgentLoading,
  query,
}: Props) {
  const [showExcluded, setShowExcluded] = useState(false)

  const summaries = results.video_summaries ?? []
  const displayQuery = query ?? results.query

  // Partition
  const relevantSummaries = summaries.filter(
    s => !EXCLUDED_CLASSIFICATIONS.has(s.match_classification),
  )
  const excludedSummaries = summaries.filter(s =>
    EXCLUDED_CLASSIFICATIONS.has(s.match_classification),
  )

  // Counts
  const relevantCount = summaries.filter(
    s => s.match_classification === 'relevant_match',
  ).length
  const possibleCount = summaries.filter(
    s => s.match_classification === 'possible_match',
  ).length
  const excludedCount = excludedSummaries.length

  const sortFn = (a: VideoAnalysisSummary, b: VideoAnalysisSummary) =>
    SORT_ORDER[a.match_classification] - SORT_ORDER[b.match_classification]

  const sortedVisible = [...relevantSummaries].sort(sortFn)
  const sortedExcluded = [...excludedSummaries].sort(sortFn)

  const allExcluded = summaries.length > 0 && relevantSummaries.length === 0

  const showMultiAgent = !!(multiAgentResult || multiAgentLoading)

  return (
    <div className="flex flex-col min-h-screen bg-[#F8FAFC]">

      {/* ── Header / breadcrumb ── */}
      <header className="bg-white border-b border-[#E2E8F0] shadow-sm shrink-0 px-5 py-3">
        <div className="max-w-4xl mx-auto flex items-center gap-2">
          <button
            onClick={onBack}
            className="inline-flex items-center gap-1.5 text-sm text-[#64748B] hover:text-[#0F172A] transition-colors font-medium"
            aria-label="Back to overview"
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4" aria-hidden>
              <path
                d="M19 12H5M5 12l7-7M5 12l7 7"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Overview
          </button>
          <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4 text-[#CBD5E1]" aria-hidden>
            <path d="M9 18l6-6-6-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <span className="text-sm font-semibold text-[#0F172A]">Investigation Results</span>
        </div>
      </header>

      {/* ── Page content ── */}
      <main className="flex-1 max-w-4xl mx-auto w-full px-5 py-6 space-y-5">

        {/* Query box */}
        {displayQuery && (
          <div className="bg-[#EFF6FF] border border-[#BFDBFE] rounded-lg px-4 py-3 flex items-start gap-3">
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4 text-[#2563EB] mt-0.5 shrink-0" aria-hidden>
              <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
              <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            <div>
              <span className="text-xs font-semibold text-[#2563EB] uppercase tracking-wider">
                Searching for
              </span>
              <p className="text-sm font-medium text-[#1E40AF] mt-0.5">{displayQuery}</p>
            </div>
          </div>
        )}

        {/* Stats bar */}
        <div className="flex items-center flex-wrap gap-x-4 gap-y-1 text-sm text-[#64748B]">
          <span className="font-medium text-[#0F172A]">
            {results.searched_recordings} recording{results.searched_recordings !== 1 ? 's' : ''} analyzed
          </span>
          <span className="text-[#E2E8F0]">·</span>
          <span>
            <span className="font-medium text-[#16A34A]">{relevantCount}</span> relevant
          </span>
          <span className="text-[#E2E8F0]">·</span>
          <span>
            <span className="font-medium text-[#D97706]">{possibleCount}</span> possible match
          </span>
          <span className="text-[#E2E8F0]">·</span>
          <span>
            <span className="font-medium text-[#64748B]">{excludedCount}</span> excluded
          </span>
        </div>

        {/* Legend + toggle row */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-4 flex-wrap">
            {(
              [
                'relevant_match',
                'possible_match',
                'no_match',
              ] as MatchClassification[]
            ).map(key => {
              const cfg = MATCH_CONFIG[key]
              return (
                <div key={key} className="flex items-center gap-1.5 text-[11px] text-[#64748B]">
                  <span className={`w-2 h-2 rounded-full ${cfg.dotCls}`} />
                  <span>{cfg.label}</span>
                </div>
              )
            })}
          </div>
          {excludedCount > 0 && (
            <button
              onClick={() => setShowExcluded(v => !v)}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-colors font-medium ${
                showExcluded
                  ? 'bg-[#F1F5F9] text-[#0F172A] border-[#CBD5E1]'
                  : 'bg-white text-[#64748B] border-[#E2E8F0] hover:bg-[#F8FAFC]'
              }`}
              data-testid="show-excluded-toggle"
            >
              {showExcluded ? 'Hide' : 'Show'} excluded recordings ({excludedCount})
            </button>
          )}
        </div>

        {/* ── Results ── */}
        {summaries.length === 0 ? (
          /* Empty state */
          <div className="bg-white border border-[#E2E8F0] rounded-lg flex flex-col items-center justify-center py-16 gap-3">
            <svg viewBox="0 0 24 24" fill="none" className="w-10 h-10 text-[#E2E8F0]" aria-hidden>
              <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
              <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            <p className="text-sm font-semibold text-[#64748B]">
              No matching footage found for this query
            </p>
            <p className="text-xs text-[#94A3B8] text-center max-w-sm">
              No indexed recordings matched your search. Try rephrasing the query or adding more footage.
            </p>
          </div>
        ) : allExcluded ? (
          /* All excluded — no relevant matches */
          <div
            className="bg-white border border-[#E2E8F0] rounded-lg flex flex-col items-center justify-center py-16 gap-3"
            data-testid="all-excluded-empty-state"
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-10 h-10 text-[#E2E8F0]" aria-hidden>
              <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
              <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            <p className="text-sm font-semibold text-[#64748B]">
              No matching footage found for this query
            </p>
            <p className="text-xs text-[#94A3B8] text-center max-w-sm">
              {summaries.length} recording{summaries.length !== 1 ? 's' : ''} analyzed — none matched the query.
            </p>
            <button
              onClick={() => setShowExcluded(true)}
              className="text-xs text-[#2563EB] hover:underline font-medium"
              data-testid="show-excluded-from-empty"
            >
              View all analyzed recordings
            </button>
          </div>
        ) : (
          /* Normal results grid */
          <div className="space-y-3" data-testid="results-grid">
            {sortedVisible.map(summary => (
              <VideoCard key={summary.recording_id} summary={summary} onSelect={onSelectVideo} />
            ))}
          </div>
        )}

        {/* Excluded recordings section */}
        {showExcluded && sortedExcluded.length > 0 && (
          <div className="space-y-3" data-testid="excluded-videos-section">
            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-[#E2E8F0]" />
              <span className="text-[11px] font-semibold text-[#94A3B8] uppercase tracking-widest">
                Excluded — No match ({sortedExcluded.length})
              </span>
              <div className="h-px flex-1 bg-[#E2E8F0]" />
            </div>
            <div className="space-y-3">
              {sortedExcluded.map(summary => (
                <VideoCard key={summary.recording_id} summary={summary} onSelect={onSelectVideo} />
              ))}
            </div>
          </div>
        )}

        {/* Multi-agent panel */}
        {showMultiAgent && (
          <MultiAgentPanel
            multiAgentResult={multiAgentResult}
            multiAgentLoading={multiAgentLoading}
          />
        )}
      </main>
    </div>
  )
}
