"""Agent 4: CrossCameraCorrelationAgent — wraps existing correlate_across_cameras()."""
import logging
import time

from ..agents.base import AgentResult, AgentStatus, InvestigationContext
from ..agents.schemas import CorrelationLink, CorrelationReport
from ..correlator import correlate_across_cameras
from ..database import get_db

import aiosqlite

logger = logging.getLogger(__name__)

AGENT_NAME = "CrossCameraCorrelationAgent"


async def run(ctx: InvestigationContext) -> AgentResult[CorrelationReport]:
    t0 = time.monotonic()
    try:
        if len(ctx.recording_ids) < 2:
            return AgentResult(
                agent_name=AGENT_NAME,
                status=AgentStatus.SKIPPED,
                output=CorrelationReport(
                    candidate_links=[],
                    confirmed_links=[],
                    total_links_found=0,
                    skipped_reason="Fewer than 2 recordings — cross-camera correlation requires multiple cameras",
                ),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        links_created = await correlate_across_cameras(ctx.db_path, ctx.recording_ids)

        async with get_db(ctx.db_path) as db:
            db.row_factory = aiosqlite.Row
            if ctx.recording_ids:
                import json
                placeholders = ",".join("?" * len(ctx.recording_ids))
                rows = await (await db.execute(
                    f"""SELECT l.*, e.recording_id as target_rec_id
                        FROM event_links l
                        JOIN events e ON l.target_event_id = e.event_id
                        JOIN events se ON l.source_event_id = se.event_id
                        WHERE se.recording_id IN ({placeholders})""",
                    ctx.recording_ids,
                )).fetchall()
            else:
                rows = []

        candidate_links = []
        confirmed_links = []
        for row in rows:
            link = CorrelationLink(
                source_event_id=row["source_event_id"],
                target_event_id=row["target_event_id"],
                confidence=row["confidence"],
                explanation=row["explanation"],
            )
            if row["link_type"] == "confirmed":
                confirmed_links.append(link)
            else:
                candidate_links.append(link)

        report = CorrelationReport(
            candidate_links=candidate_links,
            confirmed_links=confirmed_links,
            total_links_found=links_created,
        )
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.COMPLETED,
            output=report,
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as exc:
        logger.exception("CrossCameraCorrelationAgent failed")
        return AgentResult(
            agent_name=AGENT_NAME,
            status=AgentStatus.FAILED,
            output=None,
            duration_ms=(time.monotonic() - t0) * 1000,
            error=str(exc),
        )
