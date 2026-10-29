from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict


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
    # Failed, or stuck in `processing` (step 3.5): the operator can analyse it again.
    retryable: bool = False


class ReanalyseRequest(BaseModel):
    """Every failed segment of the camera that overlaps [start, end]."""

    camera_id: UUID
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None


class ReanalyseResult(BaseModel):
    queued: int


class StatusCounts(BaseModel):
    """Segments per analysis state; `processing` leaves out the stuck ones."""

    pending: int = 0
    processing: int = 0
    stuck: int = 0
    done: int = 0
    failed: int = 0
