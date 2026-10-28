"""pytest configuration and fixtures for FindIt tests."""
import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from app.database import create_tables, get_db
from app.config import Settings, get_settings
from app.storage import LocalStorageBackend
from app.meta_client import MockMetaClient
from tests.fixtures import make_scenario_map


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture
async def tmp_data_dir(tmp_path):
    """Provide a temporary data directory for each test."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest_asyncio.fixture
async def db_path(tmp_data_dir):
    """Create a fresh SQLite database for each test."""
    path = str(tmp_data_dir / "test.db")
    await create_tables(path)
    return path


@pytest_asyncio.fixture
async def storage(tmp_data_dir):
    """Provide a LocalStorageBackend pointed at tmp_data_dir."""
    backend = LocalStorageBackend(tmp_data_dir)
    await backend.ensure_dirs()
    return backend


@pytest_asyncio.fixture
async def mock_client():
    """Return a MockMetaClient with all test scenarios loaded."""
    return MockMetaClient(scenario_map=make_scenario_map())


@pytest_asyncio.fixture
async def test_settings(tmp_data_dir):
    """Override Settings to use the temp directory."""
    return Settings(
        data_dir=str(tmp_data_dir),
        meta_api_key="",
    )


@pytest_asyncio.fixture
async def test_db(db_path):
    """Yield an open DB connection for direct test manipulation."""
    async with get_db(db_path) as db:
        import aiosqlite
        db.row_factory = aiosqlite.Row
        yield db


# Phase 5: enforce the documented "tests never call external APIs" guarantee.
# Non-live tests run with API keys stripped so LLM helpers take the
# deterministic mocked path even when keys exist in the environment.
# Opt-in live tests (marked live_api) keep the real environment.
@pytest.fixture(autouse=True)
def _strip_api_keys_for_non_live_tests(request, monkeypatch):
    if request.node.get_closest_marker("live_api") is None:
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        monkeypatch.delenv("META_API_KEY", raising=False)
        monkeypatch.delenv("META_API_BASE_URL", raising=False)


# Phase 6: each test starts with clean guard state (rate buckets, slots).
@pytest.fixture(autouse=True)
def _reset_operational_guards():
    from app.guards import (reset_investigation_slots, reset_llm_semaphore,
                            reset_rate_limiter)
    from app.investigation_cache import reset_investigation_cache_state
    reset_rate_limiter()
    reset_investigation_slots()
    reset_llm_semaphore()
    reset_investigation_cache_state()
    yield
    reset_rate_limiter()
    reset_investigation_slots()
    reset_llm_semaphore()
    reset_investigation_cache_state()


# FastAPI test client fixture
@pytest_asyncio.fixture
async def client(tmp_data_dir, db_path):
    """Return an AsyncClient for the FastAPI app, wired to tmp dirs."""
    import os
    os.environ["DATA_DIR"] = str(tmp_data_dir)
    # Clear LRU cache so the new settings are picked up
    get_settings.cache_clear()

    from httpx import AsyncClient, ASGITransport
    from app.main import create_app

    application = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as ac:
        yield ac

    get_settings.cache_clear()
    if "DATA_DIR" in os.environ:
        del os.environ["DATA_DIR"]
