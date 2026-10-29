"""The strict factual prompt (scope §8.1)."""

from services.analysis import prompts
from shared.schemas.observation import ACTION_VOCABULARY

TIMES = [1.0, 3.0, 5.0, 7.0, 9.0, 11.0]


def test_the_prompt_has_the_camera_the_times_the_vocabulary_and_the_schema():
    text = prompts.describe_prompt("Front door", TIMES, 12.0)
    assert '"Front door"' in text and "12 s clip" in text
    for n, t in enumerate(TIMES, 1):
        assert f"frame {n} at {t:.1f} s" in text
    for word in ACTION_VOCABULARY:
        assert word in text
    for field in (
        "summary",
        "entities",
        "actions",
        "visible_objects",
        "uncertainties",
        "overall_confidence",
    ):
        assert field in text
    assert "VALID JSON ONLY" in text and "person_1" in text


def test_the_system_prompt_forbids_guessing_identities():
    for word in ("only what is visible", "identities", "intentions", "uncertainties"):
        assert word in prompts.SYSTEM_PROMPT


def test_the_retry_prompt_names_the_problem_and_repeats_the_rules():
    text = prompts.retry_prompt("the summary is missing", "Front door", TIMES, 12.0)
    assert text.startswith("Your previous answer could not be used: the summary is missing.")
    assert prompts.describe_prompt("Front door", TIMES, 12.0) in text


def test_the_prompt_has_a_version():
    assert isinstance(prompts.PROMPT_VERSION, int) and prompts.PROMPT_VERSION >= 1


def test_entities_are_limited_to_what_takes_part():
    text = " ".join(prompts.describe_prompt("Car park", TIMES, 12.0).split())
    assert "at most 10" in text and "Do not list parked cars" in text
