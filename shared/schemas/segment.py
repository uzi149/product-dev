from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SegmentRead(BaseModel):
    """The storage location is internal; playback goes through /segments/{id}/playback."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    camera_id: UUID
    start_time: datetime
    end_time: datetime
    duration_ms: int
    activity_score: float | None
    status: str
    codec: str | None
    width: int | None
    height: int | None
    created_at: datetime
