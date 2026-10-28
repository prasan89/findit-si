import abc
import hashlib
import re
import os
import uuid
from pathlib import Path
from typing import Optional
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


def sniff_video_container(data: bytes) -> Optional[str]:
    """Identify a video container from magic bytes, or reject clear non-video.

    Returns 'mp4' (covers .mp4/.mov), 'avi', 'mkv' (covers .mkv/.webm), or
    None when the bytes are unrecognized (allowed — the decoder decides).
    Raises ValueError only when the content is clearly not video (text, HTML,
    JSON, PDF, still images), so mislabeled uploads fail fast at the edge.
    """
    head = bytes(data[:12])
    if len(head) < 8:
        return None
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "mkv"
    _NON_VIDEO_PREFIXES = (
        b"{", b"[", b"<", b"%PDF", b"GIF8", b"\x89PNG", b"\xff\xd8\xff",
        b"BM", b"%!", b"MZ",
    )
    if head.startswith(_NON_VIDEO_PREFIXES):
        raise ValueError("File content does not look like a video file.")
    return None

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

    @staticmethod
    def _validate_recording_id(recording_id: str) -> None:
        if not re.match(r'^[0-9a-fA-F\-]{36}$', recording_id or ""):
            raise ValueError(f"Invalid recording_id: {recording_id!r}")

    async def save_video(self, recording_id: str, filename: str, data: bytes) -> str:
        self._validate_recording_id(recording_id)
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
        self._validate_recording_id(recording_id)
        dest = self.thumbnails_dir / f"{recording_id}.jpg"
        async with aiofiles.open(dest, 'wb') as f:
            await f.write(data)
        return str(dest.relative_to(self.data_dir))

    def _resolve_within_data_dir(self, relative_path: str) -> Path:
        # Phase 6: is_relative_to instead of str.startswith — the prefix
        # check wrongly allowed siblings such as "<data_dir>_evil/...".
        # Empty paths and the base directory itself are rejected too.
        if not relative_path or not str(relative_path).strip():
            raise ValueError("Path traversal detected: empty path")
        base = self.data_dir.resolve()
        p = (self.data_dir / relative_path).resolve()
        if p != base and not p.is_relative_to(base):
            raise ValueError("Path traversal detected")
        if p == base:
            raise ValueError("Path traversal detected")
        return p

    async def get_video_path(self, relative_path: str) -> Path:
        return self._resolve_within_data_dir(relative_path)

    async def get_frame_path(self, relative_path: str) -> Path:
        return self._resolve_within_data_dir(relative_path)

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
        # Phase 6: traversal check added (previously resolved + unlinked raw).
        p = self._resolve_within_data_dir(relative_path)
        if p.exists():
            p.unlink()

    async def frame_exists(self, relative_path: str) -> bool:
        p = await self.get_frame_path(relative_path)
        return p.exists()
