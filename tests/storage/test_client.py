"""Integration tests against the MinIO from `docker compose up -d`. Skipped when unreachable."""

import socket
import time
import uuid
from datetime import timedelta

import httpx
import pytest

from shared.config import Settings
from shared.storage import NotFound, StorageClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
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
def prefix(storage):
    """A unique key prefix per test, removed afterwards, so real data is never touched."""
    prefix = f"test-{uuid.uuid4()}/"
    yield prefix
    storage.delete_prefix(prefix)


def test_ensure_bucket_is_idempotent(storage):
    storage.ensure_bucket()
    storage.ensure_bucket()


def test_upload_file_download_and_exists(storage, prefix, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"\x00\x01video-bytes" * 1000)
    key = f"{prefix}clip.mp4"

    assert not storage.exists(key)
    uri = storage.upload_file(key, source)
    assert uri == f"s3://{storage.bucket}/{key}"
    assert storage.exists(key)

    copy = tmp_path / "copy.mp4"
    storage.download_file(key, copy)
    assert copy.read_bytes() == source.read_bytes()


def test_upload_bytes_sets_content_type(storage, prefix):
    key = f"{prefix}frame.jpg"
    storage.upload_bytes(key, b"jpeg-bytes", "image/jpeg")
    response = httpx.get(storage.presigned_get_url(key))
    assert response.status_code == 200
    assert response.content == b"jpeg-bytes"
    assert response.headers["content-type"] == "image/jpeg"


def test_presigned_url_serves_the_file_with_range_requests(storage, prefix, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(bytes(range(256)) * 40)
    key = f"{prefix}clip.mp4"
    storage.upload_file(key, source)
    url = storage.presigned_get_url(key)

    assert httpx.get(url).content == source.read_bytes()
    part = httpx.get(url, headers={"Range": "bytes=10-19"})
    assert part.status_code == 206
    assert part.content == source.read_bytes()[10:20]


def test_expired_presigned_url_is_refused(storage, prefix):
    key = f"{prefix}a.txt"
    storage.upload_bytes(key, b"x", "text/plain")
    url = storage.presigned_get_url(key, expires=timedelta(seconds=1))
    time.sleep(2.5)
    assert httpx.get(url).status_code == 403


def test_delete_and_delete_prefix(storage, prefix):
    for name in ("a", "b", "sub/c"):
        storage.upload_bytes(f"{prefix}{name}.txt", b"x", "text/plain")

    storage.delete(f"{prefix}a.txt")
    assert not storage.exists(f"{prefix}a.txt")
    storage.delete(f"{prefix}a.txt")  # already gone: no error

    assert storage.delete_prefix(prefix) == 2
    assert not storage.exists(f"{prefix}sub/c.txt")


def test_missing_object_raises_not_found(storage, prefix, tmp_path):
    with pytest.raises(NotFound):
        storage.download_file(f"{prefix}missing.mp4", tmp_path / "x.mp4")
