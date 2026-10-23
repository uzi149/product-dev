"""Status board and publisher: camera status to Redis, snapshots to MinIO, the heartbeat kept alive."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from services.ingestion.status import StatusBoard, StatusPublisher
from shared.camera_status import HEARTBEAT_KEY, CameraStatus, HeartbeatLock, LiveState, status_key
from shared.storage import StorageError
from shared.storage.keys import snapshot_key
from tests.fakes import FakeRedis

AT = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.down = False

    def upload_bytes(self, key, data, content_type):
        if self.down:
            raise StorageError("MinIO is down (test)")
        self.objects[key] = data


def publisher(board, client=None, storage=None):
    client = client or FakeRedis()
    lock = HeartbeatLock(client, ttl=30)
    lock.acquire()
    return StatusPublisher(board, client, storage or FakeStorage(), lock, ttl=30)


def stored(client, camera_id) -> CameraStatus:
    return CameraStatus.from_json(client.get(status_key(camera_id)))


def test_the_board_tracks_state_activity_and_last_frame():
    board, camera = StatusBoard(), uuid4()
    board.set_state(camera, LiveState.ONLINE, AT)
    board.set_activity(camera, True)
    board.frame(camera, AT + timedelta(seconds=3))
    status = board.get(camera)
    assert (status.state, status.activity, status.last_frame_at) == (
        LiveState.ONLINE,
        True,
        AT + timedelta(seconds=3),
    )
    board.set_state(camera, LiveState.OFFLINE, AT, "the stream ended")
    assert board.get(camera).activity is False  # an offline camera has no activity
    assert board.get(camera).error == "the stream ended"


def test_statuses_and_snapshots_are_published():
    board, client, storage, camera = StatusBoard(), FakeRedis(), FakeStorage(), uuid4()
    board.set_state(camera, LiveState.ONLINE, AT)
    board.snapshot(camera, b"jpeg-1", AT)
    board.snapshot(camera, b"jpeg-2", AT + timedelta(seconds=5))  # only the newest is sent
    publisher(board, client, storage).publish_once()
    assert storage.objects == {snapshot_key(camera): b"jpeg-2"}
    status = stored(client, camera)
    assert status.state == LiveState.ONLINE
    assert status.last_snapshot_at == AT + timedelta(seconds=5)
    assert client.ttl[status_key(camera)] == 30


def test_a_removed_camera_loses_its_status():
    board, client, camera = StatusBoard(), FakeRedis(), uuid4()
    board.set_state(camera, LiveState.ONLINE, AT)
    pub = publisher(board, client)
    pub.publish_once()
    board.remove(camera)
    pub.publish_once()
    assert client.get(status_key(camera)) is None


def test_changes_are_published_at_once_and_everything_is_refreshed_before_it_expires():
    board, client, a, b = StatusBoard(), FakeRedis(), uuid4(), uuid4()
    board.set_state(a, LiveState.ONLINE, AT)
    board.set_state(b, LiveState.ONLINE, AT)
    pub = publisher(board, client)
    pub.publish_once()
    client.delete(status_key(a), status_key(b))
    board.set_activity(a, True)
    pub.publish_once()  # within the refresh interval: only the changed camera
    assert stored(client, a).activity is True
    assert client.get(status_key(b)) is None
    pub._last_full = 0  # the refresh interval has passed
    pub.publish_once()
    assert stored(client, b).state == LiveState.ONLINE
    assert client.ttl[HEARTBEAT_KEY] == 30


def test_redis_or_minio_being_down_does_not_stop_publishing(caplog):
    board, client, storage, camera = StatusBoard(), FakeRedis(), FakeStorage(), uuid4()
    pub = publisher(board, client, storage)
    client.down = storage.down = True
    board.set_state(camera, LiveState.ONLINE, AT)
    board.snapshot(camera, b"jpeg", AT)
    pub.publish_once()
    pub.publish_once()
    assert caplog.text.count("redis unreachable") == 1  # logged once per outage
    client.down = storage.down = False
    board.snapshot(camera, b"jpeg", AT)
    pub._last_full = 0
    pub.publish_once()
    assert stored(client, camera).state == LiveState.ONLINE
    assert storage.objects
