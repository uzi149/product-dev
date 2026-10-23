from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field


class StaticPeriodRead(BaseModel):
    """The reference frame is served by /static-periods/{id}/reference-frame."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    camera_id: UUID
    start_time: datetime
    end_time: datetime
    health_status: str
    # Internal storage location: read from the model, never sent to clients.
    reference_frame_uri: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def has_reference_frame(self) -> bool:
        return self.reference_frame_uri is not None
