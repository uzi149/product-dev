from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.orm import Session

from shared.database.models import StaticPeriod
from shared.storage.keys import static_frame_key

T0 = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)


@pytest.fixture
def camera_id(client):
    return client.post(
        "/cameras", json={"display_name": "Static cam", "source_type": "file"}
    ).json()["id"]


@pytest.fixture
def periods(engine, storage, camera_id):
    """Three periods: 09:00-09:10 with a frame, 09:20-09:30 and 10:00-10:05 without."""
    rows = []
    with Session(engine) as session:
        for start, minutes in ((0, 10), (20, 10), (60, 5)):
            rows.append(
                StaticPeriod(
                    camera_id=camera_id,
                    start_time=T0 + timedelta(minutes=start),
                    end_time=T0 + timedelta(minutes=start + minutes),
                )
            )
        session.add_all(rows)
        session.flush()
        key = static_frame_key(rows[0].camera_id, rows[0].id)
        rows[0].reference_frame_uri = storage.upload_bytes(key, b"\xff\xd8jpeg", "image/jpeg")
        session.commit()
        return [str(r.id) for r in rows]


def test_list_newest_first_without_the_storage_uri(client, camera_id, periods):
    listed = client.get("/static-periods", params={"camera_id": camera_id}).json()
    assert [p["id"] for p in listed] == list(reversed(periods))
    assert [p["has_reference_frame"] for p in listed] == [False, False, True]
    assert all(p["health_status"] == "ok" for p in listed)
    assert "reference_frame_uri" not in listed[0]
    assert "s3://" not in client.get("/static-periods").text


def test_time_filters_use_overlap(client, camera_id, periods):
    def ids(**params):
        params["camera_id"] = camera_id
        return [p["id"] for p in client.get("/static-periods", params=params).json()]

    # 09:05-09:25 overlaps the first two periods.
    window = {
        "start": (T0 + timedelta(minutes=5)).isoformat(),
        "end": (T0 + timedelta(minutes=25)).isoformat(),
    }
    assert ids(**window) == [periods[1], periods[0]]
    assert ids(start=(T0 + timedelta(minutes=40)).isoformat()) == [periods[2]]
    assert client.get("/static-periods", params={"start": "2026-03-07T09:00:00"}).status_code == 422


def test_other_cameras_are_not_listed(client, periods):
    other = client.post("/cameras", json={"display_name": "Other", "source_type": "file"}).json()
    assert client.get("/static-periods", params={"camera_id": other["id"]}).json() == []


def test_reference_frame_redirects_to_the_image(client, periods):
    response = client.get(f"/static-periods/{periods[0]}/reference-frame", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["cache-control"] == "no-store"
    assert httpx.get(response.headers["location"]).content == b"\xff\xd8jpeg"


def test_reference_frame_missing_or_unknown_is_404(client, periods):
    assert client.get(f"/static-periods/{periods[1]}/reference-frame").status_code == 404
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/static-periods/{missing}/reference-frame").status_code == 404
