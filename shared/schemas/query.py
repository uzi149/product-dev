"""Query results: the playback manifest of an evidence result (scope §11, step 4.3).

A result plays its video parts in order. A gap part stands for footage that is not shown, so
separated clips never look continuous. The API (4.4) and the Investigate page (4.5) use these.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class VideoPart(BaseModel):
    """Plays segment `segment_id` from `source_start_ms` to `source_end_ms` (through
    /segments/{segment_id}/playback)."""

    type: Literal["video"] = "video"
    camera_id: UUID
    segment_id: UUID
    source_start_ms: int
    source_end_ms: int
    start_time: datetime
    end_time: datetime


class GapPart(BaseModel):
    """Footage between two parts that is not shown."""

    type: Literal["gap"] = "gap"
    duration_ms: int


Part = Annotated[VideoPart | GapPart, Field(discriminator="type")]


class PlaybackManifest(BaseModel):
    result_id: UUID
    camera_id: UUID
    start_time: datetime
    end_time: datetime
    parts: list[Part]
