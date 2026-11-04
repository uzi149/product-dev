"""Deleting a camera with all its data (step 3.6, ADR 0026).

The database cascades from the camera to its segments, static periods, analysis windows,
keyframes, text embeddings, uploads, trigger assignments and query results. The files are
removed by prefix afterwards. Refused while the camera has events or any of its footage is
event evidence (ADR 0007), while an RTSP camera is still recording, and while one of its uploads
is being processed.
"""

import logging
from dataclasses import dataclass

import redis
from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from shared.camera_status import delete_status, read_statuses
from shared.database.models import (
    AnalysisWindow,
    Camera,
    Event,
    EventEvidence,
    ProcessingStatus,
    SourceType,
    StaticPeriod,
    Upload,
    VideoSegment,
)
from shared.storage import StorageClient, StorageError
from shared.storage.keys import upload_key

log = logging.getLogger(__name__)


class StorageUnavailable(Exception):
    """MinIO can't be reached: nothing was deleted."""


@dataclass
class DeletePreview:
    segments: int
    static_periods: int
    analysed_windows: int
    uploads: int
    # Why the camera can't be deleted now; None when it can.
    blocker: str | None


def _count(session: Session, model, camera_id, *where) -> int:
    query = select(func.count()).select_from(model).where(model.camera_id == camera_id, *where)
    return session.scalar(query)


def deletion_blocker(session: Session, client: redis.Redis, camera: Camera) -> str | None:
    """Why the camera can't be deleted now, or None."""
    evidence = select(EventEvidence.id).join(
        VideoSegment, EventEvidence.segment_id == VideoSegment.id
    )
    has_events = exists(select(Event.id).where(Event.camera_id == camera.id))
    is_evidence = exists(evidence.where(VideoSegment.camera_id == camera.id))
    if session.scalar(select(has_events | is_evidence)):
        return "It has events, or its footage is evidence for one; those events must go first"
    if camera.source_type == SourceType.RTSP:
        if camera.enabled:
            return "It is still recording. Disable it first; recording stops within 30 s"
        try:
            recording = bool(read_statuses(client, [camera.id]))
        except redis.RedisError:
            return "Can't check that recording has stopped: Redis is not reachable"
        if recording:
            return "Recording is still stopping (within 30 s); try again shortly"
    busy = (ProcessingStatus.PENDING, ProcessingStatus.PROCESSING)
    if _count(session, Upload, camera.id, Upload.status.in_(busy)):
        return "One of its uploads is still being processed; try again when it has finished"
    return None


def preview(session: Session, client: redis.Redis, camera: Camera) -> DeletePreview:
    """What deleting the camera removes, and whether it can be deleted now."""
    return DeletePreview(
        segments=_count(session, VideoSegment, camera.id),
        static_periods=_count(session, StaticPeriod, camera.id),
        analysed_windows=_count(
            session,
            AnalysisWindow,
            camera.id,
            AnalysisWindow.processing_status == ProcessingStatus.DONE,
        ),
        uploads=_count(session, Upload, camera.id),
        blocker=deletion_blocker(session, client, camera),
    )


def camera_prefixes(camera_id) -> list[str]:
    """Every object prefix that belongs to the camera (shared/storage/keys.py)."""
    return [f"{kind}/{camera_id}/" for kind in ("segments", "keyframes", "static", "snapshots")]


def delete_camera(
    session: Session, storage: StorageClient, client: redis.Redis, camera: Camera
) -> None:
    """Delete the camera's rows, then its files and live status. Call deletion_blocker first.

    Raises StorageUnavailable (nothing changed) when MinIO is down. Files that can't be deleted
    after the rows are gone are logged: nothing points to them any more.
    """
    if not storage.is_healthy():
        raise StorageUnavailable("storage (MinIO) is not reachable; nothing was deleted")
    camera_id, name = camera.id, camera.display_name
    upload_ids = list(session.scalars(select(Upload.id).where(Upload.camera_id == camera_id)))
    # One statement: the database cascades, without loading thousands of rows into the session.
    session.execute(delete(Camera).where(Camera.id == camera_id))
    session.commit()
    log.info("camera %s (%s) deleted", camera_id, name)

    removed = 0
    for prefix in camera_prefixes(camera_id):
        try:
            removed += storage.delete_prefix(prefix)
        except StorageError:
            log.warning(
                "camera %s: files under %s were not deleted", camera_id, prefix, exc_info=True
            )
    for upload_id in upload_ids:
        try:
            storage.delete(upload_key(upload_id))
        except StorageError:
            log.warning("camera %s: staged upload %s was not deleted", camera_id, upload_id)
    log.info("camera %s: %d file(s) deleted", camera_id, removed)
    try:
        delete_status(client, camera_id)
    except redis.RedisError:
        pass  # it expires by itself
