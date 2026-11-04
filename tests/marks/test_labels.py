"""The words grounded for a trigger rule or a question (step 5.7)."""

import pytest

from services.marks.labels import grounding_labels


@pytest.mark.parametrize(
    ("text", "classes", "expected"),
    [
        ("a person leaves a bag", [], ["person", "bag"]),
        ("two people carry boxes across a parking lot", [], ["person", "box"]),
        ("a man and a woman get out of a red car", [], ["person", "car"]),
        ("dogs on the lawn", [], ["dog"]),
        ("someone leaves a suitcase", [], ["person", "suitcase"]),
        # Vague words: the class's default words.
        (
            "someone places an object and leaves it behind",
            ["person", "portable_object"],
            ["person", "bag", "backpack", "suitcase", "box", "package"],
        ),
        ("a vehicle stops at the gate", [], ["vehicle"]),
        ("parking", ["vehicle"], ["car", "truck", "van", "bicycle", "motorcycle"]),
        # A class from the compiled rule that the text doesn't name.
        ("loitering near the door", ["person"], ["person"]),
        # Nothing known: every distinct object.
        ("something unusual happens", [], []),
    ],
)
def test_labels(text, classes, expected):
    assert grounding_labels(text, classes) == expected
