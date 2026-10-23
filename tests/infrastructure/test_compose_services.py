"""Smoke tests against the running Docker Compose services (`docker compose up -d`).

Marked `integration`; each test is skipped when its service is not reachable.
"""

import socket

import pytest

from shared.config import Settings

pytestmark = pytest.mark.integration

settings = Settings()


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
