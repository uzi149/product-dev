"""Events (scope §13, step 5.3): what the Events page lists, opens, acknowledges and exports.

An event's evidence plays like a query result (scope §11): video parts in order, with explicit
gap parts for footage that isn't shown.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from shared.schemas.query import PlaybackManifest

EventStatusName = Literal["new", "confirmed", "dismissed"]
ExportStatusName = Literal["none", "running", "done", "failed"]


class EventRead(BaseModel):
    """One alerted event (held events wait for their post-roll and aren't shown, ADR 0033)."""

    id: UUID
    trigger_id: UUID
    trigger_name: str
    camera_id: UUID
    camera_name: str
    start_time: datetime
    end_time: datetime
    confidence: float | None
    summary: str | None
    status: EventStatusName
    # verified, not_required, unverifiable (couldn't be verified), not_verified_local
    verification: str | None
    verification_reason: str | None
    keyframe_id: UUID | None  # a picture of the matched moment, for the list
    alerted_at: datetime
    acknowledged_at: datetime | None
    note: str | None
    export_status: ExportStatusName


class EventDetail(EventRead):
    mode: str | None  # structured or semantic
    delay_seconds: float | None  # alert time - the last matched action; live cameras only
    details: dict[str, Any]  # the matches: windows, actions, verification
    manifest: PlaybackManifest


class EventList(BaseModel):
    items: list[EventRead]
    total: int


class EventCounts(BaseModel):
    new: int = 0
    confirmed: int = 0
    dismissed: int = 0
    total: int = 0


class Acknowledge(BaseModel):
    """Confirm or dismiss an event, or put it back to `new`; the note is optional."""

    status: EventStatusName
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note")
    @classmethod
    def _stripped(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ExportState(BaseModel):
    status: ExportStatusName
    error: str | None = None
    exported_at: datetime | None = None
    # When done: a time-limited link that downloads the MP4 under `file_name`.
    url: str | None = None
    file_name: str | None = None
