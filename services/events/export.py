"""Export an event's evidence as one MP4, on request (scope §11 "a physical combined MP4 only
when the operator requests an export", step 5.3).

Each video part is cut from its segment and encoded at the first part's size and EXPORT_FPS, so
parts from different segments join cleanly. Between separated parts a 2 s card says how much
footage isn't shown ("5 minutes 12 seconds not shown", the camera, the times on each side), so
the file never makes separated footage look continuous. The pieces are joined without encoding
again and stored at exports/{event_id}.mp4.

`exported_at` is when the evidence was read: an event extended after it (`updated_at` later) has
an out-of-date export, made again on the next request.

With marks (step 5.7, ADR 0038) a second file, exports/{event_id}-marked.mp4: each part is decoded
with OpenCV, the boxes of that moment are drawn (`shared.video.draw`, the player's rule), and the
frames are encoded like any other part. The plain export never changes.
"""

import logging
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import cv2
import numpy as np
from sqlalchemy.orm import Session

from services.events.playback import event_manifest, evidence_rows, gap_wording
from shared.database.models import Camera, Event, EvidenceMarks, ExportStatus, MarksStatus
from shared.database.session import get_sessionmaker
from shared.jobs.celery_app import EXPORT_EVENT, celery_app
from shared.schemas.query import GapPart, VideoPart
from shared.storage import NotFound, StorageClient, StorageError, get_storage
from shared.storage.keys import export_key, marked_export_key
from shared.video.draw import boxes_at, draw_marks
from shared.video.ffmpeg import FFmpegError, concat_clips, encode_part, probe, still_clip
from shared.video.frames import FrameReadError, frame_rate, frames_between

log = logging.getLogger(__name__)

EXPORT_FPS = 25
GAP_CARD_SECONDS = 2.0
_BACKGROUND = (24, 24, 24)
_TEXT = (235, 235, 235)
_MUTED = (160, 160, 160)


def _clock(value: datetime) -> str:
    """The time in this machine's zone (the operator's, on a single-site installation)."""
    return value.astimezone().strftime("%H:%M:%S")


def gap_card(width: int, height: int, gap: GapPart, camera: str, before: datetime,
             after: datetime) -> np.ndarray:  # fmt: skip
    """The picture shown between two separated parts."""
    card = np.full((height, width, 3), _BACKGROUND, np.uint8)
    lines = [
        (f"{gap_wording(gap.duration_ms)} not shown", _TEXT, 1.0),
        (f"{camera}: {_clock(before)} to {_clock(after)}", _MUTED, 0.6),
    ]
    scale_to_width = width / 640
    y = height // 2 - round(20 * scale_to_width)
    for text, colour, size in lines:
        scale = size * scale_to_width
        thickness = max(1, round(2 * scale))
        (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        cv2.putText(card, text, ((width - w) // 2, y), cv2.FONT_HERSHEY_SIMPLEX, scale, colour,
                    thickness, cv2.LINE_AA)  # fmt: skip
        y += h + round(24 * scale_to_width)
    return card


def _marked_part(src: Path, part: VideoPart, marks: EvidenceMarks, work: Path, n: int) -> Path:
    """The part's frames with the marks drawn in, as a file `encode_part` can read."""
    fps = frame_rate(src)
    path = work / f"marked-{n:03d}.avi"
    writer: cv2.VideoWriter | None = None
    count = 0
    try:
        for t, frame in frames_between(src, part.source_start_ms / 1000, part.source_end_ms / 1000):
            shown = boxes_at(
                marks.tracks_json or [], marks.pick_status, str(part.segment_id), t * 1000
            )
            draw_marks(frame, shown)
            if writer is None:
                size = (frame.shape[1], frame.shape[0])
                writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
            writer.write(frame)
            count += 1
    finally:
        if writer is not None:
            writer.release()
    if count == 0:
        raise FrameReadError(
            f"no frames between {part.source_start_ms} and {part.source_end_ms} ms"
        )
    return path


def build_export(
    session: Session,
    storage: StorageClient,
    event: Event,
    out: Path,
    marks: EvidenceMarks | None = None,
) -> int:
    """Write the event's evidence to `out` (with `marks`, drawn in); returns how many video
    parts it has."""
    camera = session.get(Camera, event.camera_id)
    rows = evidence_rows(session, event.id)
    manifest = event_manifest(event, rows)
    videos = [p for p in manifest.parts if isinstance(p, VideoPart)]
    if not videos:
        raise FFmpegError("the event has no stored evidence footage")
    uris = {segment.id: segment.storage_uri for _, segment in rows}
    work = out.parent
    sources: dict[UUID, Path] = {}
    for part in videos:
        if part.segment_id not in sources:
            path = work / f"{part.segment_id}.mp4"
            storage.download_file(storage.key_from_uri(uris[part.segment_id]), path)
            sources[part.segment_id] = path
    first = probe(sources[videos[0].segment_id])
    width, height = first.width - first.width % 2, first.height - first.height % 2

    pieces: list[Path] = []
    previous: VideoPart | None = None
    for n, part in enumerate(manifest.parts):
        piece = work / f"piece-{n:03d}.mp4"
        if isinstance(part, VideoPart) and marks is not None:
            drawn = _marked_part(sources[part.segment_id], part, marks, work, n)
            length = (part.source_end_ms - part.source_start_ms) / 1000
            encode_part(drawn, piece, 0, length, width, height, EXPORT_FPS)
            previous = part
        elif isinstance(part, VideoPart):
            encode_part(sources[part.segment_id], piece, part.source_start_ms / 1000,
                        part.source_end_ms / 1000, width, height, EXPORT_FPS)  # fmt: skip
            previous = part
        else:
            following = next(p for p in manifest.parts[n + 1 :] if isinstance(p, VideoPart))
            image = work / f"gap-{n:03d}.png"
            card = gap_card(width, height, part, camera.display_name, previous.end_time,
                            following.start_time)  # fmt: skip
            cv2.imwrite(str(image), card)
            still_clip(image, piece, GAP_CARD_SECONDS, width, height, EXPORT_FPS)
        pieces.append(piece)
    concat_clips(pieces, out)
    return len(videos)


class _Columns:
    """The plain export's columns on the event, or the marked one's."""

    def __init__(self, marked: bool):
        self.prefix = "marked_export" if marked else "export"
        self.at = "marked_exported_at" if marked else "exported_at"

    def set(self, event: Event, **values) -> None:
        for name, value in values.items():
            setattr(event, self.at if name == "at" else f"{self.prefix}_{name}", value)


def process_export(
    event_id: UUID,
    session_factory: Callable[[], Session] | None = None,
    storage: StorageClient | None = None,
    marked: bool = False,
) -> None:
    session_factory = session_factory or get_sessionmaker()
    storage = storage or get_storage()
    columns = _Columns(marked)
    with session_factory() as session:
        event = session.get(Event, event_id)
        if event is None:
            log.warning("event %s does not exist (deleted?); nothing to export", event_id)
            return
        read_at = datetime.now(UTC)
        key = marked_export_key(event.id) if marked else export_key(event.id)
        try:
            marks = None
            if marked:
                marks = session.query(EvidenceMarks).filter_by(event_id=event.id).first()
                if marks is None or marks.status != MarksStatus.DONE:
                    raise FFmpegError("the marks of this event aren't ready")
            with tempfile.TemporaryDirectory(prefix="sentinel-export-") as tmp:
                out = Path(tmp, "export.mp4")
                parts = build_export(session, storage, event, out, marks)
                uri = storage.upload_file(key, out, content_type="video/mp4")
        except (FFmpegError, FrameReadError, StorageError, NotFound, OSError) as error:
            session.rollback()
            event = session.get(Event, event_id)
            if event is None:
                return
            log.warning("event %s couldn't be exported: %s", event_id, error)
            columns.set(event, status=ExportStatus.FAILED,
                        error=str(error)[:1000] or type(error).__name__)  # fmt: skip
            session.commit()
            return
        event = session.get(Event, event_id)
        if event is None:  # deleted while exporting
            storage.delete(key)
            return
        columns.set(event, status=ExportStatus.DONE, uri=uri, error=None, at=read_at)
        session.commit()
        log.info(
            "event %s exported%s: %d part(s)", event_id, " with marks" if marked else "", parts
        )


@celery_app.task(name=EXPORT_EVENT)
def export_event(event_id: str, marks: bool = False) -> None:
    process_export(UUID(event_id), marked=marks)
