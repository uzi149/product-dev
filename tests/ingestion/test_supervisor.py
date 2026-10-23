"""Supervisor: one worker per enabled RTSP camera, restarted when its settings change."""

from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from services.ingestion.camera_worker import CameraConfig
from services.ingestion.status import StatusBoard
from services.ingestion.supervisor import Supervisor, plan_changes
from shared.camera_status import LiveState
from shared.config import Settings, get_settings
from shared.crypto import encrypt_text
from shared.database.models import Camera


def config(**changes) -> CameraConfig:
    values = {"id": uuid4(), "name": "cam", "source": "rtsp://h/1", "analysis_fps": 1}
    return CameraConfig(**{**values, **changes})


def test_plan_changes_starts_new_stops_removed_and_restarts_changed():
    same, changed, removed, added = config(), config(), config(), config()
    running = {c.id: c for c in (same, changed, removed)}
    edited = CameraConfig(changed.id, "cam", "rtsp://h/2", 1)
    wanted = {same.id: same, changed.id: edited, added.id: added}
    stop, start = plan_changes(running, wanted)
    assert set(stop) == {changed.id, removed.id}
    assert set(start) == {changed.id, added.id}


def test_detection_config_and_frame_rate_changes_restart_the_worker():
    base = config(detection_config={"changed_fraction": 0.001})
    for edited in (
        CameraConfig(base.id, "cam", base.source, 2, base.detection_config),
        CameraConfig(base.id, "cam", base.source, 1, {"changed_fraction": 0.01}),
    ):
        assert plan_changes({base.id: base}, {base.id: edited}) == ([base.id], [base.id])
    assert plan_changes({base.id: base}, {base.id: base}) == ([], [])


class FakeWorker:
    def __init__(self, camera, settings, outbox, board):
        self.camera = camera
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


@pytest.fixture
def fernet_key(monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def supervisor(engine, fernet_key):
    return Supervisor(
        Settings(_env_file=None),
        lambda: Session(engine),
        outbox=None,
        board=StatusBoard(),
        worker_factory=FakeWorker,
    )


def add(session, **values) -> Camera:
    camera = Camera(display_name=values.pop("name", "cam"), **values)
    session.add(camera)
    session.commit()
    return camera


def test_only_enabled_rtsp_cameras_with_a_source_are_read(supervisor, session):
    live = add(
        session,
        name="Door",
        source_type="rtsp",
        source_uri_encrypted=encrypt_text("rtsp://u:p@h/1"),
    )
    add(session, source_type="rtsp", source_uri_encrypted=encrypt_text("rtsp://h/2"), enabled=False)
    add(session, source_type="file")
    wanted = supervisor.load()
    assert list(wanted) == [live.id]
    assert wanted[live.id].source == "rtsp://u:p@h/1"  # decrypted for FFmpeg
    assert wanted[live.id].label == "Door (rtsp://***@h/1)"  # never logged with the password


def test_a_source_that_cant_be_decrypted_shows_as_offline(supervisor, session):
    broken = add(session, source_type="rtsp", source_uri_encrypted="gAAAAA-not-a-token")
    assert supervisor.load() == {}
    status = supervisor.board.get(broken.id)
    assert status.state == LiveState.OFFLINE
    assert "FERNET_KEY" in status.error


def test_sync_starts_restarts_and_stops_workers(supervisor):
    camera = config()
    supervisor.sync({camera.id: camera})
    first = supervisor.workers[camera.id]
    assert first.started

    supervisor.sync({camera.id: camera})  # nothing changed
    assert supervisor.workers[camera.id] is first

    edited = CameraConfig(camera.id, "cam", "rtsp://h/other", 1)
    supervisor.sync({camera.id: edited})
    assert first.stopped
    assert supervisor.workers[camera.id].camera == edited

    supervisor.board.set_state(camera.id, LiveState.ONLINE, None)
    supervisor.sync({})
    assert supervisor.workers == {}
    assert supervisor.board.get(camera.id) is None  # its status is removed too


def test_a_database_outage_keeps_the_running_cameras(supervisor, caplog):
    camera = config()
    supervisor.sync({camera.id: camera})

    def down():
        raise ConnectionError("database down (test)")

    supervisor.session_factory = down
    supervisor.reload()
    assert camera.id in supervisor.workers
    assert "keeping the current one" in caplog.text
