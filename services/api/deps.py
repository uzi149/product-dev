from collections.abc import Callable
from uuid import UUID

import redis

from shared.camera_status import get_redis as _get_redis
from shared.database.session import get_session
from shared.jobs.celery_app import queue_ingest_upload
from shared.storage import StorageClient, get_storage


def get_storage_client() -> StorageClient:
    return get_storage()


def get_upload_queue() -> Callable[[UUID], None]:
    """Queues the ingestion of a staged upload; tests replace it."""
    return queue_ingest_upload


def get_redis() -> redis.Redis:
    """Live camera status (written by `python -m services.ingestion`); tests replace it."""
    return _get_redis()


__all__ = ["get_redis", "get_session", "get_storage_client", "get_upload_queue"]
