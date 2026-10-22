"""Application settings, read from environment variables and the `.env` file.

The ingestion values come from the Technical Scope §7.2 and are configuration, not
hard-coded behaviour. Defaults are the "Initial configuration" values.
"""

from functools import lru_cache
from urllib.parse import quote

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # PostgreSQL
    postgres_user: str = "sentinel"
    postgres_password: str = "sentinel_dev_password"
    postgres_db: str = "sentinel"
    postgres_host: str = "localhost"
    postgres_port: int = 5433

    # MinIO
    minio_access_key: str = "sentinel"
    minio_secret_key: str = "sentinel_dev_password"
    minio_bucket: str = "sentinel"
    minio_host: str = "localhost"
    minio_port: int = 9000

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6380

    # MediaMTX (RTSP server)
    mediamtx_host: str = "localhost"
    mediamtx_rtsp_port: int = 8554

    # Encrypts camera source URIs (used from step 1.4)
    fernet_key: str = ""

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

    # MVP plan §2
    keyframes_per_window: int = Field(default=6, gt=0)
    default_retention_days: int = Field(default=14, gt=0)

    @model_validator(mode="after")
    def _check_windows(self) -> "Settings":
        if self.window_overlap_seconds >= self.analysis_window_seconds:
            raise ValueError("window_overlap_seconds must be smaller than analysis_window_seconds")
        if self.pre_roll_seconds > self.rolling_buffer_seconds:
            raise ValueError("pre_roll_seconds can't be longer than rolling_buffer_seconds")
        return self

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{quote(self.postgres_user, safe='')}:{quote(self.postgres_password, safe='')}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def minio_endpoint(self) -> str:
        return f"{self.minio_host}:{self.minio_port}"

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def mediamtx_rtsp_url(self) -> str:
        return f"rtsp://{self.mediamtx_host}:{self.mediamtx_rtsp_port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
