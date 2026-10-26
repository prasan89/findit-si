import abc
import hashlib
import re
import os
import uuid
from pathlib import Path
import aiofiles
import aiofiles.os

ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}
MAX_FILENAME_LENGTH = 255

def sanitize_filename(filename: str) -> str:
    """Remove path components, unsafe chars; return a safe filename."""
    # Strip path components
    name = Path(filename).name
    # Allow only alphanumerics, dots, hyphens, underscores, spaces
    name = re.sub(r'[^\w\s.\-]', '_', name)
    # Collapse spaces
    name = re.sub(r'\s+', '_', name).strip('._')
    if not name:
        name = 'upload'
    # Enforce length
    stem = Path(name).stem[:200]
    suffix = Path(name).suffix[:10]
    return stem + suffix

def validate_video_extension(filename: str) -> str:
    """Return lowercased extension or raise ValueError."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise ValueError(f"Extension {ext!r} not allowed. Allowed: {ALLOWED_VIDEO_EXTENSIONS}")
    return ext

class StorageBackend(abc.ABC):
    @abc.abstractmethod
    async def save_video(self, recording_id: str, filename: str, data: bytes) -> str: ...
    @abc.abstractmethod
    async def save_frame(self, recording_id: str, frame_id: str, data: bytes) -> str: ...
    @abc.abstractmethod
    async def save_thumbnail(self, recording_id: str, data: bytes) -> str: ...
    @abc.abstractmethod
    async def get_video_path(self, relative_path: str) -> Path: ...
    @abc.abstractmethod
    async def get_frame_path(self, relative_path: str) -> Path: ...
    @abc.abstractmethod
    async def delete_video(self, relative_path: str) -> None: ...
    @abc.abstractmethod
    async def delete_frames_for_recording(self, recording_id: str) -> None: ...
    @abc.abstractmethod
    async def delete_thumbnail(self, relative_path: str) -> None: ...
    @abc.abstractmethod
    async def frame_exists(self, relative_path: str) -> bool: ...

class LocalStorageBackend(StorageBackend):
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.videos_dir = data_dir / 'videos'
        self.frames_dir = data_dir / 'frames'
        self.thumbnails_dir = data_dir / 'thumbnails'
        self.tmp_dir = data_dir / 'tmp'

    async def ensure_dirs(self):
        for d in [self.videos_dir, self.frames_dir, self.thumbnails_dir, self.tmp_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def _safe_recording_dir(self, recording_id: str) -> Path:
        # Validate UUID format to prevent path traversal
        if not re.match(r'^[0-9a-f\-]{36}$', recording_id):
            raise ValueError(f"Invalid recording_id: {recording_id!r}")
        return self.frames_dir / recording_id

    async def save_video(self, recording_id: str, filename: str, data: bytes) -> str:
        safe = sanitize_filename(filename)
        ext = validate_video_extension(safe)
        dest = self.videos_dir / f"{recording_id}{ext}"
        async with aiofiles.open(dest, 'wb') as f:
            await f.write(data)
        return str(dest.relative_to(self.data_dir))

    async def save_frame(self, recording_id: str, frame_id: str, data: bytes) -> str:
        frame_dir = self._safe_recording_dir(recording_id)
        frame_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r'[^\w\-]', '_', frame_id)
        dest = frame_dir / f"{safe_id}.jpg"
        async with aiofiles.open(dest, 'wb') as f:
            await f.write(data)
        return str(dest.relative_to(self.data_dir))

    async def save_thumbnail(self, recording_id: str, data: bytes) -> str:
        dest = self.thumbnails_dir / f"{recording_id}.jpg"
        async with aiofiles.open(dest, 'wb') as f:
            await f.write(data)
        return str(dest.relative_to(self.data_dir))

    async def get_video_path(self, relative_path: str) -> Path:
        p = (self.data_dir / relative_path).resolve()
        if not str(p).startswith(str(self.data_dir.resolve())):
            raise ValueError("Path traversal detected")
        return p

    async def get_frame_path(self, relative_path: str) -> Path:
        p = (self.data_dir / relative_path).resolve()
        if not str(p).startswith(str(self.data_dir.resolve())):
            raise ValueError("Path traversal detected")
        return p

    async def delete_video(self, relative_path: str) -> None:
        p = await self.get_video_path(relative_path)
        if p.exists():
            p.unlink()

    async def delete_frames_for_recording(self, recording_id: str) -> None:
        frame_dir = self._safe_recording_dir(recording_id)
        if frame_dir.exists():
            import shutil
            shutil.rmtree(frame_dir)

    async def delete_thumbnail(self, relative_path: str) -> None:
        p = (self.data_dir / relative_path).resolve()
        if p.exists():
            p.unlink()

    async def frame_exists(self, relative_path: str) -> bool:
        p = await self.get_frame_path(relative_path)
        return p.exists()
