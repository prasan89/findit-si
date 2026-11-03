import '@testing-library/jest-dom'
import { render, screen, fireEvent } from '@testing-library/react'
import InvestigationVideoList from '../InvestigationVideoList'
import type { InvestigationResult, VideoAnalysisSummary, MatchClassification } from '@/types'

// ── Helpers ────────────────────────────────────────────────────────────────────

function makeSummary(
  overrides: Partial<VideoAnalysisSummary> & { match_classification: MatchClassification }
): VideoAnalysisSummary {
  return {
    recording_id: `rec-${Math.random().toString(36).slice(2, 8)}`,
    filename: 'test.mp4',
    duration_seconds: 30,
    thumbnail_path: null,
    camera_name: null,
    camera_location: null,
    processing_status: 'INDEXED',
    observation: 'Some activity observed.',
    relevant_timestamps: [],
    event_ids: [],
    confidence: null,
    ...overrides,
  }
}

function makeResult(
  summaries: VideoAnalysisSummary[],
  query = 'chain snatch',
): InvestigationResult {
  return {
    investigation_id: 'inv-1',
    query,
    events: [],
    narrative: '',
    timestamp_ordering_reliable: false,
    ordering_explanation: '',
    searched_recordings: summaries.length,
    total_events_found: 0,
    evidence_gaps: [],
    uncertainty_summary: '',
    video_summaries: summaries,
  }
}

const noop = () => {}

// ── Filtering tests ────────────────────────────────────────────────────────────

describe('InvestigationVideoList — default filtering', () => {
  test('shows relevant_match videos by default', () => {
    const s = makeSummary({ match_classification: 'relevant_match', filename: 'relevant.mp4' })
    render(<InvestigationVideoList results={makeResult([s])} onBack={noop} onSelectVideo={noop} />)
    expect(screen.getByText('relevant.mp4')).toBeInTheDocument()
  })

  test('shows possible_match videos by default', () => {
    const s = makeSummary({ match_classification: 'possible_match', filename: 'possible.mp4' })
    render(<InvestigationVideoList results={makeResult([s])} onBack={noop} onSelectVideo={noop} />)
    expect(screen.getByText('possible.mp4')).toBeInTheDocument()
  })

  test('shows needs_review videos by default', () => {
    const s = makeSummary({ match_classification: 'needs_review', filename: 'review.mp4' })
    render(<InvestigationVideoList results={makeResult([s])} onBack={noop} onSelectVideo={noop} />)
    expect(screen.getByText('review.mp4')).toBeInTheDocument()
  })

  test('hides no_match videos by default', () => {
    const s = makeSummary({ match_classification: 'no_match', filename: 'nomatch.mp4' })
    render(<InvestigationVideoList results={makeResult([s])} onBack={noop} onSelectVideo={noop} />)
    expect(screen.queryByText('nomatch.mp4')).not.toBeInTheDocument()
  })

  test('hides analysis_failed videos by default', () => {
    const s = makeSummary({ match_classification: 'analysis_failed', filename: 'failed.mp4' })
    render(<InvestigationVideoList results={makeResult([s])} onBack={noop} onSelectVideo={noop} />)
    expect(screen.queryByText('failed.mp4')).not.toBeInTheDocument()
  })

  test('shows toggle button when no_match videos exist', () => {
    const nm = makeSummary({ match_classification: 'no_match', filename: 'nomatch.mp4' })
    const pm = makeSummary({ match_classification: 'possible_match', filename: 'possible.mp4' })
    render(<InvestigationVideoList results={makeResult([nm, pm])} onBack={noop} onSelectVideo={noop} />)
    expect(screen.getByTestId('show-excluded-toggle')).toBeInTheDocument()
  })

  test('does not show toggle when no excluded videos exist', () => {
    const s = makeSummary({ match_classification: 'possible_match', filename: 'possible.mp4' })
    render(<InvestigationVideoList results={makeResult([s])} onBack={noop} onSelectVideo={noop} />)
    expect(screen.queryByTestId('show-excluded-toggle')).not.toBeInTheDocument()
  })
})

describe('InvestigationVideoList — show excluded toggle', () => {
  test('clicking toggle reveals excluded no_match videos', () => {
    const nm = makeSummary({ match_classification: 'no_match', filename: 'hidden.mp4' })
    const pm = makeSummary({ match_classification: 'possible_match', filename: 'visible.mp4' })
    render(<InvestigationVideoList results={makeResult([nm, pm])} onBack={noop} onSelectVideo={noop} />)

    expect(screen.queryByText('hidden.mp4')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('show-excluded-toggle'))
    expect(screen.getByText('hidden.mp4')).toBeInTheDocument()
    expect(screen.getByTestId('excluded-videos-section')).toBeInTheDocument()
  })

  test('clicking toggle again hides excluded videos', () => {
    const nm = makeSummary({ match_classification: 'no_match', filename: 'hidden.mp4' })
    const pm = makeSummary({ match_classification: 'possible_match', filename: 'visible.mp4' })
    render(<InvestigationVideoList results={makeResult([nm, pm])} onBack={noop} onSelectVideo={noop} />)

    fireEvent.click(screen.getByTestId('show-excluded-toggle'))
    expect(screen.getByText('hidden.mp4')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('show-excluded-toggle'))
    expect(screen.queryByText('hidden.mp4')).not.toBeInTheDocument()
  })
})

describe('InvestigationVideoList — all-excluded empty state', () => {
  test('shows empty state when all videos are no_match', () => {
    const s1 = makeSummary({ match_classification: 'no_match', filename: 'a.mp4' })
    const s2 = makeSummary({ match_classification: 'no_match', filename: 'b.mp4' })
    render(<InvestigationVideoList results={makeResult([s1, s2])} onBack={noop} onSelectVideo={noop} />)
    expect(screen.getByTestId('all-excluded-empty-state')).toBeInTheDocument()
    expect(screen.getByText(/No matching footage found/i)).toBeInTheDocument()
    // Individual cards should not be visible
    expect(screen.queryByText('a.mp4')).not.toBeInTheDocument()
    expect(screen.queryByText('b.mp4')).not.toBeInTheDocument()
  })

  test('empty state offers "View all analyzed videos" link', () => {
    const s = makeSummary({ match_classification: 'no_match', filename: 'nomatch.mp4' })
    render(<InvestigationVideoList results={makeResult([s])} onBack={noop} onSelectVideo={noop} />)
    const btn = screen.getByTestId('show-excluded-from-empty')
    expect(btn).toBeInTheDocument()
    fireEvent.click(btn)
    // After clicking, the no-match video should appear
    expect(screen.getByText('nomatch.mp4')).toBeInTheDocument()
  })
})

describe('InvestigationVideoList — thumbnail URL', () => {
  test('renders thumbnail with correct /thumbnails/ URL (not /api/thumbnails/)', () => {
    const rid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    const s = makeSummary({
      match_classification: 'possible_match',
      filename: 'cam01.mp4',
      recording_id: rid,
      thumbnail_path: `thumbnails/${rid}.jpg`,
    })
    render(<InvestigationVideoList results={makeResult([s])} onBack={noop} onSelectVideo={noop} />)
    const img = screen.getByRole('img', { name: /cam01/i })
    expect(img).toHaveAttribute('src', `/thumbnails/${rid}.jpg`)
    expect(img).not.toHaveAttribute('src', expect.stringContaining('/api/thumbnails/'))
  })

  test('shows fallback when thumbnail_path is null', () => {
    const s = makeSummary({
      match_classification: 'possible_match',
      filename: 'nothumb.mp4',
      thumbnail_path: null,
    })
    render(<InvestigationVideoList results={makeResult([s])} onBack={noop} onSelectVideo={noop} />)
    // No img element for this card (or it should have the fallback SVG visible)
    expect(screen.queryByRole('img', { name: /nothumb/i })).not.toBeInTheDocument()
  })

  test('each video card uses its own recording-specific thumbnail', () => {
    const rid1 = 'rid1-0000-0000-0000-000000000001'
    const rid2 = 'rid2-0000-0000-0000-000000000002'
    const s1 = makeSummary({
      match_classification: 'possible_match',
      filename: 'cam01.mp4',
      recording_id: rid1,
      thumbnail_path: `thumbnails/${rid1}.jpg`,
    })
    const s2 = makeSummary({
      match_classification: 'possible_match',
      filename: 'cam02.mp4',
      recording_id: rid2,
      thumbnail_path: `thumbnails/${rid2}.jpg`,
    })
    render(<InvestigationVideoList results={makeResult([s1, s2])} onBack={noop} onSelectVideo={noop} />)

    const img1 = screen.getByRole('img', { name: /cam01/i })
    const img2 = screen.getByRole('img', { name: /cam02/i })
    expect(img1).toHaveAttribute('src', `/thumbnails/${rid1}.jpg`)
    expect(img2).toHaveAttribute('src', `/thumbnails/${rid2}.jpg`)
    // Different thumbnails
    expect(img1.getAttribute('src')).not.toBe(img2.getAttribute('src'))
  })
})

describe('InvestigationVideoList — counts in header', () => {
  test('stats bar shows relevant and excluded counts separately', () => {
    const nm = makeSummary({ match_classification: 'no_match' })
    const pm = makeSummary({ match_classification: 'possible_match' })
    render(<InvestigationVideoList results={makeResult([nm, pm])} onBack={noop} onSelectVideo={noop} />)
    // stats bar should have "excluded" text visible
    expect(screen.getAllByText(/excluded/i).length).toBeGreaterThan(0)
  })

  test('stats bar shows total analyzed count', () => {
    const summaries = [
      makeSummary({ match_classification: 'possible_match' }),
      makeSummary({ match_classification: 'no_match' }),
      makeSummary({ match_classification: 'no_match' }),
    ]
    render(<InvestigationVideoList results={makeResult(summaries)} onBack={noop} onSelectVideo={noop} />)
    expect(screen.getByText(/3 recordings analyzed/i)).toBeInTheDocument()
  })
})
