"""FindIt FastAPI application."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .database import create_tables
from .storage import LocalStorageBackend
from .routes import recordings, search as search_router, investigations as investigations_router, usage as usage_router
from .routes import multi_agent_investigations as multi_agent_router
from .routes import video_streaming as video_streaming_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    data_dir = Path(settings.data_dir)

    # Ensure data directories exist
    storage = LocalStorageBackend(data_dir)
    await storage.ensure_dirs()

    # Initialize database
    await create_tables(settings.db_path)

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
            "SELECT recording_id, original_filename FROM recordings WHERE processing_status = ?",
            (ProcessingStatus.PROCESSING.value,),
        )).fetchall()
        if stuck:
            logger.info("Recovering %d stuck PROCESSING recording(s)…", len(stuck))
            for row in stuck:
                await db.execute(
                    "UPDATE recordings SET processing_status = ? WHERE recording_id = ?",
                    (ProcessingStatus.PENDING.value, row["recording_id"]),
                )
            await db.commit()

    for row in stuck:
        rid = row["recording_id"]
        logger.info("Requeueing recording %s (%s)", rid[:8], row["original_filename"])
        raw_client = get_meta_client()
        meta_client = build_accounted_client(
            delegate=raw_client,
            db_path=settings.db_path,
            provider=settings.vision_provider,
            budget_limit_usd=settings.budget_limit_usd,
            budget_reserve_usd=settings.budget_reserve_usd,
        )
        asyncio.create_task(process_recording(
            recording_id=rid,
            db_path=settings.db_path,
            storage=storage,
            meta_client=meta_client,
            fps=settings.findit_frame_extraction_fps,
            max_frames=settings.findit_max_frames_per_video,
            max_api_calls=settings.findit_max_api_calls_per_video,
        ))


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
    app.include_router(search_router.router, prefix="/api/search", tags=["search"])
    app.include_router(investigations_router.router, prefix="/api/investigations", tags=["investigations"])
    app.include_router(multi_agent_router.router, prefix="/api/investigations", tags=["investigations"])
    app.include_router(usage_router.router, prefix="/api/usage", tags=["usage"])

    return app


app = create_app()
