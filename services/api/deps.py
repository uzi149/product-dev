from collections.abc import Callable
from uuid import UUID

from shared.database.session import get_session
from shared.jobs.celery_app import queue_ingest_upload
from shared.storage import StorageClient, get_storage


def get_storage_client() -> StorageClient:
    return get_storage()


def get_upload_queue() -> Callable[[UUID], None]:
    """Queues the ingestion of a staged upload; tests replace it."""
    return queue_ingest_upload


__all__ = ["get_session", "get_storage_client", "get_upload_queue"]
