"""API tests: FastAPI TestClient + scratch database + the real MinIO."""

import shutil
import socket
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.deps import get_session, get_storage_client
from services.api.main import create_app
from shared.config import Settings, get_settings
from shared.database.models import Camera
from shared.storage import StorageClient

FFMPEG = shutil.which("ffmpeg")


@pytest.fixture(autouse=True)
def fernet_key(monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def storage():
    settings = Settings()
    try:
        socket.create_connection((settings.minio_host, settings.minio_port), timeout=2).close()
    except OSError:
        pytest.skip("MinIO is not reachable (run `docker compose up -d`)")
    client = StorageClient(settings)
    client.ensure_bucket()
    return client


@pytest.fixture
def client(engine, storage):
    app = create_app()

    def db_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = db_session
    app.dependency_overrides[get_storage_client] = lambda: storage
    with TestClient(app) as test_client:
        yield test_client
    # Remove every object the test created (segments are stored under the camera id).
    with Session(engine) as session:
        for camera_id in session.scalars(select(Camera.id)):
            storage.delete_prefix(f"segments/{camera_id}/")


def make_video(path: Path, codec: str = "libx264", seconds: int = 3, extra: tuple = ()) -> Path:
    """Generate a small test video with ffmpeg, so no video file is committed."""
    if FFMPEG is None:
        pytest.skip("ffmpeg is not installed")
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=320x240:rate=10",
            "-c:v",
            codec,
            *extra,
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def h264_video(tmp_path):
    return make_video(tmp_path / "clip.mp4", extra=("-pix_fmt", "yuv420p"))
