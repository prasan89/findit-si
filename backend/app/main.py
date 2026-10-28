"""FindIt FastAPI application."""
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
from .routes import recordings, search as search_router, investigations as investigations_router

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

    logger.info(f"FindIt started. Data dir: {data_dir}, DB: {settings.db_path}")
    yield
    logger.info("FindIt shutting down")


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
    app.include_router(search_router.router, prefix="/api/search", tags=["search"])
    app.include_router(investigations_router.router, prefix="/api/investigations", tags=["investigations"])

    return app


app = create_app()
