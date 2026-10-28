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

    # Phase 3: Multi-agent investigation
    investigation_timeout_seconds: float = 120.0
    agent_timeout_seconds: float = 30.0
    investigation_budget_usd: float = 2.0
    investigation_model: str = "muse-spark-1.3"

    # Phase 6: Meta API reliability
    meta_connect_timeout_s: float = 10.0
    meta_read_timeout_s: float = 60.0
    meta_text_read_timeout_s: float = 45.0
    meta_backoff_max_s: float = 8.0
    meta_retry_jitter: bool = True
    # P0.1: muse-spark-1.3 spends ~1600-2200 completion tokens on hidden
    # reasoning for vision frames; 2000 starved content to null/truncated.
    # Measured complete responses need ~3300 total; 8000 gives headroom.
    vision_max_output_tokens: int = 8000
    text_max_output_tokens: int = 1500

    # Phase 6: pipeline + cost safeguards
    investigation_max_concurrent: int = 4
    investigation_max_llm_calls: int = 8

    # Phase 8: cost & performance
    llm_max_concurrent: int = 2  # max parallel Meta API requests per process
    agent_parallel_pairs: bool = True  # run independent agent pairs concurrently
    investigation_cache_ttl_s: int = 3600  # 0 = disable result cache
    investigation_cache_max_entries: int = 200

    # Phase 6: upload hardening
    upload_max_recordings: int = 0  # 0 = unlimited
    upload_chunk_bytes: int = 1024 * 1024
    ffmpeg_timeout_s: float = 300.0

    # Phase 6: cache retention (0 = keep indefinitely)
    frame_cache_retention_days: int = 0

    # Phase 6: security boundaries
    api_token: str = ""  # when set, mutating endpoints require X-API-Token
    rate_limit_per_minute: int = 240  # per client IP, expensive POST routes; 0 = off
    max_query_chars: int = 2000
    max_recording_ids: int = 50
    max_form_field_chars: int = 200

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
