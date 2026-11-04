from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UploadRead(BaseModel):
    """An uploaded file and the state of its background ingestion (ADR 0013)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    camera_id: UUID
    filename: str
    start_time: datetime
    duration_ms: int | None
    status: str
    segments_created: int | None
    static_periods_created: int | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class FootageConflict(BaseModel):
    """Footage already on the camera that an upload would overlap (ADR 0037). `filename` is the
    upload it came from; None for live footage or uploads from before step 5.6."""

    model_config = ConfigDict(from_attributes=True)

    start: datetime
    end: datetime
    filename: str | None
