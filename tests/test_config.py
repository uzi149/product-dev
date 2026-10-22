import pytest
from pydantic import ValidationError

from shared.config import Settings, get_settings


@pytest.fixture
def settings():
    """Settings with defaults only: ignores any local .env file."""
    return lambda **kw: Settings(_env_file=None, **kw)


def test_scope_defaults(settings):
    s = settings()
    assert s.analysis_fps == 1
    assert s.rolling_buffer_seconds == 60
    assert s.pre_roll_seconds == 10
    assert s.post_roll_seconds == 10
    assert s.fragment_seconds == 5
    assert s.analysis_window_seconds == 12
    assert s.window_overlap_seconds == 3
    assert s.stable_timeout_seconds == 10
    assert s.health_snapshot_interval_seconds == 600
    assert s.keyframes_per_window == 6
    assert s.default_retention_days == 14


def test_environment_overrides_defaults(settings, monkeypatch):
    monkeypatch.setenv("ANALYSIS_WINDOW_SECONDS", "20")
    monkeypatch.setenv("POSTGRES_PORT", "6000")
    s = settings()
    assert s.analysis_window_seconds == 20
    assert s.postgres_port == 6000


def test_env_file_is_read(tmp_path, monkeypatch):
    monkeypatch.delenv("MINIO_BUCKET", raising=False)
    env = tmp_path / ".env"
    env.write_text("MINIO_BUCKET=other\nBIND_ADDRESS=127.0.0.1\n")
    assert Settings(_env_file=env).minio_bucket == "other"


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_overlap_seconds": 12},
        {"window_overlap_seconds": 20},
        {"pre_roll_seconds": -1},
        {"pre_roll_seconds": 61},
        {"fragment_seconds": 0},
        {"analysis_fps": 0},
    ],
)
def test_invalid_values_are_rejected(settings, overrides):
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_connection_urls(settings):
    s = settings(postgres_password="p@ss/word")
    assert s.database_url == "postgresql+psycopg://sentinel:p%40ss%2Fword@localhost:5433/sentinel"
    assert s.minio_endpoint == "localhost:9000"
    assert s.redis_url == "redis://localhost:6380/0"
    assert s.mediamtx_rtsp_url == "rtsp://localhost:8554"


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
