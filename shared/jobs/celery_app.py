"""Celery app shared by the API (which queues tasks) and the workers (ADR 0011).

Run a worker on Windows with:

    celery -A shared.jobs.celery_app worker --pool=solo -Q default,analysis
    celery -A shared.jobs.celery_app worker --pool=solo -Q query      # queries, trigger rules

A second worker on `-Q analysis` alone can run next to it, so a long analysis backlog never
holds up an upload; queries have their own worker for the same reason.

The API queues tasks by name, so it never imports worker code.
"""

import logging
from collections.abc import Iterable
from uuid import UUID

from celery import Celery

from shared.config import Settings, get_settings

log = logging.getLogger(__name__)

DEFAULT_QUEUE = "default"
ANALYSIS_QUEUE = "analysis"
QUERY_QUEUE = "query"

# Task names: the API sends these, the worker modules register them.
INGEST_UPLOAD = "ingestion.ingest_upload"
ANALYZE_SEGMENT = "analysis.analyze_segment"
RUN_QUERY = "query.run_query"
COMPILE_TRIGGER = "triggers.compile_trigger"

celery_app = Celery(
    "sentinel",
    broker=get_settings().redis_url,
    include=[
        "services.ingestion.tasks",
        "services.analysis.tasks",
        "services.query_engine.run",
        "services.triggers.compile",
    ],
)
celery_app.conf.update(
    task_default_queue=DEFAULT_QUEUE,
    task_routes={
        INGEST_UPLOAD: {"queue": DEFAULT_QUEUE},
        ANALYZE_SEGMENT: {"queue": ANALYSIS_QUEUE},
        RUN_QUERY: {"queue": QUERY_QUEUE},
        # Text only, but the local model must not load in the API (ADR 0032).
        COMPILE_TRIGGER: {"queue": QUERY_QUEUE},
    },
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


def queue_run_query(query_id: UUID) -> None:
    """Raises when the broker (Redis) can't be reached."""
    celery_app.send_task(RUN_QUERY, args=[str(query_id)])


def queue_compile_trigger(trigger_id: UUID) -> None:
    """Raises when the broker (Redis) can't be reached."""
    celery_app.send_task(COMPILE_TRIGGER, args=[str(trigger_id)])


def queue_ingest_upload(upload_id: UUID) -> None:
    """Raises when the broker (Redis) can't be reached."""
    celery_app.send_task(INGEST_UPLOAD, args=[str(upload_id)])


def queue_analyze_segment(segment_id: UUID) -> None:
    """Raises when the broker (Redis) can't be reached."""
    celery_app.send_task(ANALYZE_SEGMENT, args=[str(segment_id)])


def request_analysis(segment_ids: Iterable[UUID], settings: Settings | None = None) -> int:
    """Queue the analysis of newly stored segments, if ANALYSIS_ENABLED (ADR 0022).

    Never raises: storing footage must not fail because analysis couldn't be queued. A segment
    that wasn't queued stays `pending`; the worker-start sweep or the requeue command finds it.
    Returns how many were queued.
    """
    settings = settings or get_settings()
    if not settings.analysis_enabled:
        return 0
    queued = 0
    for segment_id in segment_ids:
        try:
            queue_analyze_segment(segment_id)
        except Exception as error:  # noqa: BLE001 - any broker failure leaves it pending
            log.warning("could not queue the analysis of segment %s: %s", segment_id, error)
            continue
        queued += 1
    return queued
