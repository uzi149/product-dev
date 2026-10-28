"""Celery task: analyse one stored segment (scope §8-9, ADR 0021).

Per window: 6 keyframes saved to MinIO, the VLM's observation, a text embedding of it and a
visual embedding of every keyframe, all written in one transaction, so a window never looks
finished while its vectors are missing. Window and keyframe ids are derived from the segment
id, so a retried or repeated task carries on where it stopped and never duplicates anything.

Temporary trouble (VLM unavailable, MinIO or PostgreSQL down) retries the task, after 30 s,
2 min and 10 min. A used-up quota or a rejected request fails the remaining windows at once.
"""

import logging
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import numpy as np
from celery.signals import worker_ready
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from services.analysis.observations import describe_window, index_text
from services.analysis.prompts import PROMPT_VERSION
from services.analysis.windows import Window, keyframe_id, keyframe_times, plan_windows, window_id
from shared.config import Settings, get_settings
from shared.database.index_settings import IndexSettingsMismatch, ensure_index_settings
from shared.database.models import (
    AnalysisWindow,
    Camera,
    Keyframe,
    ProcessingStatus,
    TextEmbedding,
    VideoSegment,
)
from shared.database.session import get_sessionmaker
from shared.jobs.celery_app import ANALYSIS_QUEUE, ANALYZE_SEGMENT, celery_app, request_analysis
from shared.model_adapters import (
    ImageEmbedder,
    TextEmbedder,
    VisionLanguageModel,
    VLMQuotaExhausted,
    VLMRequestRejected,
    VLMUnavailable,
    get_image_embedder,
    get_text_embedder,
    get_vlm,
)
from shared.model_adapters.images import resize_max_side, to_jpeg
from shared.schemas.observation import InvalidObservation
from shared.storage import NotFound, StorageClient, StorageError, get_storage
from shared.storage.keys import keyframe_key
from shared.video.frames import FrameReadError
from shared.video.keyframes import iter_frames_at

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = (30, 120, 600)


class TransientError(Exception):
    """The VLM, MinIO or PostgreSQL was unreachable; the task can be tried again."""


class _Refused(Exception):
    """The VLM refused (quota used up, request rejected): the other windows would fail too."""


@dataclass
class Models:
    vlm: VisionLanguageModel
    text: TextEmbedder
    image: ImageEmbedder


def default_models() -> Models:
    return Models(get_vlm(), get_text_embedder(), get_image_embedder())


def _is_transient(error: Exception) -> bool:
    return isinstance(error, (VLMUnavailable, OperationalError)) or (
        isinstance(error, StorageError) and not isinstance(error, NotFound)
    )


def process_segment(
    segment_id: UUID,
    session_factory: Callable[[], Session] | None = None,
    storage: StorageClient | None = None,
    settings: Settings | None = None,
    models: Models | None = None,
) -> None:
    """Analyse every window of the segment that isn't done yet, then set the segment's status.

    Raises TransientError when the work should be tried again later.
    """
    session_factory = session_factory or get_sessionmaker()
    storage = storage or get_storage()
    settings = settings or get_settings()
    try:
        with session_factory() as session:
            _process(session, segment_id, storage, settings, models)
    except Exception as error:
        if _is_transient(error):
            raise TransientError(str(error)) from error
        raise


def _process(
    session: Session,
    segment_id: UUID,
    storage: StorageClient,
    settings: Settings,
    models: Models | None,
) -> None:
    segment = session.get(VideoSegment, segment_id)
    if segment is None:
        log.warning("segment %s does not exist (deleted?); nothing to analyse", segment_id)
        return
    if segment.status == ProcessingStatus.DONE:
        return  # delivered twice (late acks): already finished
    models = models or default_models()
    try:
        ensure_index_settings(session, models.text.name, models.image.name)
        session.commit()
    except IndexSettingsMismatch as error:
        session.rollback()
        log.error("segment %s is not analysed: %s", segment_id, error)
        return

    segment.status = ProcessingStatus.PROCESSING
    session.commit()
    camera = session.get(Camera, segment.camera_id)
    windows = plan_windows(
        segment.duration_ms / 1000,
        settings.analysis_window_seconds,
        settings.window_overlap_seconds,
    )
    done = set(
        session.scalars(
            select(AnalysisWindow.id).where(
                AnalysisWindow.segment_id == segment.id,
                AnalysisWindow.processing_status == ProcessingStatus.DONE,
            )
        )
    )
    todo = [w for w in windows if window_id(segment.id, w.number) not in done]
    if todo:
        with tempfile.TemporaryDirectory(prefix="sentinel-analysis-") as tmp:
            video = Path(tmp, "segment.mp4")
            try:
                storage.download_file(storage.key_from_uri(segment.storage_uri), video)
                for window, frames in _window_frames(video, todo, settings.keyframes_per_window):
                    _analyse_window(
                        session, storage, settings, models, segment, camera, window, frames
                    )
            except (NotFound, FrameReadError, _Refused) as error:
                session.rollback()
                reason = (
                    "the video is no longer in storage"
                    if isinstance(error, NotFound)
                    else str(error)
                )
                log.error("segment %s can't be analysed: %s", segment.id, reason)
                _fail_unfinished(session, segment, todo, reason)
                return
    _finish(session, segment, windows)


def _window_frames(
    video: Path, windows: list[Window], per_window: int
) -> Iterator[tuple[Window, list[tuple[float, np.ndarray]]]]:
    """Each window with its keyframes as (seconds into the segment, frame), as soon as all of
    them have been read: one pass through the video, at most a few windows' frames in memory."""
    wanted = {w.number: keyframe_times(w, per_window) for w in windows}
    users: dict[float, list[int]] = {}
    for number, times in wanted.items():
        for t in times:
            users.setdefault(t, []).append(number)
    collected: dict[int, dict[float, tuple[float, np.ndarray]]] = {w.number: {} for w in windows}
    by_number = {w.number: w for w in windows}
    for target, t, frame in iter_frames_at(video, list(users)):
        for number in users[target]:
            collected[number][target] = (t, frame)
            if len(collected[number]) == len(wanted[number]):
                got = collected.pop(number)
                yield by_number[number], [got[time] for time in wanted[number]]


def _set_window(
    session: Session,
    segment: VideoSegment,
    window: Window,
    status: ProcessingStatus,
    error: str | None = None,
) -> AnalysisWindow:
    """The window's row, created on first use, with this status."""
    wid = window_id(segment.id, window.number)
    row = session.get(AnalysisWindow, wid)
    if row is None:
        row = AnalysisWindow(
            id=wid,
            segment_id=segment.id,
            camera_id=segment.camera_id,
            start_time=segment.start_time + timedelta(seconds=window.start),
            end_time=segment.start_time + timedelta(seconds=window.end),
        )
        session.add(row)
    row.processing_status = status
    row.error = error[:1000] if error else None
    return row


def _analyse_window(
    session: Session,
    storage: StorageClient,
    settings: Settings,
    models: Models,
    segment: VideoSegment,
    camera: Camera,
    window: Window,
    frames: list[tuple[float, np.ndarray]],
) -> None:
    """Analyse one window. A window the model can't describe is `failed` and the next one
    follows; a refused request raises _Refused, because every other window would fail too."""
    _set_window(session, segment, window, ProcessingStatus.PROCESSING)
    session.commit()
    pictures = [resize_max_side(frame, settings.snapshot_width) for _, frame in frames]
    try:
        described = describe_window(
            models.vlm,
            pictures,
            camera.display_name,
            [t - window.start for t, _ in frames],
            window.seconds,
        )
    except InvalidObservation as error:
        log.warning("segment %s window %d: %s", segment.id, window.number, error)
        _set_window(session, segment, window, ProcessingStatus.FAILED, str(error))
        session.commit()
        return
    except (VLMQuotaExhausted, VLMRequestRejected) as error:
        raise _Refused(f"the VLM refused the request ({type(error).__name__}): {error}") from error

    observation = described.observation
    wid = window_id(segment.id, window.number)
    uris = [
        storage.upload_bytes(keyframe_key(camera.id, wid, n), to_jpeg(picture), "image/jpeg")
        for n, picture in enumerate(pictures, 1)
    ]
    text = index_text(observation)
    text_vector = models.text.embed_documents([text])[0]
    picture_vectors = models.image.embed_images(pictures)

    # One transaction: the window is only `done` together with its keyframes and vectors.
    session.execute(delete(Keyframe).where(Keyframe.analysis_window_id == wid))
    session.execute(delete(TextEmbedding).where(TextEmbedding.analysis_window_id == wid))
    row = _set_window(session, segment, window, ProcessingStatus.DONE)
    reply = described.reply
    row.vlm_description = observation.summary
    row.structured_observation_json = {
        **observation.model_dump(),
        "prompt_version": PROMPT_VERSION,
        "attempts": described.attempts,
        "latency_ms": reply.latency_ms,
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
    }
    row.vlm_model = reply.model
    row.confidence = observation.overall_confidence
    session.add_all(
        Keyframe(
            id=keyframe_id(wid, n),
            analysis_window_id=wid,
            timestamp=segment.start_time + timedelta(seconds=t),
            storage_uri=uri,
            visual_embedding=vector,
        )
        for n, ((t, _), uri, vector) in enumerate(
            zip(frames, uris, picture_vectors, strict=True), 1
        )
    )
    session.add(
        TextEmbedding(
            id=uuid5(wid, "text/description"),
            analysis_window_id=wid,
            embedding=text_vector,
            source_text=text,
        )
    )
    session.commit()
    log.info(
        "segment %s window %d done by %s in %.1f s (%s+%s tokens): %s",
        segment.id,
        window.number,
        reply.model,
        reply.latency_ms / 1000,
        reply.prompt_tokens,
        reply.completion_tokens,
        observation.summary,
    )


def _fail_unfinished(
    session: Session, segment: VideoSegment, windows: list[Window], reason: str
) -> None:
    """Every window of `windows` that isn't done becomes `failed`, and so does the segment."""
    for window in windows:
        row = session.get(AnalysisWindow, window_id(segment.id, window.number))
        if row is None or row.processing_status != ProcessingStatus.DONE:
            _set_window(session, segment, window, ProcessingStatus.FAILED, reason)
    segment.status = ProcessingStatus.FAILED
    session.commit()


def _finish(session: Session, segment: VideoSegment, windows: list[Window]) -> None:
    rows = session.scalars(
        select(AnalysisWindow.processing_status).where(AnalysisWindow.segment_id == segment.id)
    ).all()
    finished = len(rows) == len(windows) and all(s == ProcessingStatus.DONE for s in rows)
    segment.status = ProcessingStatus.DONE if finished else ProcessingStatus.FAILED
    session.commit()
    log.info("segment %s: %s (%d window(s))", segment.id, segment.status, len(windows))


def give_up(
    segment_id: UUID,
    message: str,
    settings: Settings | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> None:
    """After the last retry: the unfinished windows and the segment become `failed`."""
    settings = settings or get_settings()
    with (session_factory or get_sessionmaker())() as session:
        segment = session.get(VideoSegment, segment_id)
        if segment is None:
            return
        windows = plan_windows(
            segment.duration_ms / 1000,
            settings.analysis_window_seconds,
            settings.window_overlap_seconds,
        )
        _fail_unfinished(session, segment, windows, message)


@celery_app.task(name=ANALYZE_SEGMENT, bind=True, max_retries=MAX_RETRIES)
def analyze_segment(self, segment_id: str) -> None:
    try:
        process_segment(UUID(segment_id))
    except TransientError as error:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=error, countdown=RETRY_DELAYS[self.request.retries]) from error
        log.error("segment %s: giving up after %d retries: %s", segment_id, self.max_retries, error)
        give_up(UUID(segment_id), f"gave up after {self.max_retries} retries: {error}")


def pending_segments(
    session: Session, since: datetime, until: datetime | None = None
) -> list[UUID]:
    query = select(VideoSegment.id).where(
        VideoSegment.status == ProcessingStatus.PENDING, VideoSegment.start_time >= since
    )
    if until is not None:
        query = query.where(VideoSegment.start_time < until)
    return list(session.scalars(query.order_by(VideoSegment.start_time)))


def sweep(
    session_factory: Callable[[], Session] | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """Queue the `pending` segments of the last ANALYSIS_SWEEP_HOURS, in case their messages
    were lost. Older backlog is only queued on purpose (the requeue command)."""
    settings = settings or get_settings()
    if not settings.analysis_enabled:
        return 0
    since = (now or datetime.now(UTC)) - timedelta(hours=settings.analysis_sweep_hours)
    with (session_factory or get_sessionmaker())() as session:
        ids = pending_segments(session, since)
    return request_analysis(ids, settings)


def _consumes_analysis(sender) -> bool:
    queues = getattr(getattr(sender, "task_consumer", None), "queues", None)
    return queues is None or any(queue.name == ANALYSIS_QUEUE for queue in queues)


@worker_ready.connect
def _on_worker_ready(sender=None, **_) -> None:
    if not _consumes_analysis(sender):
        return
    settings = get_settings()
    if not settings.analysis_enabled:
        log.warning("ANALYSIS_ENABLED is false: new segments stay pending (requeue them later)")
        return
    from shared.model_adapters import resolve_vlm_backend

    log.info(
        "analysis on: VLM %s (%s), embeddings %s + %s",
        settings.vlm_model if resolve_vlm_backend(settings) == "api" else settings.vlm_local_model,
        resolve_vlm_backend(settings),
        settings.text_embedding_model,
        settings.image_embedding_model,
    )
    try:
        with get_sessionmaker()() as session:
            ensure_index_settings(
                session, settings.text_embedding_model, settings.image_embedding_model
            )
            session.commit()
        queued = sweep(settings=settings)
        log.info(
            "queued %d pending segment(s) of the last %d h", queued, settings.analysis_sweep_hours
        )
    except IndexSettingsMismatch as error:
        log.error("analysis will refuse every segment: %s", error)
    except Exception:
        log.exception("the start-up sweep failed; queue pending segments with the requeue command")
