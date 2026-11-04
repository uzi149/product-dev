"""Custom triggers (scope §12, step 5.1): the API's request and response models and the
compiled rule (ADR 0032).

The compiled rule is what live evaluation (5.2) and testing (5.4) check: `structured` rules need
their actions in order within the gap, as `required_action_sequence` or any of the
`alternative_action_sequences` (other ways the analysis may describe the same event, e.g.
`leave_object_behind` for "places an object and leaves"); `semantic` rules (no vocabulary action)
are matched by similarity to `search_text`.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from shared.schemas.entities import EntityClass
from shared.schemas.observation import ACTION_VOCABULARY
from shared.schemas.query import PlaybackManifest

MIN_GAP_SECONDS, MAX_GAP_SECONDS, DEFAULT_GAP_SECONDS = 5, 600, 30


class CompiledRule(BaseModel):
    mode: Literal["structured", "semantic"]
    required_entities: list[EntityClass] = Field(default_factory=list)
    required_action_sequence: list[str] = Field(default_factory=list)
    alternative_action_sequences: list[list[str]] = Field(default_factory=list)
    maximum_sequence_gap_seconds: int = Field(
        default=DEFAULT_GAP_SECONDS, ge=MIN_GAP_SECONDS, le=MAX_GAP_SECONDS
    )
    requires_vlm_verification: bool = True
    # True when the operator switched verification in the builder: a new compile keeps it.
    verification_set_by_operator: bool = False
    search_text: str = Field(min_length=1)
    # The rule text this was compiled from, so a compile of an older wording is never stored.
    source_rule: str
    prompt_version: int
    model: str | None = None

    @field_validator("required_action_sequence")
    @classmethod
    def _known_actions(cls, value: list[str]) -> list[str]:
        unknown = [a for a in value if a not in ACTION_VOCABULARY]
        if unknown:
            raise ValueError(f"not in the action vocabulary: {', '.join(unknown)}")
        return value

    @field_validator("alternative_action_sequences")
    @classmethod
    def _known_alternatives(cls, value: list[list[str]]) -> list[list[str]]:
        for sequence in value:
            cls._known_actions(sequence)
        return value

    @property
    def action_sequences(self) -> list[list[str]]:
        """Every sequence that satisfies the rule: the required one first."""
        return [s for s in [self.required_action_sequence, *self.alternative_action_sequences] if s]


class _TriggerSettings(BaseModel):
    minimum_confidence: float = Field(default=0.8, ge=0, le=1)
    cooldown_seconds: int = Field(default=120, ge=0, le=3600)
    pre_roll_seconds: int = Field(default=10, ge=0, le=60)
    post_roll_seconds: int = Field(default=20, ge=0, le=120)


class TriggerCreate(_TriggerSettings):
    """A new trigger starts disabled; its rule is compiled in the background (ADR 0032)."""

    name: str = Field(min_length=1, max_length=200)
    natural_language_rule: str = Field(min_length=1, max_length=1000)
    camera_ids: list[UUID] = Field(min_length=1)

    @field_validator("name", "natural_language_rule")
    @classmethod
    def _stripped(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class TriggerUpdate(BaseModel):
    """Only the fields sent change. A changed rule is compiled again and the trigger disabled
    until then; `requires_vlm_verification` overrides the compiler's choice."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    natural_language_rule: str | None = Field(default=None, min_length=1, max_length=1000)
    camera_ids: list[UUID] | None = Field(default=None, min_length=1)
    minimum_confidence: float | None = Field(default=None, ge=0, le=1)
    cooldown_seconds: int | None = Field(default=None, ge=0, le=3600)
    pre_roll_seconds: int | None = Field(default=None, ge=0, le=60)
    post_roll_seconds: int | None = Field(default=None, ge=0, le=120)
    requires_vlm_verification: bool | None = None

    @field_validator("name", "natural_language_rule")
    @classmethod
    def _stripped(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class TriggerCamera(BaseModel):
    id: UUID
    display_name: str


class EventCounts(BaseModel):
    new: int = 0
    total: int = 0


class TriggerRead(_TriggerSettings):
    id: UUID
    name: str
    natural_language_rule: str
    cameras: list[TriggerCamera]
    enabled: bool
    compile_status: Literal["pending", "done", "failed"]
    compile_error: str | None
    compiled: CompiledRule | None
    events: EventCounts
    created_at: datetime
    updated_at: datetime


class TriggerTestCreate(BaseModel):
    """Scope §12.4: a selected test video (an upload, ADR 0035), the previous hour, the previous
    24 hours or a custom range."""

    source: Literal["video", "last_hour", "last_24h", "range"]
    upload_id: UUID | None = None
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None

    @model_validator(mode="after")
    def _complete(self) -> "TriggerTestCreate":
        if self.source == "video" and self.upload_id is None:
            raise ValueError("choose the uploaded video to test on")
        if self.source == "range":
            if self.start is None or self.end is None:
                raise ValueError("a custom range needs a start and an end")
            if self.end <= self.start:
                raise ValueError("the end must be after the start")
        return self


class TestAction(BaseModel):
    type: str
    start: datetime
    end: datetime


class TestVerification(BaseModel):
    # verified, rejected, not_required, unverifiable, not_verified_local, not_checked
    status: str
    confidence: float | None = None
    reason: str | None = None


class TestMatch(BaseModel):
    """A would-be event: what the trigger would have reported, and the verdict."""

    __test__ = False

    camera_id: UUID
    camera_name: str
    start_time: datetime
    end_time: datetime
    verdict: Literal["would_report", "rejected", "below_confidence"]
    confidence: float
    verification: TestVerification
    mode: str
    score: float | None
    actions: list[TestAction]
    summary: str | None
    keyframe_id: UUID | None
    manifest: PlaybackManifest


class TestCounts(BaseModel):
    __test__ = False

    would_report: int = 0
    rejected: int = 0
    below_confidence: int = 0


class TriggerTestRead(BaseModel):
    __test__ = False

    id: UUID
    trigger_id: UUID
    source: Literal["video", "last_hour", "last_24h", "range"]
    upload_id: UUID | None
    upload_filename: str | None
    camera_ids: list[UUID]
    start: datetime
    end: datetime
    status: Literal["pending", "running", "done", "failed"]
    stage: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    # The rule and settings the test used (the trigger may have changed since).
    rule: CompiledRule
    minimum_confidence: float
    cooldown_seconds: int
    candidates: int | None = None
    footage_seconds: float | None = None
    counts: TestCounts = Field(default_factory=TestCounts)
    matches: list[TestMatch] = Field(default_factory=list)
