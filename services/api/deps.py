from collections.abc import Callable
from uuid import UUID

import redis

from services.query_engine.progress import QueryEvents
from shared.camera_status import get_redis as _get_redis
from shared.database.session import get_session
from shared.jobs import celery_app as jobs
from shared.jobs.celery_app import queue_ingest_upload
from shared.storage import StorageClient, get_storage


def get_storage_client() -> StorageClient:
    return get_storage()


def get_upload_queue() -> Callable[[UUID], None]:
    """Queues the ingestion of a staged upload; tests replace it."""
    return queue_ingest_upload


def get_analysis_queue() -> Callable[[UUID], None]:
    """Queues the analysis of a segment (the Retry buttons); tests replace it."""
    return jobs.queue_analyze_segment


def get_query_queue() -> Callable[[UUID], None]:
    """Queues a query for the query worker (ADR 0028); tests replace it."""
    return jobs.queue_run_query


def get_trigger_queue() -> Callable[[UUID], None]:
    """Queues the compilation of a trigger's rule on the query worker (ADR 0032); tests replace
    it."""
    return jobs.queue_compile_trigger


def get_query_events() -> QueryEvents:
    """Progress notices of a query (Redis pub/sub); tests replace it."""
    return QueryEvents()


def get_redis() -> redis.Redis:
    """Live camera status (written by `python -m services.ingestion`); tests replace it."""
    return _get_redis()


__all__ = [
    "get_analysis_queue",
    "get_query_events",
    "get_query_queue",
    "get_redis",
    "get_session",
    "get_storage_client",
    "get_trigger_queue",
    "get_upload_queue",
]
