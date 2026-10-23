"""Object keys in the bucket. This is the only place that knows the layout.

segments/{camera_id}/{yyyy}/{mm}/{dd}/{segment_id}.mp4
keyframes/{camera_id}/{window_id}/{n}.jpg
static/{camera_id}/{static_period_id}.jpg
snapshots/{camera_id}/latest.jpg
exports/{event_id}.mp4
uploads/{upload_id}          staged upload, deleted after ingestion (ADR 0012)
"""

from datetime import UTC, datetime
from uuid import UUID


def segment_key(camera_id: UUID, start_time: datetime, segment_id: UUID) -> str:
    """The date in the path is the segment's start time in UTC."""
    if start_time.tzinfo is None:
        raise ValueError("start_time must be timezone-aware")
    day = start_time.astimezone(UTC)
    return f"segments/{camera_id}/{day:%Y}/{day:%m}/{day:%d}/{segment_id}.mp4"


def keyframe_key(camera_id: UUID, window_id: UUID, n: int) -> str:
    return f"keyframes/{camera_id}/{window_id}/{n}.jpg"


def static_frame_key(camera_id: UUID, static_period_id: UUID) -> str:
    return f"static/{camera_id}/{static_period_id}.jpg"


def snapshot_key(camera_id: UUID) -> str:
    return f"snapshots/{camera_id}/latest.jpg"


def export_key(event_id: UUID) -> str:
    return f"exports/{event_id}.mp4"


def upload_key(upload_id: UUID) -> str:
    return f"uploads/{upload_id}"
