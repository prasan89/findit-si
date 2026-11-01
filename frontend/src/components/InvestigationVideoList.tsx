'use client'
import { useState } from 'react'
import type { InvestigationResult, VideoAnalysisSummary, MatchClassification, Recording } from '@/types'

// ── Classification config ──────────────────────────────────────────────────────

const MATCH_CONFIG: Record<MatchClassification, {
  label: string
  badgeCls: string
  dotCls: string
}> = {
  relevant_match:  { label: 'Relevant match',  badgeCls: 'bg-[#F0FDF4] text-[#16A34A] border border-[#BBF7D0]', dotCls: 'bg-[#16A34A]' },
  possible_match:  { label: 'Possible match',  badgeCls: 'bg-[#FFFBEB] text-[#D97706] border border-[#FDE68A]', dotCls: 'bg-[#D97706]' },
  no_match:        { label: 'No match found',  badgeCls: 'bg-[#F1F5F9] text-[#64748B] border border-[#E2E8F0]', dotCls: 'bg-[#CBD5E1]' },
  needs_review:    { label: 'Needs review',    badgeCls: 'bg-[#F0F9FF] text-[#0284C7] border border-[#BAE6FD]', dotCls: 'bg-[#0284C7]' },
  analysis_failed: { label: 'Analysis failed', badgeCls: 'bg-[#FEF2F2] text-[#DC2626] border border-[#FECACA]', dotCls: 'bg-[#DC2626]' },
}

// Classifications shown by default (excluded = hidden unless toggle is on)
const EXCLUDED_BY_DEFAULT: ReadonlySet<MatchClassification> = new Set([
  'no_match',
  'analysis_failed',
])

function formatDuration(secs?: number | null): string {
  if (secs == null) return '—'
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatTs(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

// ── Summary row ────────────────────────────────────────────────────────────────

interface VideoRowProps {
  summary: VideoAnalysisSummary
  recording?: Recording
  onOpen: (summary: VideoAnalysisSummary) => void
}

function VideoRow({ summary, recording, onOpen }: VideoRowProps) {
  const cfg = MATCH_CONFIG[summary.match_classification]
  // Thumbnail path from DB is "thumbnails/<uuid>.jpg" — proxy maps /thumbnails/* to backend
  const thumbnail = summary.thumbnail_path
    ? `/thumbnails/${summary.thumbnail_path.split('/').pop()}`
    : null

  return (
    <div className="rt-card p-4 flex gap-4 hover:shadow-md transition-shadow">
      {/* Thumbnail */}
      <button
        onClick={() => onOpen(summary)}
        className="flex-shrink-0 w-28 h-20 rounded-lg overflow-hidden bg-[#F1F5F9] relative focus:outline-none focus:ring-2 focus:ring-[#93C5FD]"
        aria-label={`Open video ${summary.filename}`}
      >
        {thumbnail ? (
          <img
            src={thumbnail}
            alt={summary.filename}
            className="w-full h-full object-cover"
            onError={e => {
              const el = e.target as HTMLImageElement
              el.style.display = 'none'
              const parent = el.parentElement
              if (parent) parent.setAttribute('data-no-thumb', 'true')
            }}
          />
        ) : null}
        {/* Fallback icon shown when no thumbnail or load fails */}
        <div className="absolute inset-0 flex items-center justify-center opacity-0 [data-no-thumb='true']_&:opacity-100 pointer-events-none" aria-hidden>
          <svg viewBox="0 0 24 24" fill="none" className="w-8 h-8 text-[#CBD5E1]">
            <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </div>
        {!thumbnail && (
          <div className="absolute inset-0 flex items-center justify-center">
            <svg viewBox="0 0 24 24" fill="none" className="w-8 h-8 text-[#CBD5E1]">
              <path d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
        )}
        {/* Status dot overlay */}
        <span className={`absolute top-1.5 left-1.5 w-2.5 h-2.5 rounded-full border-2 border-white ${cfg.dotCls}`} />
      </button>

      {/* Details */}
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-3 mb-1">
          <div className="min-w-0">
            <div className="text-sm font-medium text-[#0F172A] truncate">{summary.filename}</div>
            <div className="flex items-center gap-2 text-[11px] text-[#94A3B8] mt-0.5">
              {summary.camera_name && <span>{summary.camera_name}</span>}
              {summary.camera_name && summary.duration_seconds != null && <span>·</span>}
              {summary.duration_seconds != null && <span>{formatDuration(summary.duration_seconds)}</span>}
              {summary.relevant_timestamps.length > 0 && (
                <>
                  <span>·</span>
                  <span className="text-[#2563EB]">
                    {summary.relevant_timestamps.slice(0, 3).map(formatTs).join(', ')}
                    {summary.relevant_timestamps.length > 3 && ` +${summary.relevant_timestamps.length - 3} more`}
                  </span>
                </>
              )}
            </div>
          </div>
          <span className={`rt-badge text-[11px] font-semibold shrink-0 ${cfg.badgeCls}`}>
            {cfg.label}
          </span>
        </div>

        <p className="text-xs text-[#64748B] leading-snug line-clamp-2 mb-2">
          {summary.observation}
        </p>

        <div className="flex items-center gap-2">
          {summary.match_classification !== 'no_match' &&
           summary.match_classification !== 'analysis_failed' && (
            <button
              onClick={() => onOpen(summary)}
              className="rt-btn-primary text-xs py-1 px-3"
            >
              <svg viewBox="0 0 24 24" fill="none" className="w-3.5 h-3.5">
                <path d="M8 5v14l11-7L8 5z" fill="currentColor"/>
              </svg>
              Open video
            </button>
          )}
          {(summary.match_classification === 'no_match' ||
            summary.match_classification === 'analysis_failed') && (
            <button
              onClick={() => onOpen(summary)}
              className="rt-btn-secondary text-xs py-1 px-3"
            >
              View
            </button>
          )}
          {summary.confidence != null && (
            <span className="text-[11px] text-[#94A3B8] font-mono">
              {(summary.confidence * 100).toFixed(0)}% confidence
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  result: InvestigationResult
  recordings: Recording[]
  onOpenVideo: (summary: VideoAnalysisSummary) => void
  onNewInvestigation: () => void
}

export default function InvestigationVideoList({ result, recordings, onOpenVideo, onNewInvestigation }: Props) {
  const [showExcluded, setShowExcluded] = useState(false)

  const summaries = result.video_summaries ?? []
  const recMap = Object.fromEntries(recordings.map(r => [r.recording_id, r]))

  // Partition into visible (relevant/review) and excluded (no-match/failed)
  const visibleSummaries = summaries.filter(s => !EXCLUDED_BY_DEFAULT.has(s.match_classification))
  const excludedSummaries = summaries.filter(s => EXCLUDED_BY_DEFAULT.has(s.match_classification))

  const matchCount = summaries.filter(s =>
    s.match_classification === 'relevant_match' || s.match_classification === 'possible_match'
  ).length
  const noMatchCount = excludedSummaries.length
  const reviewCount = summaries.filter(s => s.match_classification === 'needs_review').length

  // Sort: relevant > possible > needs_review > no_match > failed
  const ORDER: Record<MatchClassification, number> = {
    relevant_match: 0,
    possible_match: 1,
    needs_review: 2,
    no_match: 3,
    analysis_failed: 4,
  }
  const sortFn = (a: VideoAnalysisSummary, b: VideoAnalysisSummary) =>
    ORDER[a.match_classification] - ORDER[b.match_classification]

  const sorted = [...visibleSummaries].sort(sortFn)
  const sortedExcluded = [...excludedSummaries].sort(sortFn)

  const allExcluded = summaries.length > 0 && visibleSummaries.length === 0

  return (
    <div className="flex flex-col min-h-screen bg-[#F8FAFC]">

      {/* Header */}
      <header className="flex items-center justify-between px-5 py-3 bg-white border-b border-[#E2E8F0] shadow-sm shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <button onClick={onNewInvestigation} className="rt-btn-ghost text-sm gap-1.5 shrink-0">
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4">
              <path d="M19 12H5M5 12l7-7M5 12l7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            Back
          </button>
          <div className="w-px h-5 bg-[#E2E8F0] shrink-0" />
          <div className="flex items-center gap-2 min-w-0">
            <span className="rt-badge bg-[#EFF6FF] text-[#2563EB] border border-[#BFDBFE] shrink-0">
              INVESTIGATION
            </span>
            <span className="text-sm font-medium text-[#0F172A] truncate">
              &ldquo;{result.query}&rdquo;
            </span>
          </div>
        </div>
        <div className="flex items-center gap-4 text-xs text-[#64748B] shrink-0">
          <span>{result.searched_recordings} video{result.searched_recordings !== 1 ? 's' : ''} analyzed</span>
          {matchCount > 0 && (
            <span className="text-[#D97706] font-medium">
              {matchCount} with relevant observations
            </span>
          )}
          {noMatchCount > 0 && <span>{noMatchCount} no match</span>}
          {reviewCount > 0 && <span className="text-[#0284C7]">{reviewCount} needs review</span>}
        </div>
      </header>

      {/* Content */}
      <div className="flex-1 max-w-4xl mx-auto w-full px-6 py-6 space-y-3">

        {/* Legend + toggle row */}
        <div className="flex items-center justify-between flex-wrap gap-3 mb-1">
          <div className="flex items-center gap-4 flex-wrap">
            {(Object.entries(MATCH_CONFIG) as [MatchClassification, typeof MATCH_CONFIG[MatchClassification]][])
              .filter(([key]) => !EXCLUDED_BY_DEFAULT.has(key))
              .map(([key, cfg]) => (
                <div key={key} className="flex items-center gap-1.5 text-[11px] text-[#64748B]">
                  <span className={`w-2 h-2 rounded-full ${cfg.dotCls}`} />
                  <span>{cfg.label}</span>
                </div>
              ))}
          </div>
          {noMatchCount > 0 && (
            <button
              onClick={() => setShowExcluded(v => !v)}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-colors font-medium ${
                showExcluded
                  ? 'bg-[#F1F5F9] text-[#0F172A] border-[#CBD5E1]'
                  : 'bg-white text-[#64748B] border-[#E2E8F0] hover:bg-[#F8FAFC]'
              }`}
              data-testid="show-excluded-toggle"
            >
              {showExcluded ? 'Hide' : 'Show'} {noMatchCount} excluded video{noMatchCount !== 1 ? 's' : ''}
            </button>
          )}
        </div>

        {summaries.length === 0 ? (
          /* No videos at all */
          <div className="rt-card rt-empty py-16">
            <svg viewBox="0 0 24 24" fill="none" className="w-8 h-8 text-[#E2E8F0] mb-2">
              <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
              <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            <p className="text-sm font-medium text-[#64748B]">No videos analyzed</p>
            <p className="text-xs text-[#94A3B8] mt-0.5">No indexed recordings were found for this investigation.</p>
          </div>
        ) : allExcluded ? (
          /* All videos are no-match */
          <div className="rt-card rt-empty py-16" data-testid="all-excluded-empty-state">
            <svg viewBox="0 0 24 24" fill="none" className="w-8 h-8 text-[#E2E8F0] mb-2">
              <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="1.5"/>
              <path d="M21 21l-4.35-4.35" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            <p className="text-sm font-medium text-[#64748B]">No relevant videos found</p>
            <p className="text-xs text-[#94A3B8] mt-1">
              {summaries.length} video{summaries.length !== 1 ? 's' : ''} analyzed — none matched the query.
            </p>
            <button
              onClick={() => setShowExcluded(true)}
              className="mt-3 text-xs text-[#2563EB] hover:underline font-medium"
              data-testid="show-excluded-from-empty"
            >
              View all analyzed videos
            </button>
          </div>
        ) : (
          /* Normal list */
          sorted.map(summary => (
            <VideoRow
              key={summary.recording_id}
              summary={summary}
              recording={recMap[summary.recording_id]}
              onOpen={onOpenVideo}
            />
          ))
        )}

        {/* Excluded videos section */}
        {showExcluded && sortedExcluded.length > 0 && (
          <div className="space-y-3 pt-2" data-testid="excluded-videos-section">
            <div className="flex items-center gap-2">
              <div className="h-px flex-1 bg-[#E2E8F0]" />
              <span className="text-[11px] font-semibold text-[#94A3B8] uppercase tracking-widest px-2">
                Excluded — No match found ({sortedExcluded.length})
              </span>
              <div className="h-px flex-1 bg-[#E2E8F0]" />
            </div>
            {sortedExcluded.map(summary => (
              <VideoRow
                key={summary.recording_id}
                summary={summary}
                recording={recMap[summary.recording_id]}
                onOpen={onOpenVideo}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
