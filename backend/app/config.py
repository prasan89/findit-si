"""Application configuration from environment variables."""
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    data_dir: str = "./data"

    # Auth: META_API_KEY is set in the environment; MODEL_API_KEY also accepted.
    # get_meta_client() reads both; this field is for Settings introspection only.
    meta_api_key: str = ""
    meta_api_base_url: str = "https://api.meta.ai/v1"

    findit_max_video_size_mb: int = 500
    findit_frame_extraction_fps: float = 1.0
    findit_max_frames_per_video: int = 300
    findit_max_api_calls_per_video: int = 300

    # AI credit budget safeguards
    budget_limit_usd: float = 150.0
    budget_reserve_usd: float = 10.0

    # Model routing
    vision_provider: str = "meta"
    vision_model: str = "muse-spark-1.3"

    # Retry settings
    max_api_attempts: int = 3
    base_backoff_seconds: float = 1.0

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
        "protected_namespaces": ("settings_",),
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
