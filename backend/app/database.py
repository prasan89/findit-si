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
                updated_at TEXT NOT NULL,
                content_hash TEXT
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
                prompt_version TEXT NOT NULL DEFAULT '2',
                analysis_result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (content_hash, model_version, prompt_version)
            )
        """)

        # P0.1: structured per-frame failure forensics (exact failing text).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS frame_failures (
                failure_id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE CASCADE,
                video_timestamp_seconds REAL NOT NULL,
                error_code TEXT NOT NULL,
                finish_reason TEXT,
                model_version TEXT,
                raw_response TEXT,
                created_at TEXT NOT NULL
            )
        """)

        # RealityTrace: camera metadata on recordings
        await db.execute("""
            CREATE TABLE IF NOT EXISTS recording_camera_meta (
                recording_id TEXT PRIMARY KEY REFERENCES recordings(recording_id) ON DELETE CASCADE,
                camera_name TEXT,
                camera_location TEXT
            )
        """)

        # RealityTrace: structured events extracted from observations
        await db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE CASCADE,
                timestamp_seconds REAL NOT NULL,
                timestamp_absolute TEXT,
                has_reliable_timestamp INTEGER NOT NULL DEFAULT 0,
                event_type TEXT NOT NULL,
                actor_description TEXT,
                object_descriptions TEXT NOT NULL DEFAULT '[]',
                action_description TEXT NOT NULL,
                scene_description TEXT,
                evidence_frames TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0.5,
                observed_facts TEXT NOT NULL DEFAULT '[]',
                inferred_claims TEXT NOT NULL DEFAULT '[]',
                uncertainty_notes TEXT NOT NULL DEFAULT '[]',
                source_observation_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                start_seconds REAL,
                end_seconds REAL,
                entities_json TEXT,
                action_sequence_json TEXT
            )
        """)

        # RealityTrace: cross-camera links between events
        await db.execute("""
            CREATE TABLE IF NOT EXISTS event_links (
                link_id TEXT PRIMARY KEY,
                source_event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                target_event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                confidence REAL NOT NULL,
                link_type TEXT NOT NULL DEFAULT 'candidate',
                explanation TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_recording_id
            ON events(recording_id)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_timestamp
            ON events(timestamp_seconds)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_links_source
            ON event_links(source_event_id)
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

        # Phase 2: AI usage accounting
        await db.execute("""
            CREATE TABLE IF NOT EXISTS model_usage (
                usage_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                operation TEXT NOT NULL,
                recording_id TEXT,
                tokens_in INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0,
                images_processed INTEGER NOT NULL DEFAULT 0,
                latency_ms REAL NOT NULL DEFAULT 0,
                success INTEGER NOT NULL DEFAULT 1,
                estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
                fallback_from TEXT,
                created_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_model_usage_created_at
            ON model_usage(created_at)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_model_usage_provider_model
            ON model_usage(provider, model)
        """)

        # Phase 3: Multi-agent investigation execution traces
        await db.execute("""
            CREATE TABLE IF NOT EXISTS investigation_cache (
                cache_key TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                response_json TEXT NOT NULL,
                idempotency_key TEXT,
                created_at TEXT NOT NULL,
                hits INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_investigation_cache_idem
            ON investigation_cache(idempotency_key)
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS investigation_traces (
                trace_id TEXT PRIMARY KEY,
                investigation_id TEXT NOT NULL,
                query TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                total_duration_ms REAL,
                total_cost_usd REAL NOT NULL DEFAULT 0.0,
                agent_results TEXT NOT NULL DEFAULT '[]',
                final_report TEXT,
                recording_ids TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'RUNNING'
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_investigation_traces_status
            ON investigation_traces(status)
        """)

        # Temporal event detection: per-event evidence JSON + refine audit log
        await db.execute("""
            CREATE TABLE IF NOT EXISTS temporal_events (
                event_id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE CASCADE,
                category TEXT NOT NULL,
                start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL,
                timestamp_seconds REAL NOT NULL,
                timestamp_source TEXT NOT NULL DEFAULT 'coarse_grid',
                confidence REAL NOT NULL DEFAULT 0.5,
                single_frame_candidate INTEGER NOT NULL DEFAULT 0,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS temporal_refine_log (
                refine_id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE CASCADE,
                start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                dense_seconds REAL NOT NULL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'COMPLETED',
                uncovered_spans TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_temporal_events_recording
            ON temporal_events(recording_id)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_temporal_events_span
            ON temporal_events(start_seconds, end_seconds)
        """)

        # Idempotent migrations for existing databases
        try:
            await db.execute("ALTER TABLE recordings ADD COLUMN camera_name TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE recordings ADD COLUMN camera_location TEXT")
        except Exception:
            pass
        # Phase 5: trace observability columns (nullable; older DBs migrate on boot)
        for _ddl in (
            "ALTER TABLE investigation_traces ADD COLUMN code_revision TEXT",
            "ALTER TABLE investigation_traces ADD COLUMN model_config TEXT",
            "ALTER TABLE investigation_traces ADD COLUMN usage_summary TEXT",
            # Phase 4: event span columns (nullable; older DBs migrate on boot)
            "ALTER TABLE events ADD COLUMN start_seconds REAL",
            "ALTER TABLE events ADD COLUMN end_seconds REAL",
            # Entity tracks + action sequences (JSON; nullable; older DBs migrate on boot)
            "ALTER TABLE events ADD COLUMN entities_json TEXT",
            "ALTER TABLE events ADD COLUMN action_sequence_json TEXT",
            # Phase 6: upload content hash for duplicate detection/reporting
            "ALTER TABLE recordings ADD COLUMN content_hash TEXT",
            # Phase 6: prompt-versioned cache keys (existing rows default to v1
            # and never hit under the current prompt version)
            "ALTER TABLE frame_cache ADD COLUMN prompt_version TEXT DEFAULT '1'",
            # Phase 7: two-pass analysis — track which pass produced each observation
            "ALTER TABLE observations ADD COLUMN analysis_pass TEXT DEFAULT 'general'",
            # Phase 8: upload understanding — per-recording column for UI
            "ALTER TABLE recordings ADD COLUMN upload_understanding_status TEXT DEFAULT 'pending'",
        ):
            try:
                await db.execute(_ddl)
            except Exception:
                pass

        # Upload understanding results: structured entities/actions/changes per
        # video segment from the general-purpose UPLOAD_UNDERSTANDING_PROMPT.
        # Created with CREATE TABLE IF NOT EXISTS for idempotency.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS upload_understandings (
                segment_id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE CASCADE,
                segment_hash TEXT NOT NULL,
                start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL,
                scene_context TEXT,
                entities_json TEXT NOT NULL DEFAULT '[]',
                actions_json TEXT NOT NULL DEFAULT '[]',
                interactions_json TEXT NOT NULL DEFAULT '[]',
                changes_json TEXT NOT NULL DEFAULT '[]',
                unusual_observations_json TEXT NOT NULL DEFAULT '[]',
                uncertain_observations_json TEXT NOT NULL DEFAULT '[]',
                static_scene INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_upload_understandings_recording
            ON upload_understandings(recording_id)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_upload_understandings_span
            ON upload_understandings(recording_id, start_seconds, end_seconds)
        """)

        # Guardian: upload-time hazard alerts
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guardian_alerts (
                alert_id                    TEXT PRIMARY KEY,
                recording_id                TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE CASCADE,
                hazard_category             TEXT NOT NULL,
                severity                    TEXT NOT NULL,
                confidence                  REAL NOT NULL DEFAULT 0.5,
                timestamp_seconds           REAL NOT NULL,
                time_range_start            REAL NOT NULL,
                time_range_end              REAL NOT NULL,
                description                 TEXT NOT NULL,
                evidence_frames_json        TEXT NOT NULL DEFAULT '[]',
                supporting_segment_ids_json TEXT NOT NULL DEFAULT '[]',
                review_status               TEXT NOT NULL DEFAULT 'new',
                is_mocked                   INTEGER NOT NULL DEFAULT 0,
                dedup_key                   TEXT NOT NULL,
                created_at                  TEXT NOT NULL,
                updated_at                  TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_guardian_alerts_dedup
            ON guardian_alerts(recording_id, dedup_key)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_guardian_alerts_recording
            ON guardian_alerts(recording_id)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_guardian_alerts_review_status
            ON guardian_alerts(review_status)
        """)

        await db.commit()
