"""Queries (step 4.4) and their results' playback manifests (scope §11, step 4.3).

A result plays its video parts in order. A gap part stands for footage that is not shown, so
separated clips never look continuous. The API (4.4) and the Investigate page (4.5) use these.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, field_validator


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


class QueryCreate(BaseModel):
    """A plain-English query. Chosen cameras and times win over the ones in the text (4.1);
    `parse_cameras` / `parse_times` off keep those words as search text (a removed chip)."""

    query_text: str = Field(min_length=1, max_length=500)
    camera_ids: list[UUID] | None = None
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    timezone: str = "UTC"  # the browser's IANA time zone, for "yesterday" and the like
    parse_cameras: bool = True
    parse_times: bool = True

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown time zone: {value}") from error
        return value


class UnderstoodItem(BaseModel):
    kind: Literal["camera", "time"]
    label: str
    camera_id: UUID | None = None


class QueryCounts(BaseModel):
    candidates: int = 0  # merged results found
    to_verify: int = 0  # how many the VLM checks
    verified: int = 0
    rejected: int = 0
    unverifiable: int = 0
    possible: int = 0  # not (or not yet) checked


class QueryState(BaseModel):
    """A query's progress. stage: searching, candidates, verifying, done; verification: "on" or
    "skipped" (local VLM, ADR 0029)."""

    id: UUID
    query_text: str
    search_text: str | None
    understood: list[UnderstoodItem]
    camera_ids: list[UUID] | None
    start: datetime | None
    end: datetime | None
    status: Literal["pending", "running", "done", "failed"]
    stage: str | None
    verification: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    counts: QueryCounts
    # The request as sent, for Run again and removing a chip (4.6); rebuilt from the question
    # and time zone for queries stored before it was kept.
    request: QueryCreate


class QueryList(BaseModel):
    """Query history (step 4.6), newest first."""

    items: list[QueryState]
    total: int


class EvidenceItem(BaseModel):
    """One merged result, best first."""

    id: UUID
    rank: int
    camera_id: UUID
    camera_name: str
    start_time: datetime
    end_time: datetime
    score: float
    status: Literal["possible", "verified", "rejected", "unverifiable"]
    verification_confidence: float | None
    verification_reason: str | None
    summary: str | None
    keyframe_id: UUID | None
    manifest: PlaybackManifest
