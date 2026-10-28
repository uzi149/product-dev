"""The VLM's observation of one analysis window (scope §8.1, MVP plan §3.3).

`Observation` is the stored shape. `read_observation` turns a model's parsed JSON into one,
strict about meaning and forgiving about form: like the reference code's `_ensure_parsed_fields`,
missing lists get defaults and bad values are replaced and noted instead of failing the window.
Only an answer that isn't an object, or has no summary, is invalid.

Temporary ids (`person_1`) are only valid inside one window; nothing links them across windows
or cameras.
"""

from typing import Any

from pydantic import BaseModel, Field

# Action words the VLM may use (MVP plan §3.3); triggers (Phase 5) match on the same words.
ACTION_VOCABULARY = (
    "enter",
    "leave",
    "walk",
    "run",
    "stand",
    "sit",
    "loiter",
    "place_object",
    "pick_up_object",
    "carry_object",
    "leave_object_behind",
    "vehicle_arrive",
    "vehicle_depart",
    "vehicle_park",
    "interact_with_person",
    "fall",
    "open_door",
    "close_door",
    "ring_doorbell",
)
# Used when the model gives no usable confidence.
DEFAULT_CONFIDENCE = 0.5


class InvalidObservation(Exception):
    """The answer can't be used: not a JSON object, or no summary."""


class Entity(BaseModel):
    temporary_id: str
    type: str


class Action(BaseModel):
    type: str
    subject: str | None = None
    object: str | None = None
    # Seconds into the window.
    start_second: float = Field(ge=0)
    end_second: float = Field(ge=0)


class Observation(BaseModel):
    summary: str = Field(min_length=1)
    entities: list[Entity] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    visible_objects: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    overall_confidence: float = Field(ge=0, le=1)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (_text(v) for v in value) if text]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _action_word(value: Any) -> str:
    return _text(value).lower().replace(" ", "_").replace("-", "_")


def read_observation(data: Any, window_seconds: float) -> Observation:
    """An Observation from the model's parsed JSON, or InvalidObservation."""
    if not isinstance(data, dict):
        raise InvalidObservation("the answer is not a JSON object")
    summary = _text(data.get("summary"))
    if not summary:
        raise InvalidObservation("the summary is missing")
    notes = _texts(data.get("uncertainties"))

    entities = []
    for item in data.get("entities") or []:
        if isinstance(item, dict) and _text(item.get("temporary_id")) and _text(item.get("type")):
            entities.append(
                Entity(temporary_id=_text(item["temporary_id"]), type=_text(item["type"]))
            )
    known = {entity.temporary_id for entity in entities}

    actions = []
    for item in data.get("actions") or []:
        if not isinstance(item, dict):
            continue
        word = _action_word(item.get("type"))
        if word not in ACTION_VOCABULARY:
            notes.append(f"dropped unknown action '{word or item.get('type')}'")
            continue
        start, end = _number(item.get("start_second")), _number(item.get("end_second"))
        start = 0.0 if start is None else min(max(start, 0.0), window_seconds)
        end = window_seconds if end is None else min(max(end, 0.0), window_seconds)
        subject = _text(item.get("subject")) or None
        if subject and subject not in known:
            notes.append(f"action '{word}' names an unknown subject '{subject}'")
        action = Action(
            type=word,
            subject=subject,
            object=_text(item.get("object")) or None,
            start_second=min(start, end),
            end_second=max(start, end),
        )
        if action not in actions:  # models sometimes repeat the same entry many times
            actions.append(action)

    confidence = _number(data.get("overall_confidence"))
    if confidence is None:
        notes.append("the model gave no confidence")
        confidence = DEFAULT_CONFIDENCE
    return Observation(
        summary=summary,
        entities=entities,
        actions=actions,
        visible_objects=_texts(data.get("visible_objects")),
        uncertainties=notes,
        overall_confidence=min(max(confidence, 0.0), 1.0),
    )
