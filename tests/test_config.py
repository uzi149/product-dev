from pathlib import Path

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


def test_change_detection_defaults(settings):
    s = settings()
    assert s.detect_pixel_delta == 25
    assert s.detect_changed_fraction == 0.001  # 0.1 % (ADR 0010 amendment)
    assert s.detect_ssim_threshold == 0.7
    assert s.detect_activation_samples == 2
    assert s.detect_background_alpha == 0.05
    assert s.detect_width == 320


def test_live_ingestion_defaults(settings):
    s = settings()
    assert s.max_segment_seconds == 30  # ADR 0015
    assert s.camera_reload_seconds == 30
    assert s.snapshot_interval_seconds == 5
    assert s.snapshot_width == 960
    assert s.camera_status_ttl_seconds == 30
    assert s.stream_stall_seconds == 15
    assert s.reconnect_max_backoff_seconds == 30
    assert s.buffer_dir == Path("data") / "buffer"
    assert s.outbox_dir == Path("data") / "outbox"


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
        {"detect_changed_fraction": 0},
        {"detect_ssim_threshold": 1.5},
        {"max_segment_seconds": 9},  # less than two 5 s fragments
    ],
)
def test_invalid_values_are_rejected(settings, overrides):
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_connection_urls(settings):
    s = settings(postgres_password="p@ss/word")
    assert s.database_url == "postgresql+psycopg://sentinel:p%40ss%2Fword@127.0.0.1:5433/sentinel"
    assert s.minio_endpoint == "127.0.0.1:9000"
    assert s.redis_url == "redis://127.0.0.1:6380/0"
    assert settings(redis_db=1).redis_url == "redis://127.0.0.1:6380/1"
    assert s.mediamtx_rtsp_url == "rtsp://127.0.0.1:8554"


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
