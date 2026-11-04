"""Live camera status in Redis (MVP plan §3.1, ADR 0014): written by ingestion, read by the API.

camera_status:{camera_id}   JSON CameraStatus, expires unless ingestion refreshes it
ingestion:heartbeat         "host:pid" of the running ingestion process; also its lock
"""

import json
import os
import socket
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from uuid import UUID

import redis

from shared.config import get_settings

STATUS_PREFIX = "camera_status:"
HEARTBEAT_KEY = "ingestion:heartbeat"


class LiveState(StrEnum):
    CONNECTING = "connecting"
    ONLINE = "online"
    OFFLINE = "offline"
    # Derived by the API, never written by ingestion:
    DISABLED = "disabled"
    NOT_RUNNING = "not_running"  # no ingestion heartbeat
    UNKNOWN = "unknown"  # Redis can't be reached


@dataclass
class CameraStatus:
    state: LiveState
    since: datetime | None = None
    activity: bool = False  # the change detector is in an active interval
    last_frame_at: datetime | None = None
    last_snapshot_at: datetime | None = None
    error: str | None = None  # credentials are masked before they get here

    def to_json(self) -> str:
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "CameraStatus":
        data = json.loads(raw)
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        for key in ("since", "last_frame_at", "last_snapshot_at"):
            if values.get(key):
                values[key] = datetime.fromisoformat(values[key])
        values["state"] = LiveState(values["state"])
        return cls(**values)


def status_key(camera_id: UUID) -> str:
    return f"{STATUS_PREFIX}{camera_id}"


def write_status(client: redis.Redis, camera_id: UUID, status: CameraStatus, ttl: int) -> None:
    client.set(status_key(camera_id), status.to_json(), ex=ttl)


def delete_status(client: redis.Redis, camera_id: UUID) -> None:
    client.delete(status_key(camera_id))


def read_statuses(client: redis.Redis, camera_ids: list[UUID]) -> dict[UUID, CameraStatus]:
    if not camera_ids:
        return {}
    values = client.mget([status_key(i) for i in camera_ids])
    return {i: CameraStatus.from_json(v) for i, v in zip(camera_ids, values, strict=True) if v}


def ingestion_running(client: redis.Redis) -> bool:
    return bool(client.exists(HEARTBEAT_KEY))


# Compare-and-set scripts, so a process never extends or removes another one's heartbeat.
_REFRESH = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) end
return 0
"""
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end
return 0
"""


class HeartbeatLock:
    """The ingestion heartbeat doubles as a lock: only one ingestion process runs at a time."""

    def __init__(self, client: redis.Redis, ttl: int, key: str = HEARTBEAT_KEY):
        self.client = client
        self.ttl = ttl
        self.key = key
        self.token = f"{socket.gethostname()}:{os.getpid()}"

    def acquire(self) -> bool:
        return bool(self.client.set(self.key, self.token, nx=True, ex=self.ttl))

    def holder(self) -> str | None:
        value = self.client.get(self.key)
        return value.decode() if isinstance(value, bytes) else value

    def refresh(self) -> bool:
        return bool(self.client.eval(_REFRESH, 1, self.key, self.token, self.ttl))

    def release(self) -> None:
        self.client.eval(_RELEASE, 1, self.key, self.token)


@lru_cache
def get_redis() -> redis.Redis:
    # Short timeouts: a status page must not hang when Redis is down.
    return redis.Redis.from_url(
        get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2
    )
