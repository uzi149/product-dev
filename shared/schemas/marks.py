"""Evidence marks (step 5.7, ADR 0038): boxes around the people and objects of a clip.

A track is one person or object followed through the clip. Its points are grouped in runs, one
per segment, each point `[ms in the segment, x1, y1, x2, y2]` with the box as 0-1 of the
picture, about 5 per second; the player draws the box between two points by interpolating.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from shared.schemas.entities import EntityClass

# none: never asked for (an Investigate result not opened yet, or marks switched off)
MarksStatusName = Literal["none", "pending", "running", "done", "failed", "skipped"]
PickStatusName = Literal["done", "failed", "none"]

Point = tuple[int, float, float, float, float]


class TrackRun(BaseModel):
    segment_id: UUID
    points: list[Point]


class Track(BaseModel):
    id: int
    label: str  # the model's word: "person", "backpack"
    kind: EntityClass | None  # its class, for the colour
    # The VLM picked it as one the trigger or question is about (only when pick_status is done).
    picked: bool
    runs: list[TrackRun]


class MarksState(BaseModel):
    status: MarksStatusName
    reason: str | None = None
    # New marks are being made (the event was extended); `tracks` are the earlier ones.
    updating: bool = False
    pick_status: PickStatusName | None = None
    tracks: list[Track] = []
