"""Application configuration from environment variables."""
import os
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    data_dir: str = "./data"
    meta_api_key: str = ""
    meta_api_base_url: str = "https://api.llama.com"
    findit_max_video_size_mb: int = 500
    findit_frame_extraction_fps: float = 1.0
    findit_max_frames_per_video: int = 300
    findit_max_api_calls_per_video: int = 300

    @property
    def db_path(self) -> str:
        return str(Path(self.data_dir) / "findit.db")

    @property
    def max_video_size_bytes(self) -> int:
        return self.findit_max_video_size_mb * 1024 * 1024

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
