from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from shared.config import Settings
from shared.storage import keys
from shared.storage.client import StorageClient

CAMERA = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")


def test_segment_key_uses_utc_date_with_zero_padding():
    key = keys.segment_key(CAMERA, datetime(2026, 3, 7, 9, 5, tzinfo=UTC), OTHER)
    assert key == f"segments/{CAMERA}/2026/03/07/{OTHER}.mp4"


def test_segment_key_converts_to_utc_before_taking_the_date():
    # 01:30 on 8 March at UTC+5 is still 7 March in UTC.
    local = datetime(2026, 3, 8, 1, 30, tzinfo=timezone(timedelta(hours=5)))
    assert keys.segment_key(CAMERA, local, OTHER).startswith(f"segments/{CAMERA}/2026/03/07/")


def test_segment_key_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        keys.segment_key(CAMERA, datetime(2026, 3, 7), OTHER)


def test_other_keys():
    assert keys.keyframe_key(CAMERA, OTHER, 3) == f"keyframes/{CAMERA}/{OTHER}/3.jpg"
    assert keys.static_frame_key(CAMERA, OTHER) == f"static/{CAMERA}/{OTHER}.jpg"
    assert keys.snapshot_key(CAMERA) == f"snapshots/{CAMERA}/latest.jpg"
    assert keys.export_key(OTHER) == f"exports/{OTHER}.mp4"
    assert keys.upload_key(OTHER) == f"uploads/{OTHER}"


def test_uri_round_trip():
    client = StorageClient(Settings(_env_file=None))
    uri = client.to_uri("segments/a.mp4")
    assert uri == "s3://sentinel/segments/a.mp4"
    assert client.key_from_uri(uri) == "segments/a.mp4"
    with pytest.raises(ValueError):
        client.key_from_uri("s3://other-bucket/a.mp4")


def test_signing_endpoint_defaults_to_the_api_endpoint():
    assert Settings(_env_file=None).minio_signing_endpoint == "127.0.0.1:9000"
    public = Settings(_env_file=None, minio_public_endpoint="192.168.1.20:9000")
    assert public.minio_signing_endpoint == "192.168.1.20:9000"


def test_presigned_url_uses_the_public_endpoint_without_contacting_the_server():
    settings = Settings(_env_file=None, minio_public_endpoint="192.168.1.20:9000")
    url = StorageClient(settings).presigned_get_url("segments/a.mp4")
    assert url.startswith("http://192.168.1.20:9000/sentinel/segments/a.mp4?")
