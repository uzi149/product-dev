"""The strict factual prompt for one analysis window (scope §8.1).

Adapted from the reference code's video scene description ("these frames are sampled in order
from one video… describe it as one continuous scene… be factual") and its prompt templates'
"respond with valid JSON only, these fields, an example" style. The answer is the §8.1 JSON
instead of free text.

PROMPT_VERSION is stored with every observation: change it whenever the wording changes, so
observations from different prompts can be told apart.
"""

import json

from shared.schemas.observation import ACTION_VOCABULARY

PROMPT_VERSION = 1
MAX_ANSWER_TOKENS = 800

SYSTEM_PROMPT = (
    "You analyse security camera footage. Report only what is visible. Do not guess "
    "identities, names, age, gender, ethnicity or intentions. If something is unclear or "
    "hidden, say so in `uncertainties` instead of guessing. Answer with valid JSON only."
)

EXAMPLE = {
    "summary": "A person enters, places a dark bag near the wall and leaves.",
    "entities": [
        {"temporary_id": "person_1", "type": "person"},
        {"temporary_id": "object_1", "type": "bag"},
    ],
    "actions": [
        {"type": "enter", "subject": "person_1", "start_second": 1.2, "end_second": 2.8},
        {
            "type": "place_object",
            "subject": "person_1",
            "object": "object_1",
            "start_second": 5.1,
            "end_second": 7.4,
        },
        {"type": "leave", "subject": "person_1", "start_second": 9.0, "end_second": 11.1},
    ],
    "visible_objects": ["person", "bag"],
    "uncertainties": [],
    "overall_confidence": 0.88,
}

_FIELDS = """\
- summary: 1-3 factual sentences about what happens in the clip as a whole.
- entities: every person, vehicle, animal or object that takes part, each with a temporary_id
  that is only valid in this clip ("person_1", "vehicle_1", "object_1") and a short type.
- actions: what the entities do, in order. type must be one of the action words below;
  subject (and object, if any) are temporary_ids; start_second and end_second are seconds
  from the start of the clip. Leave out anything that is not one of the action words.
- visible_objects: short, common, singular nouns for the kinds of things visible.
- uncertainties: what could not be seen clearly; an empty list if nothing.
- overall_confidence: 0 to 1, how sure you are of this description."""


def describe_prompt(camera_name: str, frame_seconds: list[float], window_seconds: float) -> str:
    """The user message that follows the frames. `frame_seconds` are relative to the clip."""
    times = ", ".join(f"frame {n} at {t:.1f} s" for n, t in enumerate(frame_seconds, 1))
    return (
        f"These {len(frame_seconds)} frames are sampled in order from one {window_seconds:.0f} s "
        f'clip of camera "{camera_name}": {times}. Describe the clip as one continuous scene. '
        "Never mention frames or images, and do not describe them one by one.\n\n"
        "Respond with VALID JSON ONLY containing these fields:\n"
        f"{_FIELDS}\n\n"
        f"Action words: {', '.join(ACTION_VOCABULARY)}.\n\n"
        f"Example:\n{json.dumps(EXAMPLE)}\n\n"
        "If nothing happens, give a summary of the scene and empty entities and actions. "
        "Answer with the JSON object only."
    )


def retry_prompt(
    problem: str, camera_name: str, frame_seconds: list[float], window_seconds: float
) -> str:
    """The stricter prompt, used once after an answer that could not be used."""
    return (
        f"Your previous answer could not be used: {problem}. Answer again, following the rules "
        "exactly. Your whole answer must be one JSON object that starts with { and ends with }, "
        "with a non-empty summary, and no other text.\n\n"
        + describe_prompt(camera_name, frame_seconds, window_seconds)
    )
