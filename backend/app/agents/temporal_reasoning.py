"""Agent 3: TemporalReasoningAgent — timestamp normalization and gap detection."""
import logging
import time
from typing import Optional

from ..agents.base import AgentResult, AgentStatus, InvestigationContext
from ..agents.schemas import TemporalEntry, TemporalReport
from ..database import get_db
from ..investigate import _sort_key

import aiosqlite

logger = logging.getLogger(__name__)

AGENT_NAME = "TemporalReasoningAgent"
GAP_THRESHOLD_SECONDS = 30.0


async def run(ctx: InvestigationContext) -> AgentResult[TemporalReport]:
    t0 = time.monotonic()
    try:
        events = ctx.events
        if not events:
            return AgentResult(
                agent_name=AGENT_NAME,
                status=AgentStatus.SKIPPED,
                output=TemporalReport(
                    timeline=[],
                    timestamp_reliable=False,
                    gaps_detected=["No events to analyze"],
                    inconsistencies=[],
                ),
                duration_ms=0.0,
            )

        sorted_events = sorted(events, key=_sort_key)
        timeline: list[TemporalEntry] = []
        for order, ev in enumerate(sorted_events):
            abs_ts = _compute_abs_ts(ev)
            timeline.append(TemporalEntry(
                event_id=ev["event_id"],
                recording_id=ev["recording_id"],
                abs_timestamp=abs_ts,
                relative_order=order,
                video_timestamp_seconds=ev["timestamp_seconds"],
            ))

        ts_reliable = all(e.abs_timestamp is not None for e in timeline)

        gaps: list[str] = []
        inconsistencies: list[str] = []
        for i in range(1, len(timeline)):
            a = timeline[i - 1]
            b = timeline[i]
            if a.abs_timestamp is not None and b.abs_timestamp is not None:
                gap = b.abs_timestamp - a.abs_timestamp
                if gap > GAP_THRESHOLD_SECONDS:
                    gaps.append(
                        f"{gap:.0f}s gap between event {a.event_id[:8]} "
                        f"and {b.event_id[:8]}"
                    )
                if gap < 0:
                    inconsistencies.append(
                        f"Negative time gap ({gap:.1f}s) between events "
                        f"{a.event_id[:8]} and {b.event_id[:8]} — possible clock skew"
                    )

        duration_ms = (time.monotonic() - t0) * 1000
        report = TemporalReport(
            timeline=timeline,
            timestamp_reliable=ts_reliable,
            gaps_detected=gaps,
            inconsistencies=inconsistencies,
        )
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.COMPLETED,
            output=report,
            duration_ms=duration_ms,
        )
    except Exception as exc:
        logger.exception("TemporalReasoningAgent failed")
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.FAILED,
            output=None,
            duration_ms=(time.monotonic() - t0) * 1000,
            error=str(exc),
        )


def _compute_abs_ts(ev: dict) -> Optional[float]:
    from datetime import datetime, timezone
    rec_dt = ev.get("rec_dt") or ev.get("recording_datetime")
    if rec_dt:
        try:
            dt = datetime.fromisoformat(rec_dt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp() + ev["timestamp_seconds"]
        except Exception:
            pass
    return None
