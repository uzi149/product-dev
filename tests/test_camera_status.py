"""Camera status in Redis: the format ingestion writes and the API reads, and the heartbeat lock."""

import socket
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import redis

from shared.camera_status import (
    HEARTBEAT_KEY,
    CameraStatus,
    HeartbeatLock,
    LiveState,
    ingestion_running,
    read_statuses,
    status_key,
    write_status,
)
from shared.config import Settings
from tests.fakes import FakeRedis

AT = datetime(2026, 3, 7, 9, 0, 5, tzinfo=UTC)


def test_status_round_trips_through_json():
    status = CameraStatus(
        LiveState.ONLINE,
        since=AT,
        activity=True,
        last_frame_at=AT,
        last_snapshot_at=None,
        error=None,
    )
    assert CameraStatus.from_json(status.to_json()) == status
    assert (
        CameraStatus.from_json('{"state": "offline", "newer_field": 1}').state == LiveState.OFFLINE
    )


def test_statuses_are_written_with_a_ttl_and_read_back_by_camera():
    client, present, missing = FakeRedis(), uuid4(), uuid4()
    write_status(client, present, CameraStatus(LiveState.CONNECTING, since=AT), ttl=30)
    assert client.ttl[status_key(present)] == 30
    statuses = read_statuses(client, [present, missing])
    assert list(statuses) == [present]
    assert statuses[present].state == LiveState.CONNECTING
    assert read_statuses(client, []) == {}


def test_ingestion_is_running_while_its_heartbeat_exists():
    client = FakeRedis()
    assert not ingestion_running(client)
    lock = HeartbeatLock(client, ttl=30)
    assert lock.acquire()
    assert ingestion_running(client)
    lock.release()
    assert not ingestion_running(client)


def test_only_one_process_holds_the_heartbeat():
    client = FakeRedis()
    first, second = HeartbeatLock(client, ttl=30), HeartbeatLock(client, ttl=30)
    second.token = "other-host:1"
    assert first.acquire()
    assert not second.acquire()
    assert second.holder() == first.token
    assert not second.refresh()  # it can't extend someone else's heartbeat
    second.release()  # nor remove it
    assert client.get(HEARTBEAT_KEY) == first.token.encode()
    assert first.refresh()


@pytest.fixture
def real_redis():
    settings = Settings()
    try:
        socket.create_connection((settings.redis_host, settings.redis_port), timeout=2).close()
    except OSError:
        pytest.skip("Redis is not reachable (run `docker compose up -d`)")
    return redis.Redis.from_url(settings.redis_url)


@pytest.mark.integration
def test_the_lock_scripts_work_on_the_real_redis(real_redis):
    key = f"test:heartbeat:{uuid4()}"  # never the real heartbeat: ingestion may be running
    first, second = HeartbeatLock(real_redis, 30, key), HeartbeatLock(real_redis, 30, key)
    second.token = "other-host:1"
    try:
        assert first.acquire() and not second.acquire()
        assert first.refresh() and not second.refresh()
        assert 0 < real_redis.ttl(key) <= 30
        second.release()
        assert real_redis.exists(key)
        first.release()
        assert not real_redis.exists(key)
    finally:
        real_redis.delete(key)
