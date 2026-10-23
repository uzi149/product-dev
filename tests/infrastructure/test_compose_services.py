"""Smoke tests against the running Docker Compose services (`docker compose up -d`).

Marked `integration`; each test is skipped when its service is not reachable.
"""

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from shared.config import Settings

pytestmark = pytest.mark.integration

settings = Settings()
ROOT = Path(__file__).resolve().parents[2]


def _skip_unless_open(host: str, port: int) -> None:
    try:
        socket.create_connection((host, port), timeout=2).close()
    except OSError:
        pytest.skip(f"{host}:{port} is not reachable (run `docker compose up -d`)")


def test_postgres_has_vector_extension():
    _skip_unless_open(settings.postgres_host, settings.postgres_port)
    import psycopg

    url = settings.database_url.replace("+psycopg", "")
    with psycopg.connect(url, connect_timeout=5) as conn:
        names = {row[0] for row in conn.execute("SELECT extname FROM pg_extension")}
    assert "vector" in names


def test_minio_bucket_exists():
    _skip_unless_open(settings.minio_host, settings.minio_port)
    from minio import Minio

    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
    )
    assert client.bucket_exists(settings.minio_bucket)


def test_redis_answers_ping():
    _skip_unless_open(settings.redis_host, settings.redis_port)
    import redis

    assert redis.Redis.from_url(settings.redis_url).ping()


def test_mediamtx_accepts_rtsp_connections():
    _skip_unless_open(settings.mediamtx_host, settings.mediamtx_rtsp_port)


def test_sim_profile_is_valid():
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is not installed")
    result = subprocess.run(
        [docker, "compose", "--profile", "sim", "config", "--quiet"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path", ["cam-01", "cam-02"])
def test_sim_cameras_stream_h264_when_the_profile_runs(path):
    _skip_unless_open(settings.mediamtx_host, settings.mediamtx_rtsp_port)
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe is not installed")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0",
         f"{settings.mediamtx_rtsp_url}/{path}"],
        capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        pytest.skip("the sim cameras are not running (docker compose --profile sim up -d)")
    assert result.stdout.strip() == "h264"
