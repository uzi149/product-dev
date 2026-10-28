"""Pulling the JSON out of model answers (adapted from the reference `_try_parse_json`)."""

import pytest

from shared.model_adapters.json_output import extract_json


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"summary": "A person enters."}', {"summary": "A person enters."}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('Here is the observation:\n{"a": 1}\nLet me know if you need more.', {"a": 1}),
        ('[{"a": 1}, {"b": 2}]', [{"a": 1}, {"b": 2}]),
        ('{"text": "braces } and { inside a string"}', {"text": "braces } and { inside a string"}),
        ('{"first": 1} {"second": 2}', {"first": 1}),
        ('{"a": {"b": [1, 2, {"c": null}]}}', {"a": {"b": [1, 2, {"c": None}]}}),
        ("no json at all", None),
        ('{"summary": "cut off in the midd', None),
        ("", None),
    ],
)
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_a_fence_wins_over_json_like_prose_before_it():
    text = 'I looked at [1] frame.\n```json\n{"a": 1}\n```'
    assert extract_json(text) == {"a": 1}


def test_expect_skips_the_other_kind():
    text = 'Frames [1, 2] show it: {"summary": "x"}'
    assert extract_json(text) == [1, 2]
    assert extract_json(text, expect=dict) == {"summary": "x"}
    assert extract_json('{"a": 1}', expect=list) is None
