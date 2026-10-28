"""Ask the VLM about one window and turn its answer into a validated Observation.

An answer that can't be used gets one more try with a stricter prompt; a second bad answer is
InvalidObservation. VLM errors (unavailable, quota, rejected) pass through to the task, which
decides whether to retry.
"""

import logging
import re
from dataclasses import dataclass

import numpy as np

from services.analysis import prompts
from shared.model_adapters import VisionLanguageModel, VLMReply
from shared.model_adapters.json_output import extract_json
from shared.schemas.observation import InvalidObservation, Observation, read_observation

log = logging.getLogger(__name__)

# Adapted from the reference code's `to_general_video_summary`: small local models describe the
# frames one by one despite the instruction, so bullets and sentences about frames are dropped.
_FRAME_PHRASE = re.compile(
    r"\s*\b(across|in|throughout|over|between)\s+(the\s+|all\s+|different\s+|"
    r"various\s+|these\s+)?(key)?frames\b",
    re.IGNORECASE,
)
_FRAME_WORD = re.compile(r"\b(key)?frames?\b", re.IGNORECASE)
_BULLET = re.compile(r"(?:^|\s)(?:[-•*]|\d+[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Described:
    observation: Observation
    reply: VLMReply  # the answer that was used
    attempts: int  # 1, or 2 after the stricter retry


def clean_summary(text: str) -> str:
    """The summary as plain prose without frame-by-frame content (the original if nothing is
    left)."""
    sentences: list[str] = []
    for part in _BULLET.split(text.strip()):
        sentences.extend(_SENTENCE_END.split(part.strip()))
    kept = []
    for sentence in sentences:
        # "Across the frames, a person walks in." -> "A person walks in."
        sentence = _FRAME_PHRASE.sub("", sentence).strip(" ,;")
        if not sentence or sentence.endswith(":") or _FRAME_WORD.search(sentence):
            continue
        kept.append(sentence[0].upper() + sentence[1:])
    return " ".join(kept).strip() or text.strip()


def _read(reply: VLMReply, window_seconds: float) -> Observation:
    data = extract_json(reply.text, expect=dict)
    if data is None:
        raise InvalidObservation("no JSON object found")
    observation = read_observation(data, window_seconds)
    return observation.model_copy(update={"summary": clean_summary(observation.summary)})


def describe_window(
    vlm: VisionLanguageModel,
    frames: list[np.ndarray],
    camera_name: str,
    frame_seconds: list[float],
    window_seconds: float,
) -> Described:
    prompt = prompts.describe_prompt(camera_name, frame_seconds, window_seconds)
    for attempt in (1, 2):
        reply = vlm.complete(
            frames,
            prompt,
            system=prompts.SYSTEM_PROMPT,
            max_tokens=prompts.MAX_ANSWER_TOKENS,
        )
        try:
            return Described(_read(reply, window_seconds), reply, attempt)
        except InvalidObservation as error:
            if attempt == 2:
                raise InvalidObservation(
                    f"the model did not return a valid observation ({error})"
                ) from error
            log.info("%s gave an unusable answer (%s); asking again", reply.model, error)
            prompt = prompts.retry_prompt(str(error), camera_name, frame_seconds, window_seconds)
    raise AssertionError("unreachable")


def index_text(observation: Observation) -> str:
    """What goes into the text index: the summary plus one plain line of actions and objects."""
    types = {entity.temporary_id: entity.type for entity in observation.entities}

    def name(temporary_id: str | None) -> str:
        if temporary_id is None:
            return ""
        kind = types.get(temporary_id)
        return f"{temporary_id} ({kind})" if kind and kind not in temporary_id else temporary_id

    actions = [
        " ".join(p for p in (name(a.subject), a.type, name(a.object)) if p)
        for a in observation.actions
    ]
    lines = [observation.summary]
    if actions:
        lines.append(f"Actions: {', '.join(actions)}.")
    if observation.visible_objects:
        lines.append(f"Visible: {', '.join(observation.visible_objects)}.")
    return "\n".join(lines)
