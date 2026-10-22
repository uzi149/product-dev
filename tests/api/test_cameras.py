import time

from sqlalchemy import text

RTSP = "rtsp://admin:s3cret@192.168.1.10:554/stream1?token=abc"


def create(client, **overrides):
    body = {"display_name": "CAM-01", "source_type": "rtsp", "source_uri": RTSP} | overrides
    return client.post("/cameras", json=body)


def test_create_and_read_camera_never_returns_the_uri(client):
    response = create(client)
    assert response.status_code == 201
    camera = response.json()
    assert camera["has_source_uri"] is True
    assert camera["source_uri_masked"] == "rtsp://***@192.168.1.10:554/stream1"
    assert camera["retention_days"] == 14
    assert camera["segment_count"] == 0
    assert "s3cret" not in response.text
    assert "source_uri" not in camera
    assert "source_uri_encrypted" not in camera

    fetched = client.get(f"/cameras/{camera['id']}")
    assert fetched.json() == camera
    assert "s3cret" not in client.get("/cameras").text


def test_source_uri_is_stored_encrypted(client, engine):
    create(client)
    with engine.connect() as conn:
        stored = conn.execute(text("SELECT source_uri_encrypted FROM cameras")).scalar_one()
    assert stored != RTSP
    assert "s3cret" not in stored


def test_file_camera_has_no_uri(client):
    camera = create(client, source_type="file", source_uri=None).json()
    assert camera["has_source_uri"] is False
    assert camera["source_uri_masked"] is None


def test_validation(client):
    assert create(client, source_uri=None).status_code == 422
    assert create(client, source_uri="http://example.com/x").status_code == 422
    assert create(client, source_type="file").status_code == 422
    assert create(client, source_type="usb").status_code == 422
    assert create(client, display_name="").status_code == 422
    assert create(client, retention_days=0).status_code == 422


def test_patch_updates_fields_and_replaces_the_uri(client, engine):
    camera = create(client).json()
    response = client.patch(
        f"/cameras/{camera['id']}",
        json={
            "display_name": "Front door",
            "enabled": False,
            "retention_days": 30,
            "source_uri": "rtsp://10.0.0.5/live",
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert (body["display_name"], body["enabled"], body["retention_days"]) == (
        "Front door",
        False,
        30,
    )
    assert body["source_uri_masked"] == "rtsp://10.0.0.5/live"
    with engine.connect() as conn:
        assert (
            "10.0.0.5"
            not in conn.execute(text("SELECT source_uri_encrypted FROM cameras")).scalar_one()
        )


def test_patch_rejects_bad_input(client):
    camera = create(client).json()
    url = f"/cameras/{camera['id']}"
    assert client.patch(url, json={"source_uri": "http://x"}).status_code == 422
    assert client.patch(url, json={"display_name": None}).status_code == 422
    assert client.patch(url, json={"retention_days": 0}).status_code == 422
    file_camera = create(client, source_type="file", source_uri=None).json()
    assert (
        client.patch(f"/cameras/{file_camera['id']}", json={"source_uri": "rtsp://x/y"}).status_code
        == 422
    )


def test_unknown_camera_is_404(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/cameras/{missing}").status_code == 404
    assert client.patch(f"/cameras/{missing}", json={"enabled": False}).status_code == 404
    assert client.post(f"/cameras/{missing}/test-connection").status_code == 404


def test_connection_test_fails_quickly_on_a_closed_port(client):
    camera = create(client, source_uri="rtsp://127.0.0.1:1/none").json()
    started = time.monotonic()
    result = client.post(f"/cameras/{camera['id']}/test-connection").json()
    assert result["ok"] is False
    assert result["error"]
    assert time.monotonic() - started < 10


def test_connection_test_for_a_file_camera(client):
    camera = create(client, source_type="file", source_uri=None).json()
    result = client.post(f"/cameras/{camera['id']}/test-connection").json()
    assert result["ok"] is False
    assert "no live source" in result["error"]


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "database": True, "storage": True}


def test_cors_allows_the_dev_server_on_any_local_port_only(client):
    def preflight(origin):
        return client.options(
            "/cameras",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
        )

    assert preflight("http://localhost:3001").headers["access-control-allow-origin"] == (
        "http://localhost:3001"
    )
    assert "access-control-allow-origin" not in preflight("https://evil.example").headers
