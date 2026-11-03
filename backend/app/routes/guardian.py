"""Guardian API routes — hazard alert retrieval and review status management."""
import logging
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from app.police_eligibility import is_police_alert_eligible, normalize_incident_category

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

recording_router = APIRouter()
global_router = APIRouter()


class GuardianAlertUpdate(BaseModel):
    review_status: str

    @field_validator("review_status")
    @classmethod
    def valid_status(cls, v: str) -> str:
        if v not in ("new", "acknowledged", "dismissed"):
            raise ValueError("review_status must be 'new', 'acknowledged', or 'dismissed'")
        return v


class PoliceDispatchRequest(BaseModel):
    alert_id: str
    recording_id: str

    @field_validator("alert_id", "recording_id")
    @classmethod
    def valid_uuid(cls, v: str) -> str:
        if not _UUID_RE.match(v):
            raise ValueError("Must be a valid UUID")
        return v


def _get_deps():
    from app.config import get_settings
    from app import guardian as guardian_mod
    settings = get_settings()
    return settings.db_path, guardian_mod


@recording_router.get("/{recording_id}/guardian-alerts")
async def list_recording_guardian_alerts(
    recording_id: str,
    review_status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0),
):
    if not _UUID_RE.match(recording_id):
        raise HTTPException(status_code=400, detail="Invalid recording_id")
    db_path, guardian_mod = _get_deps()

    import aiosqlite
    from app.database import get_db
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT recording_id FROM recordings WHERE recording_id = ?", (recording_id,)
        )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Recording not found")

    alerts = await guardian_mod.get_alerts_for_recording(
        db_path, recording_id,
        review_status=review_status,
        severity=severity,
        min_confidence=min_confidence,
    )
    return {"recording_id": recording_id, "alerts": alerts, "count": len(alerts)}


@recording_router.patch("/{recording_id}/guardian-alerts/{alert_id}")
async def update_guardian_alert(
    recording_id: str,
    alert_id: str,
    body: GuardianAlertUpdate,
):
    if not _UUID_RE.match(recording_id):
        raise HTTPException(status_code=400, detail="Invalid recording_id")
    if not _UUID_RE.match(alert_id):
        raise HTTPException(status_code=400, detail="Invalid alert_id")
    db_path, guardian_mod = _get_deps()

    updated = await guardian_mod.update_alert_review_status(
        db_path, recording_id, alert_id, body.review_status
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Alert not found")

    alerts = await guardian_mod.get_alerts_for_recording(db_path, recording_id)
    alert = next((a for a in alerts if a["alert_id"] == alert_id), None)
    if alert:
        return alert
    return {"alert_id": alert_id, "review_status": body.review_status}


@global_router.get("/guardian-alerts")
async def list_all_guardian_alerts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    review_status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    hazard_category: Optional[str] = Query(None),
):
    db_path, guardian_mod = _get_deps()
    alerts, total = await guardian_mod.get_all_alerts(
        db_path,
        limit=limit,
        offset=offset,
        review_status=review_status,
        severity=severity,
        hazard_category=hazard_category,
    )
    return {"alerts": alerts, "count": len(alerts), "total": total}


@global_router.post("/guardian-alerts/police-dispatch")
async def request_police_dispatch(body: PoliceDispatchRequest):
    """Simulate a police dispatch request.

    Enforces the THEFT / ASSAULT / ABUSE allowlist server-side.
    Ineligible incidents are rejected with 403; the block is logged without
    exposing evidence content.  No real dispatch is performed — simulation only.
    """
    db_path, guardian_mod = _get_deps()

    # Load the alert to check its category
    alerts = await guardian_mod.get_alerts_for_recording(db_path, body.recording_id)
    alert = next((a for a in alerts if a["alert_id"] == body.alert_id), None)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    eligible = is_police_alert_eligible(
        hazard_category=alert.get("hazard_category"),
        description=alert.get("description"),
    )

    if not eligible:
        canonical = normalize_incident_category(
            hazard_category=alert.get("hazard_category"),
            description=alert.get("description"),
        )
        logger.info(
            "Police dispatch blocked: alert_id=%s recording_id=%s "
            "hazard_category=%s canonical=%s",
            body.alert_id[:8],
            body.recording_id[:8],
            alert.get("hazard_category"),
            canonical,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "Incident category is not eligible for police notification. "
                "Only THEFT, ASSAULT, and ABUSE incidents may be dispatched."
            ),
        )

    # Human review gate — never auto-dispatch; require acknowledged status first
    if alert.get("review_status") == "new":
        raise HTTPException(
            status_code=409,
            detail=(
                "Alert must be acknowledged by an authorized reviewer "
                "before police dispatch can proceed."
            ),
        )

    police_category = normalize_incident_category(
        hazard_category=alert.get("hazard_category"),
        description=alert.get("description"),
    )
    return {
        "status": "simulation",
        "message": "Police dispatch prepared (simulation only — no real call placed).",
        "alert_id": body.alert_id,
        "police_category": police_category,
        "review_status": alert.get("review_status"),
    }
