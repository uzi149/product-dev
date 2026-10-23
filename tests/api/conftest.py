"""API tests: FastAPI TestClient + scratch database + the real MinIO."""

import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.deps import get_session, get_storage_client, get_upload_queue
from services.api.main import create_app
from shared.config import get_settings
from shared.database.models import Camera, Upload
from shared.storage.keys import upload_key

FFMPEG = shutil.which("ffmpeg")


@pytest.fixture(autouse=True)
def fernet_key(monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def queued():
    """Upload ids the API queued for ingestion (no Redis or worker needed)."""
    return []


@pytest.fixture
def client(engine, storage, queued):
    app = create_app()

    def db_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = db_session
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_upload_queue] = lambda: queued.append
    with TestClient(app) as test_client:
        yield test_client
    # Remove every object the test created: segments and static frames are stored under the
    # camera id, staged uploads under the upload id.
    with Session(engine) as session:
        for camera_id in session.scalars(select(Camera.id)):
            storage.delete_prefix(f"segments/{camera_id}/")
            storage.delete_prefix(f"static/{camera_id}/")
        for upload_id in session.scalars(select(Upload.id)):
            storage.delete(upload_key(upload_id))


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
