"""Celery app shared by the API (which queues tasks) and the workers (ADR 0011).

Run a worker on Windows with:

    celery -A shared.jobs.celery_app worker --pool=solo -Q default

The API queues tasks by name, so it never imports worker code.
"""

from uuid import UUID

from celery import Celery

from shared.config import get_settings

DEFAULT_QUEUE = "default"
QUERY_QUEUE = "query"

# Task names: the API sends these, the worker modules register them.
INGEST_UPLOAD = "ingestion.ingest_upload"

celery_app = Celery(
    "sentinel", broker=get_settings().redis_url, include=["services.ingestion.tasks"]
)
celery_app.conf.update(
    task_default_queue=DEFAULT_QUEUE,
    task_routes={INGEST_UPLOAD: {"queue": DEFAULT_QUEUE}},
    task_serializer="json",
    accept_content=["json"],
    # No result backend: lasting job state lives in PostgreSQL (e.g. the uploads table).
    task_ignore_result=True,
    # Tasks run for minutes: take one at a time and acknowledge only when it has finished,
    # so a task lost with a crashed worker is delivered again.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Fail fast when Redis is down instead of retrying a publish for a long time.
    broker_connection_timeout=3,
    task_publish_retry_policy={"max_retries": 2, "interval_start": 0, "interval_step": 0.5},
    broker_connection_retry_on_startup=True,
)


def queue_ingest_upload(upload_id: UUID) -> None:
    """Raises when the broker (Redis) can't be reached."""
    celery_app.send_task(INGEST_UPLOAD, args=[str(upload_id)])
