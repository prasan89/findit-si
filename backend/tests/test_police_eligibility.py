"""Tests for police alert eligibility module."""
import uuid

import aiosqlite
import pytest

from app.police_eligibility import (
    POLICE_ALERT_ALLOWED_CATEGORIES,
    is_police_alert_eligible,
    normalize_incident_category,
)
from app.guardian import upsert_guardian_alert, get_alerts_for_recording


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _insert_recording(db_path: str, rid: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO recordings("
            "recording_id,original_filename,upload_timestamp,"
            "video_path,processing_status,frame_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (rid, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "INDEXED", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()


# ── Canonical category set ────────────────────────────────────────────────────

class TestAllowedCategories:
    def test_contains_exactly_three_categories(self):
        assert POLICE_ALERT_ALLOWED_CATEGORIES == {"THEFT", "ASSAULT", "ABUSE"}

    def test_categories_are_uppercase(self):
        for cat in POLICE_ALERT_ALLOWED_CATEGORIES:
            assert cat == cat.upper()


# ── normalize_incident_category ───────────────────────────────────────────────

class TestNormalizeIncidentCategory:

    # ── Allowed via event_type ────────────────────────────────────────────────

    def test_possible_theft_event_type(self):
        assert normalize_incident_category(event_type="possible-theft") == "THEFT"

    def test_robbery_event_type(self):
        assert normalize_incident_category(event_type="robbery") == "THEFT"

    def test_chain_snatching_event_type(self):
        assert normalize_incident_category(event_type="chain-snatching") == "THEFT"

    def test_vehicle_theft_event_type(self):
        assert normalize_incident_category(event_type="vehicle-theft") == "THEFT"

    def test_carjacking_event_type(self):
        assert normalize_incident_category(event_type="carjacking") == "THEFT"

    def test_possible_assault_event_type(self):
        assert normalize_incident_category(event_type="possible-assault") == "ASSAULT"

    def test_confrontation_event_type(self):
        assert normalize_incident_category(event_type="confrontation") == "ASSAULT"

    def test_dangerous_threat_event_type(self):
        assert normalize_incident_category(event_type="dangerous-threat") == "ASSAULT"

    def test_armed_person_event_type(self):
        assert normalize_incident_category(event_type="armed-person") == "ASSAULT"

    # ── Allowed via other_hazard description keywords ─────────────────────────

    def test_other_hazard_theft_description(self):
        result = normalize_incident_category(
            hazard_category="other_hazard",
            description="Person snatched chain from pedestrian",
        )
        assert result == "THEFT"

    def test_other_hazard_robbery_description(self):
        result = normalize_incident_category(
            hazard_category="other_hazard",
            description="Robbery observed near entrance gate",
        )
        assert result == "THEFT"

    def test_other_hazard_assault_description(self):
        result = normalize_incident_category(
            hazard_category="other_hazard",
            description="Assault occurred between two individuals",
        )
        assert result == "ASSAULT"

    def test_other_hazard_struggle_description(self):
        result = normalize_incident_category(
            hazard_category="other_hazard",
            description="A struggle was observed",
        )
        assert result == "ASSAULT"

    def test_other_hazard_abuse_description(self):
        result = normalize_incident_category(
            hazard_category="other_hazard",
            description="Harassment and abuse directed at individual",
        )
        assert result == "ABUSE"

    # ── Blocked — non-eligible hazard categories ──────────────────────────────

    def test_fire_smoke_blocked(self):
        assert normalize_incident_category(hazard_category="fire_smoke") is None

    def test_dangerous_animal_blocked(self):
        assert normalize_incident_category(hazard_category="dangerous_animal") is None

    def test_weapon_visible_blocked(self):
        # weapon_visible is owner-only; it doesn't map to assault in the strict allowlist
        assert normalize_incident_category(hazard_category="weapon_visible") is None

    def test_vehicle_collision_blocked(self):
        assert normalize_incident_category(hazard_category="vehicle_collision") is None

    def test_person_fall_injury_blocked(self):
        assert normalize_incident_category(hazard_category="person_fall_injury") is None

    def test_restricted_area_entry_blocked(self):
        assert normalize_incident_category(hazard_category="restricted_area_entry") is None

    # ── Blocked — non-eligible event types ───────────────────────────────────

    def test_suspicious_activity_event_blocked(self):
        assert normalize_incident_category(event_type="suspicious-approach-departure") is None

    def test_person_detected_event_blocked(self):
        assert normalize_incident_category(event_type="person_detected") is None

    def test_vehicle_detected_event_blocked(self):
        assert normalize_incident_category(event_type="vehicle_detected") is None

    def test_motion_event_blocked(self):
        assert normalize_incident_category(event_type="motion_detected") is None

    def test_sudden_fall_blocked(self):
        assert normalize_incident_category(event_type="sudden-fall") is None

    def test_other_anomaly_blocked(self):
        assert normalize_incident_category(event_type="other-anomaly") is None

    def test_narrative_step_blocked(self):
        assert normalize_incident_category(event_type="narrative_step") is None

    # ── Blocked — unknown / missing / ambiguous ───────────────────────────────

    def test_none_category_blocked(self):
        assert normalize_incident_category() is None

    def test_empty_string_event_type_blocked(self):
        assert normalize_incident_category(event_type="") is None

    def test_unknown_event_type_blocked(self):
        assert normalize_incident_category(event_type="completely_unknown_type") is None

    def test_other_hazard_no_description_blocked(self):
        assert normalize_incident_category(hazard_category="other_hazard") is None

    def test_other_hazard_unrelated_description_blocked(self):
        result = normalize_incident_category(
            hazard_category="other_hazard",
            description="Person walking through frame near entrance",
        )
        assert result is None

    def test_severity_alone_cannot_grant_eligibility(self):
        # High-severity fire should still be blocked
        result = normalize_incident_category(hazard_category="fire_smoke")
        assert result is None


# ── is_police_alert_eligible ──────────────────────────────────────────────────

class TestIsPoliceAlertEligible:

    # ── Allowed ───────────────────────────────────────────────────────────────

    def test_theft_event_type_eligible(self):
        assert is_police_alert_eligible(event_type="possible-theft") is True

    def test_assault_event_type_eligible(self):
        assert is_police_alert_eligible(event_type="possible-assault") is True

    def test_abuse_via_description_eligible(self):
        assert is_police_alert_eligible(
            hazard_category="other_hazard",
            description="Harassment and abuse observed",
        ) is True

    def test_robbery_eligible(self):
        assert is_police_alert_eligible(event_type="robbery") is True

    def test_chain_snatching_eligible(self):
        assert is_police_alert_eligible(event_type="chain-snatching") is True

    # ── Blocked ───────────────────────────────────────────────────────────────

    def test_fire_smoke_blocked(self):
        assert is_police_alert_eligible(hazard_category="fire_smoke") is False

    def test_dangerous_animal_blocked(self):
        assert is_police_alert_eligible(hazard_category="dangerous_animal") is False

    def test_weapon_visible_blocked(self):
        assert is_police_alert_eligible(hazard_category="weapon_visible") is False

    def test_person_detected_blocked(self):
        assert is_police_alert_eligible(event_type="person_detected") is False

    def test_vehicle_detected_blocked(self):
        assert is_police_alert_eligible(event_type="vehicle_detected") is False

    def test_suspicious_activity_blocked(self):
        assert is_police_alert_eligible(
            event_type="suspicious-approach-departure"
        ) is False

    def test_motion_detection_blocked(self):
        assert is_police_alert_eligible(event_type="motion_detected") is False

    def test_animal_detection_blocked(self):
        assert is_police_alert_eligible(hazard_category="dangerous_animal") is False

    def test_unknown_category_blocked(self):
        assert is_police_alert_eligible(event_type="unknown_type") is False

    def test_missing_category_blocked(self):
        assert is_police_alert_eligible() is False

    def test_severity_alone_cannot_bypass(self):
        # Even critical fire should not be eligible
        assert is_police_alert_eligible(hazard_category="fire_smoke") is False


# ── Police dispatch API enforcement ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_api_blocks_ineligible_category(client, tmp_data_dir):
    """Direct API call with ineligible hazard_category returns 403."""
    from app.database import create_tables, get_db
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    rid = str(uuid.uuid4())
    async with get_db(app_db) as db:
        await db.execute(
            "INSERT INTO recordings(recording_id,original_filename,upload_timestamp,"
            "video_path,processing_status,frame_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (rid, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "INDEXED", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    # Insert a fire_smoke alert (should be blocked)
    await upsert_guardian_alert(app_db, rid, {
        "hazard_category": "fire_smoke",
        "severity": "critical",
        "confidence": 0.95,
        "timestamp_seconds": 1.0,
        "time_range_start": 0.0,
        "time_range_end": 10.0,
        "description": "Large fire visible",
        "evidence_frames": [],
        "supporting_segment_ids": [],
    })
    alerts = await get_alerts_for_recording(app_db, rid)
    alert_id = alerts[0]["alert_id"]

    res = await client.post(
        "/api/guardian-alerts/police-dispatch",
        json={"alert_id": alert_id, "recording_id": rid},
    )
    assert res.status_code == 403
    assert "THEFT" in res.json()["detail"] or "eligible" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_dispatch_api_blocks_dangerous_animal(client, tmp_data_dir):
    """Dangerous animal alert cannot be dispatched regardless of severity."""
    from app.database import create_tables, get_db
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    rid = str(uuid.uuid4())
    async with get_db(app_db) as db:
        await db.execute(
            "INSERT INTO recordings(recording_id,original_filename,upload_timestamp,"
            "video_path,processing_status,frame_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (rid, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "INDEXED", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    await upsert_guardian_alert(app_db, rid, {
        "hazard_category": "dangerous_animal",
        "severity": "critical",
        "confidence": 0.95,
        "timestamp_seconds": 1.0,
        "time_range_start": 0.0,
        "time_range_end": 10.0,
        "description": "Snake detected in corridor",
        "evidence_frames": [],
        "supporting_segment_ids": [],
    })
    alerts = await get_alerts_for_recording(app_db, rid)
    alert_id = alerts[0]["alert_id"]

    res = await client.post(
        "/api/guardian-alerts/police-dispatch",
        json={"alert_id": alert_id, "recording_id": rid},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_dispatch_api_blocks_weapon_visible(client, tmp_data_dir):
    """Weapon visible alert is owner-only; cannot be dispatched to police."""
    from app.database import create_tables, get_db
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    rid = str(uuid.uuid4())
    async with get_db(app_db) as db:
        await db.execute(
            "INSERT INTO recordings(recording_id,original_filename,upload_timestamp,"
            "video_path,processing_status,frame_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (rid, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "INDEXED", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    await upsert_guardian_alert(app_db, rid, {
        "hazard_category": "weapon_visible",
        "severity": "critical",
        "confidence": 0.90,
        "timestamp_seconds": 2.0,
        "time_range_start": 0.0,
        "time_range_end": 10.0,
        "description": "Firearm visible",
        "evidence_frames": [],
        "supporting_segment_ids": [],
    })
    alerts = await get_alerts_for_recording(app_db, rid)
    alert_id = alerts[0]["alert_id"]

    res = await client.post(
        "/api/guardian-alerts/police-dispatch",
        json={"alert_id": alert_id, "recording_id": rid},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_dispatch_api_allows_theft_alert_after_review(client, tmp_data_dir):
    """other_hazard with theft description → eligible; requires human review first."""
    from app.database import create_tables, get_db
    from app.guardian import update_alert_review_status
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    rid = str(uuid.uuid4())
    async with get_db(app_db) as db:
        await db.execute(
            "INSERT INTO recordings(recording_id,original_filename,upload_timestamp,"
            "video_path,processing_status,frame_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (rid, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "INDEXED", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    await upsert_guardian_alert(app_db, rid, {
        "hazard_category": "other_hazard",
        "severity": "high",
        "confidence": 0.85,
        "timestamp_seconds": 5.0,
        "time_range_start": 0.0,
        "time_range_end": 10.0,
        "description": "Chain snatching robbery observed",
        "evidence_frames": [],
        "supporting_segment_ids": [],
    })
    alerts = await get_alerts_for_recording(app_db, rid)
    alert_id = alerts[0]["alert_id"]

    # Without review: 409 (human review required)
    res = await client.post(
        "/api/guardian-alerts/police-dispatch",
        json={"alert_id": alert_id, "recording_id": rid},
    )
    assert res.status_code == 409

    # After human review: dispatch proceeds
    await update_alert_review_status(app_db, rid, alert_id, "acknowledged")
    res2 = await client.post(
        "/api/guardian-alerts/police-dispatch",
        json={"alert_id": alert_id, "recording_id": rid},
    )
    assert res2.status_code == 200
    data = res2.json()
    assert data["status"] == "simulation"
    assert data["police_category"] == "THEFT"


@pytest.mark.asyncio
async def test_dispatch_api_retry_cannot_bypass_filter(client, tmp_data_dir):
    """Retrying with an ineligible alert always returns 403 — retry cannot bypass."""
    from app.database import create_tables, get_db
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    rid = str(uuid.uuid4())
    async with get_db(app_db) as db:
        await db.execute(
            "INSERT INTO recordings(recording_id,original_filename,upload_timestamp,"
            "video_path,processing_status,frame_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (rid, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "INDEXED", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    await upsert_guardian_alert(app_db, rid, {
        "hazard_category": "vehicle_collision",
        "severity": "critical",
        "confidence": 0.95,
        "timestamp_seconds": 1.0,
        "time_range_start": 0.0,
        "time_range_end": 10.0,
        "description": "Vehicle collision",
        "evidence_frames": [],
        "supporting_segment_ids": [],
    })
    alerts = await get_alerts_for_recording(app_db, rid)
    alert_id = alerts[0]["alert_id"]

    # First attempt: blocked
    r1 = await client.post(
        "/api/guardian-alerts/police-dispatch",
        json={"alert_id": alert_id, "recording_id": rid},
    )
    assert r1.status_code == 403

    # Retry: still blocked
    r2 = await client.post(
        "/api/guardian-alerts/police-dispatch",
        json={"alert_id": alert_id, "recording_id": rid},
    )
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_dispatch_nonexistent_alert_returns_404(client, tmp_data_dir):
    from app.database import create_tables
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    res = await client.post(
        "/api/guardian-alerts/police-dispatch",
        json={"alert_id": str(uuid.uuid4()), "recording_id": str(uuid.uuid4())},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_property_owner_workflow_unaffected(client, tmp_data_dir):
    """Fire/smoke alerts still appear in the regular (owner) guardian-alerts list."""
    from app.database import create_tables, get_db
    app_db = str(tmp_data_dir / "findit.db")
    await create_tables(app_db)

    rid = str(uuid.uuid4())
    async with get_db(app_db) as db:
        await db.execute(
            "INSERT INTO recordings(recording_id,original_filename,upload_timestamp,"
            "video_path,processing_status,frame_count,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (rid, "test.mp4", "2026-01-01T00:00:00Z",
             "videos/test.mp4", "INDEXED", 0,
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

    await upsert_guardian_alert(app_db, rid, {
        "hazard_category": "fire_smoke",
        "severity": "critical",
        "confidence": 0.90,
        "timestamp_seconds": 1.0,
        "time_range_start": 0.0,
        "time_range_end": 10.0,
        "description": "Smoke detected",
        "evidence_frames": [],
        "supporting_segment_ids": [],
    })

    # Owner-facing alert list still returns the fire alert
    res = await client.get(f"/api/recordings/{rid}/guardian-alerts")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["alerts"][0]["hazard_category"] == "fire_smoke"


# ── is_owner_alert_eligible ───────────────────────────────────────────────────

class TestIsOwnerAlertEligible:

    def test_fire_smoke_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(hazard_category="fire_smoke") is True

    def test_flood_water_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(hazard_category="flood_water") is True

    def test_electrical_hazard_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(hazard_category="electrical_hazard") is True

    def test_structural_hazard_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(hazard_category="structural_hazard") is True

    def test_dangerous_animal_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(hazard_category="dangerous_animal") is True

    def test_unauthorized_entry_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(hazard_category="unauthorized_entry") is True

    def test_vandalism_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(hazard_category="vandalism") is True

    def test_restricted_area_entry_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(hazard_category="restricted_area_entry") is True

    def test_weapon_visible_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(hazard_category="weapon_visible") is True

    def test_unknown_category_blocked(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(hazard_category="unknown_category") is False

    def test_no_category_blocked(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible() is False


class TestIsOwnerEventEligible:

    def test_suspicious_activity_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(event_type="suspicious-approach-departure") is True

    def test_sudden_fall_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(event_type="sudden-fall") is True

    def test_possible_theft_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(event_type="possible-theft") is True

    def test_possible_assault_eligible(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(event_type="possible-assault") is True

    def test_person_detected_routine_blocked(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(event_type="person_detected") is False

    def test_vehicle_detected_routine_blocked(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(event_type="vehicle_detected") is False

    def test_narrative_step_routine_blocked(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(event_type="narrative_step") is False

    def test_motion_detected_routine_blocked(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(event_type="motion_detected") is False

    def test_unknown_event_type_blocked(self):
        from app.police_eligibility import is_owner_alert_eligible
        assert is_owner_alert_eligible(event_type="unknown_event") is False
