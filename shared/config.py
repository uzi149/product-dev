"""Application settings, read from environment variables and the `.env` file.

The ingestion values come from the Technical Scope §7.2 and are configuration, not
hard-coded behaviour. Defaults are the "Initial configuration" values.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # PostgreSQL
    postgres_user: str = "sentinel"
    postgres_password: str = "sentinel_dev_password"
    postgres_db: str = "sentinel"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5433

    # MinIO
    minio_access_key: str = "sentinel"
    minio_secret_key: str = "sentinel_dev_password"
    minio_bucket: str = "sentinel"
    minio_host: str = "127.0.0.1"
    minio_port: int = 9000
    # Address browsers use to reach MinIO (presigned URLs). Empty = same as the API's address.
    minio_public_endpoint: str = ""

    # Redis
    redis_host: str = "127.0.0.1"
    redis_port: int = 6380
    # Database number: the job queue, camera status and the ingestion heartbeat all live in it.
    redis_db: int = Field(default=0, ge=0, le=15)

    # MediaMTX (RTSP server)
    mediamtx_host: str = "127.0.0.1"
    mediamtx_rtsp_port: int = 8554

    # Encrypts camera source URIs (ADR 0008)
    fernet_key: str = ""

    # Vision-language model (ADR 0019). "auto": the OpenAI-compatible API when a key is set,
    # otherwise the local model in this process. With a key, frames go to that server: test
    # videos only; real camera footage needs a local server (vlm_base_url) or no key.
    vlm_backend: Literal["auto", "api", "local"] = "auto"
    vlm_base_url: str = "https://openrouter.ai/api/v1"
    vlm_api_key: SecretStr = SecretStr("")
    # The reference code's name for the same key; used when VLM_API_KEY is empty.
    openrouter_api_key: SecretStr = SecretStr("")
    vlm_model: str = "qwen/qwen3-vl-30b-a3b-instruct"
    vlm_local_model: str = "Qwen/Qwen2.5-VL-3B-Instruct"
    # Longest side of every image sent to the model.
    vlm_image_max_side: int = Field(default=640, ge=64)
    vlm_timeout_seconds: float = Field(default=180, gt=0)
    vlm_max_retries: int = Field(default=6, ge=0)

    # Embedding models, always local and fixed per installation (ADR 0020): recorded in
    # `index_settings` on first use; a process configured for other models is refused.
    text_embedding_model: str = "BAAI/bge-base-en-v1.5"
    image_embedding_model: str = "google/siglip2-base-patch16-224"
    # CPU threads for the embedding models; 0 = half the cores, so FFmpeg and the API keep some.
    embedding_threads: int = Field(default=0, ge=0)

    # API
    max_upload_mb: int = Field(default=2048, gt=0)
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Ingestion and analysis (Technical Scope §7.2)
    analysis_fps: int = Field(default=1, gt=0)
    rolling_buffer_seconds: int = Field(default=60, gt=0)
    pre_roll_seconds: int = Field(default=10, ge=0)
    post_roll_seconds: int = Field(default=10, ge=0)
    fragment_seconds: int = Field(default=5, gt=0)
    analysis_window_seconds: int = Field(default=12, gt=0)
    window_overlap_seconds: int = Field(default=3, ge=0)
    stable_timeout_seconds: int = Field(default=10, gt=0)
    health_snapshot_interval_seconds: int = Field(default=600, gt=0)

    # Change detection defaults (ADR 0010); cameras override them in `detection_config`.
    detect_pixel_delta: int = Field(default=25, ge=1, le=255)
    # 0.1 %: a single person in a wide view covers only 0.05-0.3 % of the picture (ADR 0010).
    detect_changed_fraction: float = Field(default=0.001, gt=0, le=1)
    detect_ssim_threshold: float = Field(default=0.7, gt=0, le=1)
    detect_activation_samples: int = Field(default=2, ge=1)
    detect_background_alpha: float = Field(default=0.05, gt=0, le=1)
    # Frames are downscaled to this width before they're compared.
    detect_width: int = Field(default=320, ge=32)

    # Live ingestion (ADR 0014)
    # Holds buffer/ (the rolling fragments) and outbox/ (footage waiting for MinIO + PostgreSQL).
    ingestion_data_dir: Path = Path("data")
    camera_reload_seconds: int = Field(default=30, gt=0)
    snapshot_interval_seconds: int = Field(default=5, gt=0)
    # Frames for change detection and snapshots are scaled down to at most this width.
    snapshot_width: int = Field(default=960, ge=64)
    # Long activity is stored as back-to-back segments of at most this length (ADR 0015).
    max_segment_seconds: int = Field(default=30, gt=0)
    camera_status_ttl_seconds: int = Field(default=30, gt=0)
    # No frame for this long and the stream is restarted.
    stream_stall_seconds: int = Field(default=15, gt=0)
    reconnect_max_backoff_seconds: int = Field(default=30, gt=0)

    # MVP plan §2
    keyframes_per_window: int = Field(default=6, gt=0)
    default_retention_days: int = Field(default=14, gt=0)

    @model_validator(mode="after")
    def _check_windows(self) -> "Settings":
        if self.window_overlap_seconds >= self.analysis_window_seconds:
            raise ValueError("window_overlap_seconds must be smaller than analysis_window_seconds")
        if self.pre_roll_seconds > self.rolling_buffer_seconds:
            raise ValueError("pre_roll_seconds can't be longer than rolling_buffer_seconds")
        if self.max_segment_seconds < 2 * self.fragment_seconds:
            raise ValueError("max_segment_seconds must be at least two fragments")
        return self

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{quote(self.postgres_user, safe='')}:{quote(self.postgres_password, safe='')}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def minio_endpoint(self) -> str:
        return f"{self.minio_host}:{self.minio_port}"

    @property
    def minio_signing_endpoint(self) -> str:
        return self.minio_public_endpoint or self.minio_endpoint

    @property
    def vlm_key(self) -> str:
        """The VLM API key: VLM_API_KEY, else OPENROUTER_API_KEY, else empty (local model)."""
        return self.vlm_api_key.get_secret_value() or self.openrouter_api_key.get_secret_value()

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def mediamtx_rtsp_url(self) -> str:
        return f"rtsp://{self.mediamtx_host}:{self.mediamtx_rtsp_port}"

    @property
    def buffer_dir(self) -> Path:
        return self.ingestion_data_dir / "buffer"

    @property
    def outbox_dir(self) -> Path:
        return self.ingestion_data_dir / "outbox"


@lru_cache
def get_settings() -> Settings:
    return Settings()
