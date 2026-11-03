"""FindIt FastAPI application."""
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .config import get_settings
from .database import create_tables
from .guards import check_rate_limit, normalize_request_id
from .storage import LocalStorageBackend
from .routes import recordings, search as search_router, investigations as investigations_router, usage as usage_router
from .routes import multi_agent_investigations as multi_agent_router
from .routes import video_streaming as video_streaming_router
from .routes import temporal_events as temporal_events_router
from .routes import guardian as guardian_router_module

logger = logging.getLogger(__name__)

# Phase 6: expensive POST routes subject to per-IP rate limiting.
_RATE_LIMITED_ROUTES = frozenset({
    ("POST", "/api/investigations/"),
    ("POST", "/api/investigations/multi-agent"),
    ("POST", "/api/recordings/upload"),
})


def _is_rate_limited(method: str, path: str) -> bool:
    if (method, path) in _RATE_LIMITED_ROUTES:
        return True
    # Temporal refine spends vision budget; the recording id is dynamic.
    return method == "POST" and path.startswith("/api/recordings/") and path.endswith("/refine")


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Phase 6: request IDs, expensive-route rate limiting, structured logs.

    Logs method/path/status/latency only — never query strings, headers,
    bodies, or client payloads.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = normalize_request_id(
            request.headers.get("x-request-id"), str(uuid.uuid4()))
        request.state.request_id = request_id

        settings = get_settings()
        route_key = (request.method, request.url.path)
        if _is_rate_limited(*route_key):
            client_host = request.client.host if request.client else "unknown"
            allowed, retry_after = check_rate_limit(
                f"ip:{client_host}:{route_key[1]}", settings.rate_limit_per_minute)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Slow down and retry."},
                    headers={"Retry-After": str(int(retry_after) + 1),
                             "X-Request-ID": request_id},
                )

        t0 = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_id=%s %s %s failed",
                             request_id, request.method, request.url.path)
            raise
        latency_ms = (time.monotonic() - t0) * 1000.0
        response.headers["X-Request-ID"] = request_id
        logger.info("request_id=%s %s %s status=%s latency_ms=%.1f",
                    request_id, request.method, request.url.path,
                    response.status_code, latency_ms)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    data_dir = Path(settings.data_dir)

    # Ensure data directories exist
    storage = LocalStorageBackend(data_dir)
    await storage.ensure_dirs()

    # Initialize database
    await create_tables(settings.db_path)

    # Validate provider configuration (warn-only; missing keys serve mocks)
    from .provider_config import validate_provider_config
    validate_provider_config(settings)

    # Register storage and meta_client in the app-level registry so that
    # components running outside of FastAPI's DI (e.g. investigate auto-trigger)
    # can reach them without import cycles.
    from .meta_client import get_meta_client
    from .provider import build_accounted_client
    from .deps import register_app_deps
    _startup_client = get_meta_client()
    _accounted_client = build_accounted_client(
        delegate=_startup_client,
        db_path=settings.db_path,
        provider=settings.vision_provider,
        budget_limit_usd=settings.budget_limit_usd,
        budget_reserve_usd=settings.budget_reserve_usd,
    )
    register_app_deps(storage=storage, meta_client=_accounted_client)

    # Recover recordings stuck in PROCESSING from a prior crash/restart
    await _recover_stuck_recordings(settings, storage)

    logger.info(f"FindIt started. Data dir: {data_dir}, DB: {settings.db_path}")
    yield
    logger.info("FindIt shutting down")


async def _recover_stuck_recordings(settings, storage) -> None:
    """
    On startup, find any recordings left in PROCESSING state from a prior crash
    and requeue them. PROCESSING means the background task was lost mid-flight.
    """
    import aiosqlite
    from .database import get_db
    from .meta_client import get_meta_client
    from .models import ProcessingStatus
    from .processor import process_recording
    from .provider import build_accounted_client

    async with get_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        stuck = await (await db.execute(
            "SELECT recording_id, original_filename FROM recordings WHERE processing_status IN (?, ?)",
            (ProcessingStatus.PROCESSING.value, ProcessingStatus.PENDING.value),
        )).fetchall()
        if stuck:
            logger.info("Recovering %d unprocessed recording(s)…", len(stuck))
            for row in stuck:
                await db.execute(
                    "UPDATE recordings SET processing_status = ? WHERE recording_id = ?",
                    (ProcessingStatus.PENDING.value, row["recording_id"]),
                )
            await db.commit()

    # Limit concurrent reprocessing to avoid overwhelming ffmpeg + Meta API.
    _recovery_sem = asyncio.Semaphore(3)

    async def _reprocess(row):
        async with _recovery_sem:
            rid = row["recording_id"]
            raw_client = get_meta_client()
            meta_client = build_accounted_client(
                delegate=raw_client,
                db_path=settings.db_path,
                provider=settings.vision_provider,
                budget_limit_usd=settings.budget_limit_usd,
                budget_reserve_usd=settings.budget_reserve_usd,
            )
            await process_recording(
                recording_id=rid,
                db_path=settings.db_path,
                storage=storage,
                meta_client=meta_client,
                fps=settings.findit_frame_extraction_fps,
                max_frames=settings.findit_max_frames_per_video,
                max_api_calls=settings.findit_max_api_calls_per_video,
            )

    for row in stuck:
        logger.info("Requeueing recording %s (%s)", row["recording_id"][:8], row["original_filename"])
        asyncio.create_task(_reprocess(row))


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="FindIt",
        description="Cross-video object search",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(ObservabilityMiddleware)

    # Static file serving for evidence frames and thumbnails
    data_dir = Path(settings.data_dir)
    frames_dir = data_dir / "frames"
    thumbnails_dir = data_dir / "thumbnails"
    frames_dir.mkdir(parents=True, exist_ok=True)
    thumbnails_dir.mkdir(parents=True, exist_ok=True)

    app.mount("/frames", StaticFiles(directory=str(frames_dir)), name="frames")
    app.mount("/thumbnails", StaticFiles(directory=str(thumbnails_dir)), name="thumbnails")

    app.include_router(recordings.router, prefix="/api/recordings", tags=["recordings"])
    app.include_router(video_streaming_router.router, prefix="/api/recordings", tags=["recordings"])
    app.include_router(temporal_events_router.router, prefix="/api/recordings", tags=["recordings"])
    app.include_router(guardian_router_module.recording_router, prefix="/api/recordings", tags=["guardian"])
    app.include_router(guardian_router_module.global_router, prefix="/api", tags=["guardian"])
    app.include_router(search_router.router, prefix="/api/search", tags=["search"])
    app.include_router(investigations_router.router, prefix="/api/investigations", tags=["investigations"])
    app.include_router(multi_agent_router.router, prefix="/api/investigations", tags=["investigations"])
    app.include_router(usage_router.router, prefix="/api/usage", tags=["usage"])

    return app


app = create_app()
