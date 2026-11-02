"""The prompt that turns a trigger's rule into structured criteria (scope §12.2, ADR 0032).

Text only, no frames. Adapted from the reference code's prompt templates' "convert the natural
language query into structured parameters, respond with VALID JSON ONLY containing these fields"
style, with our action vocabulary (the words the analysis writes) and entity classes.

Change PROMPT_VERSION whenever the wording changes; it is stored with every compiled rule.
"""

import json

from shared.schemas.entities import ENTITY_CLASSES
from shared.schemas.observation import ACTION_VOCABULARY
from shared.schemas.trigger import DEFAULT_GAP_SECONDS, MAX_GAP_SECONDS, MIN_GAP_SECONDS

PROMPT_VERSION = 1
MAX_ANSWER_TOKENS = 400

SYSTEM_PROMPT = (
    "You turn an operator's rule for security cameras into structured criteria. Use only the "
    "action words and entity classes you are given. Do not add conditions the rule does not "
    "state. Answer with valid JSON only."
)

EXAMPLES = [
    (
        "Report when someone places an object and leaves it behind",
        {
            "required_entities": ["person", "portable_object"],
            "required_action_sequence": ["place_object", "leave"],
            "maximum_sequence_gap_seconds": 30,
            "requires_vlm_verification": True,
            "search_text": "someone places an object and leaves it behind",
        },
    ),
    (
        "Alert me if a crowd gathers near the entrance",
        {
            "required_entities": ["person"],
            "required_action_sequence": [],
            "maximum_sequence_gap_seconds": 30,
            "requires_vlm_verification": True,
            "search_text": "a crowd gathers near the entrance",
        },
    ),
]

_FIELDS = f"""\
- required_entities: the entity classes that must be visible, from: {", ".join(ENTITY_CLASSES)}.
  portable_object means bags, boxes, packages and other things a person can carry.
- required_action_sequence: the action words the rule needs, in the order they must happen,
  only from the action words below. Use an empty list when the rule describes a state or a
  scene rather than actions the words cover.
- maximum_sequence_gap_seconds: the most seconds allowed between one action and the next
  ({MIN_GAP_SECONDS} to {MAX_GAP_SECONDS}); use the rule's own time if it gives one ("within 2
  minutes" is 120), otherwise {DEFAULT_GAP_SECONDS}.
- requires_vlm_verification: true when a mistake would be easy to make from a text description
  alone (objects, sequences, anything subtle); false only for simple, obvious events.
- search_text: the rule as a short description of what should be seen, without words like
  "report when", "alert me if" or "notify", and without its time limit ("within 2 minutes")."""


def compile_prompt(rule: str) -> str:
    examples = "\n".join(
        f'Rule: "{text}"\nAnswer: {json.dumps(answer)}' for text, answer in EXAMPLES
    )
    return (
        "Convert this rule for security cameras into structured criteria.\n\n"
        f'Rule: "{rule}"\n\n'
        "Respond with VALID JSON ONLY containing these fields:\n"
        f"{_FIELDS}\n\n"
        f"Action words: {', '.join(ACTION_VOCABULARY)}.\n\n"
        f"Examples:\n{examples}\n\n"
        "Answer with the JSON object only."
    )


def retry_prompt(problem: str, rule: str) -> str:
    """The stricter prompt, used once after an answer that could not be used."""
    return (
        f"Your previous answer could not be used: {problem}. Answer again, following the rules "
        "exactly. Your whole answer must be one JSON object that starts with { and ends with }, "
        "and no other text.\n\n" + compile_prompt(rule)
    )
