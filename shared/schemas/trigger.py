"""Custom triggers (scope §12, step 5.1): the API's request and response models and the
compiled rule (ADR 0032).

The compiled rule is what live evaluation (5.2) and testing (5.4) check: `structured` rules need
their actions in order within the gap; `semantic` rules (no vocabulary action) are matched by
similarity to `search_text`.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from shared.schemas.entities import EntityClass
from shared.schemas.observation import ACTION_VOCABULARY

MIN_GAP_SECONDS, MAX_GAP_SECONDS, DEFAULT_GAP_SECONDS = 5, 600, 30


class CompiledRule(BaseModel):
    mode: Literal["structured", "semantic"]
    required_entities: list[EntityClass] = Field(default_factory=list)
    required_action_sequence: list[str] = Field(default_factory=list)
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
