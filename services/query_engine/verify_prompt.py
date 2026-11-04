"""The prompt that asks the VLM whether a result shows what the operator asked (scope §10).

Change PROMPT_VERSION whenever the wording changes, as with the analysis prompt. The answer is one
JSON object: match, confidence, reason.
"""

PROMPT_VERSION = 1
MAX_ANSWER_TOKENS = 300

SYSTEM_PROMPT = (
    "You check security camera footage against an operator's question. Judge only what is "
    "visible in the frames. Do not guess identities, names, age, gender, ethnicity or "
    "intentions. If the frames don't show enough to tell, say so and give a low confidence. "
    "Answer with valid JSON only."
)

_ANSWER = (
    'Answer with one JSON object only: {"match": true or false, "confidence": a number from 0 '
    'to 1, "reason": "one short sentence about what is visible"}. "match" is true only if the '
    "frames show what the question asks for."
)


def _clock(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def verify_prompt(
    question: str,
    frame_seconds: list[float],
    gaps: list[tuple[float, float]],
    descriptions: list[tuple[float, float, str]],
) -> str:
    """`frame_seconds`: each frame's time from the start of the result. `gaps`: (start, length)
    of footage not shown, in seconds. `descriptions`: (start, end, text) from the analysis."""
    lines = [
        f'Operator\'s question: "{question}"',
        "",
        (
            f"The {len(frame_seconds)} frames are in time order from one camera, at these times from "
            "the start of the clip:"
        ),
    ]
    gap_iter = iter(sorted(gaps))
    gap = next(gap_iter, None)
    for n, seconds in enumerate(frame_seconds, 1):
        while gap is not None and gap[0] <= seconds:
            lines.append(f"  (gap: {_clock(gap[1])} of footage not shown here)")
            gap = next(gap_iter, None)
        lines.append(f"  frame {n}: {_clock(seconds)}")
    if descriptions:
        lines += [
            "",
            "Earlier automatic descriptions of this footage (may be incomplete or wrong):",
        ]
        lines += [f"  {_clock(a)}-{_clock(b)}: {text}" for a, b, text in descriptions if text]
    lines += ["", _ANSWER]
    return "\n".join(lines)


def retry_prompt(problem: str, first_prompt: str) -> str:
    return (
        f"{first_prompt}\n\nYour previous answer could not be used ({problem}). Reply with exactly "
        "one JSON object with the keys match (true or false), confidence (0 to 1) and reason "
        "(a short sentence), and nothing else."
    )
