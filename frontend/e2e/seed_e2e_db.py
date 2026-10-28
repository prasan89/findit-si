"""Deterministic E2E seed: builds a fresh DATA_DIR with one recording + two events.

Run with the backend venv python from the backend/ directory:
    E2E_DATA_DIR=<dir> python <repo>/frontend/e2e/seed_e2e_db.py

Wipes and recreates the directory, so every E2E run starts identical.
No model calls, no network. Fails loudly if an API key is set.
"""
import asyncio
import os
import shutil
import subprocess
import sys

SEED_RECORDING_ID = "11111111-1111-4111-8111-111111111111"
VIDEO_DURATION = 20.0

EVENTS = [
    {
        "event_id": "e2e-ev-redbag",
        "timestamp_seconds": 8.0,
        "event_type": "unattended_object",
        "action_description": "red bag left near door",
        "object_descriptions": ["red bag"],
        "observed_facts": ["Red bag on floor near lobby door"],
        "confidence": 0.9,
        "frames": [(6.0, "before"), (8.0, "during"), (10.0, "after")],
    },
    {
        "event_id": "e2e-ev-person",
        "timestamp_seconds": 14.0,
        "event_type": "person_detected",
        "action_description": "person entered lobby",
        "object_descriptions": ["person"],
        "observed_facts": ["Person walked through lobby entrance"],
        "confidence": 0.85,
        "frames": [(12.0, "before"), (14.0, "during"), (16.0, "after")],
    },
]


async def main() -> None:
    if os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY"):
        sys.exit("Refusing to seed E2E data with an API key set (no live calls in E2E).")
    data_dir = os.environ.get("E2E_DATA_DIR")
    if not data_dir:
        sys.exit("E2E_DATA_DIR is required")
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    fixture = os.path.join(repo_root, "frontend", "e2e", "fixtures", "cctv_sample.mp4")
    if not os.path.exists(fixture):
        sys.exit(f"fixture video missing: {fixture}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg is required to seed E2E frames")

    sys.path.insert(0, os.path.join(repo_root, "backend"))
    from app.database import create_tables, get_db
    from tests.fixtures import insert_event, insert_recording

    if os.path.isdir(data_dir):
        shutil.rmtree(data_dir)
    videos = os.path.join(data_dir, "videos")
    frames_dir = os.path.join(data_dir, "frames", SEED_RECORDING_ID)
    os.makedirs(videos)
    os.makedirs(frames_dir)
    os.makedirs(os.path.join(data_dir, "thumbnails"))
    os.makedirs(os.path.join(data_dir, "tmp"))

    shutil.copy(fixture, os.path.join(videos, f"{SEED_RECORDING_ID}.mp4"))

    for ev in EVENTS:
        for ts, _role in ev["frames"]:
            out = os.path.join(frames_dir, f"e2e_{ev['event_id']}_{ts:.0f}s.jpg")
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", str(ts), "-i", fixture, "-frames:v", "1", out],
                check=True,
            )

    db_path = os.path.join(data_dir, "findit.db")
    await create_tables(db_path)
    async with get_db(db_path) as db:
        import aiosqlite
        db.row_factory = aiosqlite.Row
        await insert_recording(
            db, SEED_RECORDING_ID,
            filename="cctv_seed.mp4",
            processing_status="INDEXED",
            video_path=f"videos/{SEED_RECORDING_ID}.mp4",
            camera_name="E2E Lobby",
            camera_location="Lobby",
        )
        await db.execute(
            "UPDATE recordings SET video_duration_seconds=?, frame_count=? "
            "WHERE recording_id=?",
            (VIDEO_DURATION, 6, SEED_RECORDING_ID),
        )
        for ev in EVENTS:
            frames = [
                {"frame_path": f"frames/{SEED_RECORDING_ID}/e2e_{ev['event_id']}_{ts:.0f}s.jpg",
                 "timestamp_seconds": ts, "role": role}
                for ts, role in ev["frames"]
            ]
            await insert_event(
                db, SEED_RECORDING_ID,
                event_id=ev["event_id"],
                timestamp_seconds=ev["timestamp_seconds"],
                event_type=ev["event_type"],
                action_description=ev["action_description"],
                object_descriptions=ev["object_descriptions"],
                observed_facts=ev["observed_facts"],
                evidence_frames=frames,
                confidence=ev["confidence"],
            )
        await db.commit()
    print(f"seeded E2E data dir: {data_dir}")


if __name__ == "__main__":
    asyncio.run(main())
