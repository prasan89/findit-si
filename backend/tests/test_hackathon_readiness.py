"""Hackathon readiness: unanswerable-query contract for both investigation modes.

Highest-risk uncovered behavior: the system must explicitly report "no evidence"
for queries nothing matches — never invent conclusions from unrelated footage.

Seeds go to the app's own database (DATA_DIR/findit.db), so the API exercises
the real retrieval path with a present-but-nonmatching event.
"""
import pytest
import pytest_asyncio

from app.database import create_tables, get_db
from tests.fixtures import RECORDING_ID_1, insert_event, insert_recording


async def _seed_nonmatching_event(tmp_data_dir, action, objects):
    db_path = str(tmp_data_dir / "findit.db")
    await create_tables(db_path)
    async with get_db(db_path) as db:
        import aiosqlite
        db.row_factory = aiosqlite.Row
        await insert_recording(db, RECORDING_ID_1)
        await insert_event(
            db, RECORDING_ID_1,
            action_description=action,
            object_descriptions=objects,
            observed_facts=[action],
        )
        await db.commit()


@pytest.mark.asyncio
class TestCorsAllowlist:
    """Pin the backend CORS boundary the browser relies on."""

    async def test_preflight_allows_frontend_origin(self, client):
        resp = await client.options(
            "/api/recordings/",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == \
            "http://localhost:3000"

    async def test_preflight_rejects_unknown_origin(self, client):
        resp = await client.options(
            "/api/recordings/",
            headers={
                "Origin": "http://localhost:9999",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") != \
            "http://localhost:9999"


@pytest.mark.asyncio
class TestUnanswerableQuery:
    async def test_standard_returns_no_evidence_without_invention(
        self, client, tmp_data_dir
    ):
        await _seed_nonmatching_event(
            tmp_data_dir, "red person detected at entrance", ["red person"])

        resp = await client.post(
            "/api/investigations/",
            json={"query": "purple helicopter zebra",
                  "recording_ids": [RECORDING_ID_1]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []
        assert data["total_events_found"] == 0
        assert "no events matching" in data["narrative"].lower()
        # The unrelated seeded event must not leak into the answer.
        assert "red person" not in data["narrative"].lower()

    async def test_multi_agent_returns_no_invented_findings(
        self, client, tmp_data_dir
    ):
        await _seed_nonmatching_event(
            tmp_data_dir, "blue bag found in lobby", ["blue bag"])

        resp = await client.post(
            "/api/investigations/multi-agent",
            json={"query": "purple helicopter zebra",
                  "recording_ids": [RECORDING_ID_1],
                  "budget_usd": 0.0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["agents"]) == 8
        report = data["final_report"]
        assert report is not None
        assert report["key_findings"] == []
        assert report["narrative"].startswith("No evidence found relevant to:")
        assert "blue bag" not in report["narrative"].lower()
