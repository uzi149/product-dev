"""Keep only the active parts of a video file (MVP plan §2 "Uploaded MP4 handling").

find activity -> add pre-roll and post-roll -> merge overlaps -> cut each part to H.264 ->
MinIO + `video_segments`. The parts in between become `static_periods` with a reference frame.
No Celery here, so it can be tested directly; `tasks.py` wraps it.
"""

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from shared.config import Settings
from shared.database.models import Camera, StaticPeriod, VideoSegment
from shared.storage import StorageClient
from shared.storage.keys import segment_key, static_frame_key
from shared.video.change_detector import resolve_params
from shared.video.detect import find_intervals
from shared.video.ffmpeg import cut_h264, extract_frame_jpeg, probe

log = logging.getLogger(__name__)

# Gaps shorter than this between stored parts are not worth a static period.
MIN_STATIC_SECONDS = 1.0


@dataclass(frozen=True)
class Interval:
    """Seconds from the start of the file."""

    start: float
    end: float
    score: float = 0.0


@dataclass
class IngestResult:
    segments: list[VideoSegment] = field(default_factory=list)
    static_periods: list[StaticPeriod] = field(default_factory=list)
    # Every object written, so a caller whose commit fails can remove them.
    keys: list[str] = field(default_factory=list)


def find_active_intervals(path: str | Path, camera: Camera, settings: Settings) -> list[Interval]:
    params = resolve_params(camera.detection_config, settings)
    return [
        Interval(e.start, e.end, e.score)
        for e in find_intervals(path, params, fps=camera.analysis_fps)
    ]


def pad_and_merge(
    intervals: list[Interval], pre_roll: float, post_roll: float, duration: float
) -> list[Interval]:
    """Add pre/post roll, clamp to the file, merge parts that overlap or touch.

    A merged part's score is the mean of its parts, weighted by their length (at least 1 s:
    one sample).
    """
    padded = sorted(
        (
            (max(0.0, i.start - pre_roll), min(duration, i.end + post_roll), i)
            for i in intervals
            if i.start < duration
        ),
        key=lambda item: item[:2],
    )
    merged: list[Interval] = []
    weighted = weight = 0.0
    for start, end, interval in padded:
        length = max(interval.end - interval.start, 1.0)
        if merged and start <= merged[-1].end:
            last = merged[-1]
            weighted += interval.score * length
            weight += length
            merged[-1] = Interval(last.start, max(last.end, end), weighted / weight)
        else:
            weighted, weight = interval.score * length, length
            merged.append(Interval(start, end, interval.score))
    return merged


def static_gaps(intervals: list[Interval], duration: float) -> list[Interval]:
    """The parts of the file outside the (merged) intervals, without the very short ones."""
    gaps, position = [], 0.0
    for interval in intervals:
        gaps.append(Interval(position, interval.start))
        position = max(position, interval.end)
    gaps.append(Interval(position, duration))
    return [g for g in gaps if g.end - g.start >= MIN_STATIC_SECONDS]


def ingest_file(
    session: Session,
    storage: StorageClient,
    path: str | Path,
    camera: Camera,
    start_time: datetime,
    settings: Settings,
) -> IngestResult:
    """Store the active parts and static periods of `path`; rows are added, not committed.

    On failure, the objects already written are deleted before the error is raised.
    """
    duration = probe(path).duration_s
    if duration <= 0:
        raise ValueError("the video has no duration")
    active = find_active_intervals(path, camera, settings)
    parts = pad_and_merge(active, settings.pre_roll_seconds, settings.post_roll_seconds, duration)
    log.info("%s: %d active interval(s) -> %d part(s) to store", path, len(active), len(parts))

    result = IngestResult()
    try:
        with tempfile.TemporaryDirectory(prefix="sentinel-ingest-") as tmp:
            for n, part in enumerate(parts):
                result.segments.append(
                    _store_segment(
                        storage, path, Path(tmp, f"part-{n}.mp4"), camera, start_time, part, result
                    )
                )
            for n, gap in enumerate(static_gaps(parts, duration)):
                result.static_periods.append(
                    _store_static(
                        storage, path, Path(tmp, f"static-{n}.jpg"), camera, start_time, gap, result
                    )
                )
    except Exception:
        remove_objects(storage, result.keys)
        raise
    session.add_all([*result.segments, *result.static_periods])
    return result


def remove_objects(storage: StorageClient, keys: list[str]) -> None:
    """Best effort: a failure here must not hide the error that caused the clean-up."""
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            log.warning("could not delete %s after a failed ingestion", key, exc_info=True)


def _store_segment(storage, src, dst, camera, start_time, part, result) -> VideoSegment:
    cut_h264(src, dst, part.start, part.end)
    info = probe(dst)
    segment_id = uuid4()
    begins = start_time + timedelta(seconds=part.start)
    key = segment_key(camera.id, begins, segment_id)
    uri = storage.upload_file(key, dst, content_type="video/mp4")
    result.keys.append(key)
    return VideoSegment(
        id=segment_id,
        camera_id=camera.id,
        start_time=begins,
        end_time=begins + timedelta(milliseconds=info.duration_ms),
        duration_ms=info.duration_ms,
        storage_uri=uri,
        activity_score=part.score,
        codec=info.codec,
        width=info.width,
        height=info.height,
    )


def _store_static(storage, src, dst, camera, start_time, gap, result) -> StaticPeriod:
    extract_frame_jpeg(src, (gap.start + gap.end) / 2, dst)
    period_id = uuid4()
    key = static_frame_key(camera.id, period_id)
    uri = storage.upload_file(key, dst, content_type="image/jpeg")
    result.keys.append(key)
    return StaticPeriod(
        id=period_id,
        camera_id=camera.id,
        start_time=start_time + timedelta(seconds=gap.start),
        end_time=start_time + timedelta(seconds=gap.end),
        reference_frame_uri=uri,
    )
