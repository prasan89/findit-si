"""Database setup and connection management using aiosqlite."""
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


@asynccontextmanager
async def get_db(db_path: str):
    """Async context manager for getting a database connection."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


async def create_tables(db_path: str) -> None:
    """Create all required tables if they don't exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS recordings (
                recording_id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                upload_timestamp TEXT NOT NULL,
                recording_datetime TEXT,
                recording_timezone TEXT,
                video_path TEXT NOT NULL,
                thumbnail_path TEXT,
                video_duration_seconds REAL,
                file_size_bytes INTEGER,
                processing_status TEXT NOT NULL DEFAULT 'PENDING',
                frame_count INTEGER DEFAULT 0,
                processing_errors TEXT,
                indexing_status_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                observation_id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE CASCADE,
                frame_id TEXT NOT NULL,
                video_timestamp_seconds REAL NOT NULL,
                frame_capture_timestamp TEXT,
                recording_datetime TEXT,
                recording_timezone TEXT,
                object_label TEXT NOT NULL,
                object_attributes TEXT,
                scene_description TEXT,
                source_frame_path TEXT NOT NULL,
                detection_score REAL NOT NULL DEFAULT 0.5,
                model_version TEXT,
                index_version TEXT DEFAULT '1',
                content_hash TEXT,
                raw_model_response TEXT,
                created_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS frame_cache (
                content_hash TEXT NOT NULL,
                model_version TEXT NOT NULL,
                analysis_result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (content_hash, model_version)
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_observations_recording_id
            ON observations(recording_id)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_observations_object_label
            ON observations(object_label)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_recordings_status
            ON recordings(processing_status)
        """)

        await db.commit()
