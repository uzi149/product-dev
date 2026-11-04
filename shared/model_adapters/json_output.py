"""Pull the JSON out of a model answer.

Adapted from the reference repository's `_try_parse_json`, which only finds arrays: models often
wrap the JSON in a code fence or add a sentence before or after it. This finds the first complete
JSON object or array and ignores the rest.
"""

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_DECODER = json.JSONDecoder()


def extract_json(text: str, expect: type[dict] | type[list] | None = None) -> Any | None:
    """The first JSON object or array in `text`, or None when there is none.

    With `expect=dict` (or `list`), values of the other kind are skipped, so a stray "[1]" in
    the prose doesn't hide the object that follows it.
    """
    openers = {dict: "{", list: "["}.get(expect, "{[")
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for candidate in candidates:
        for start, char in enumerate(candidate):
            if char not in openers:
                continue
            try:
                value, _ = _DECODER.raw_decode(candidate, start)
            except json.JSONDecodeError:
                continue
            return value
    return None


_CLOSERS = {"{": "}", "[": "]"}
# How many cut points to try, from the end, before giving up.
_REPAIR_TRIES = 50


def close_truncated_json(text: str) -> Any | None:
    """A JSON object or array cut off mid-way (an answer that hit the token limit), closed after
    its last complete entry; None when nothing usable is left.

    `{"summary": "…", "actions": [{…}, {"type": "wa` becomes `{"summary": "…", "actions": [{…}]}`:
    it is cut at the last comma outside a string (everything before it is complete) and the open
    brackets are closed.
    """
    text = text.lstrip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
    start = next((i for i, c in enumerate(text) if c in _CLOSERS), None)
    if start is None:
        return None
    stack: list[str] = []
    cuts: list[tuple[int, tuple[str, ...]]] = []
    in_string = escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in _CLOSERS:
            stack.append(_CLOSERS[char])
        elif char in "}]":
            if not stack:
                break
            stack.pop()
            if not stack:
                return None  # complete after all: extract_json's job
        elif char == ",":
            cuts.append((i, tuple(stack)))
    for cut, open_brackets in reversed(cuts[-_REPAIR_TRIES:]):
        try:
            return json.loads(text[start:cut] + "".join(reversed(open_brackets)))
        except json.JSONDecodeError:
            continue
    return None
