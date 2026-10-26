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
