from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from shared.database.models import Camera, VideoSegment

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def seeded(engine):
    """Two cameras; cam1 has three 60 s segments at 12:00, 12:10 and 12:20."""
    with Session(engine) as session:
        cam1 = Camera(display_name="cam1", source_type="file")
        cam2 = Camera(display_name="cam2", source_type="file")
        session.add_all([cam1, cam2])
        session.flush()
        for camera, minutes in ((cam1, 0), (cam1, 10), (cam1, 20), (cam2, 5)):
            start = T0 + timedelta(minutes=minutes)
            session.add(
                VideoSegment(
                    camera_id=camera.id,
                    start_time=start,
                    end_time=start + timedelta(seconds=60),
                    duration_ms=60_000,
                    storage_uri=f"s3://sentinel/segments/{camera.id}/x.mp4",
                )
            )
        session.commit()
        return str(cam1.id), str(cam2.id)


def starts(response):
    return [s["start_time"][11:16] for s in response.json()]


def test_lists_newest_first_and_filters_by_camera(client, seeded):
    cam1, cam2 = seeded
    assert starts(client.get("/segments")) == ["12:20", "12:10", "12:05", "12:00"]
    assert starts(client.get("/segments", params={"camera_id": cam1})) == [
        "12:20",
        "12:10",
        "12:00",
    ]
    assert starts(client.get("/segments", params={"camera_id": cam2})) == ["12:05"]


def test_time_filter_uses_overlap(client, seeded):
    cam1, _ = seeded
    params = {"camera_id": cam1}

    def query(start, end):
        return starts(
            client.get(
                "/segments", params=params | {"start": start.isoformat(), "end": end.isoformat()}
            )
        )

    # Range starts 30 s into the 12:00 segment, so it still overlaps it.
    assert query(T0 + timedelta(seconds=30), T0 + timedelta(minutes=11)) == ["12:10", "12:00"]
    # Range that begins right after 12:00 segment ended (12:01) and ends before 12:10 starts.
    assert query(T0 + timedelta(minutes=2), T0 + timedelta(minutes=9)) == []
    assert starts(
        client.get("/segments", params=params | {"start": (T0 + timedelta(minutes=15)).isoformat()})
    ) == ["12:20"]


def test_paging(client, seeded):
    first = client.get("/segments", params={"limit": 2}).json()
    rest = client.get("/segments", params={"limit": 2, "offset": 2}).json()
    assert len(first) == 2 and len(rest) == 2
    assert {s["id"] for s in first}.isdisjoint(s["id"] for s in rest)
    assert client.get("/segments", params={"limit": 0}).status_code == 422


def test_naive_times_are_rejected(client):
    assert client.get("/segments", params={"start": "2026-01-01T12:00:00"}).status_code == 422


def test_unknown_segment_is_404(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/segments/{missing}").status_code == 404
    assert client.get(f"/segments/{missing}/playback").status_code == 404
