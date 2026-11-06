"""Grounding answers read into 0-1 boxes, and picking tracks (step 5.7)."""

import json

import numpy as np
import pytest

from shared.model_adapters.grounding import (
    Detection,
    bbox_space,
    candidate_image,
    crop,
    drop_repetition_runs,
    ground,
    grounding_prompt,
    parse_boxes,
    parse_pick,
    pick_prompt,
    pick_tracks,
    pick_views,
)
from shared.model_adapters.vlm import VLMUnavailable
from tests.fakes import FakeVLM


def boxes(*items) -> str:
    return json.dumps([{"bbox_2d": list(b), "label": label} for b, label in items])


@pytest.mark.parametrize(
    ("model", "setting", "expected"),
    [
        ("qwen/qwen3-vl-30b-a3b-instruct", "auto", "norm1000"),
        ("Qwen/Qwen2.5-VL-3B-Instruct", "auto", "pixel"),
        ("qwen/qwen-2.5-vl-72b-instruct", "auto", "pixel"),
        ("Qwen/Qwen2.5-VL-3B-Instruct", "norm1000", "norm1000"),
        ("qwen/qwen3-vl-30b-a3b-instruct", "pixel", "pixel"),
    ],
)
def test_the_coordinate_space_follows_the_model(model, setting, expected):
    assert bbox_space(model, setting) == expected


def test_pixel_and_grid_answers_give_the_same_box():
    pixel = parse_boxes(boxes(([100, 50, 300, 250], "person")), 1000, 500, "pixel")
    grid = parse_boxes(boxes(([100, 100, 300, 500], "person")), 1000, 500, "norm1000")
    assert pixel == grid == [Detection("person", (0.1, 0.1, 0.3, 0.5))]


def test_the_json_is_found_in_prose_and_fences():
    text = "Here you go:\n```json\n" + boxes(([0, 0, 500, 500], "Bag")) + "\n```\nDone."
    assert parse_boxes(text, 0, 0, "norm1000") == [Detection("bag", (0.0, 0.0, 0.5, 0.5))]


def test_a_cut_off_answer_keeps_its_complete_boxes():
    text = '[{"bbox_2d": [0, 0, 100, 200], "label": "person"}, {"bbox_2d": [500, 500, 6'
    found = parse_boxes(text, 0, 0, "norm1000")
    assert found == [Detection("person", (0.0, 0.0, 0.1, 0.2))]


def test_boxes_are_clipped_and_tiny_or_scene_boxes_dropped():
    found = parse_boxes(
        boxes(
            ([-50, 900, 400, 1200], "person"),
            ([10, 10, 11, 11], "person"),  # 1/1000 of the picture: noise
            ([0, 0, 1000, 300], "wall"),
        ),
        0,
        0,
        "norm1000",
    )
    assert found == [Detection("person", (0.0, 0.9, 0.4, 1.0))]


def test_a_scene_part_that_was_asked_for_is_kept():
    found = parse_boxes(boxes(([0, 0, 500, 300], "window")), 0, 0, "norm1000", ["window"])
    assert [d.label for d in found] == ["window"]


def test_duplicates_and_group_boxes_are_dropped():
    found = parse_boxes(
        boxes(
            ([100, 100, 200, 400], "person"),
            ([105, 100, 205, 400], "person"),  # the same person twice
            ([400, 100, 500, 400], "person"),
            ([90, 90, 520, 420], "person"),  # "the group": encloses both
            ([110, 110, 210, 410], "bag"),  # another label on the same pixels: stricter
        ),
        0,
        0,
        "norm1000",
    )
    assert [(d.label, d.box[0]) for d in found] == [("person", 0.1), ("person", 0.4)]


def test_a_repetition_loop_is_dropped():
    """A degenerating model tiles a row with same-sized boxes."""
    row = [((0.1 * k, 0.5, 0.1 * k + 0.1, 0.6), "chair") for k in range(6)]
    kept = drop_repetition_runs([((0.2, 0.1, 0.3, 0.3), "person"), *row], 5)
    assert kept == [((0.2, 0.1, 0.3, 0.3), "person")]


def test_the_prompt_names_the_labels_or_asks_for_everything():
    assert "person, bag" in grounding_prompt(["person", "bag"])
    assert "bbox_2d" in grounding_prompt(["person"])
    assert "every distinct object" in grounding_prompt([])


def test_ground_sends_the_grounding_size_and_reads_the_answer():
    vlm = FakeVLM([boxes(([100, 50, 300, 250], "person"))], model="Qwen/Qwen2.5-VL-3B-Instruct")
    frame = np.zeros((1080, 1920, 3), np.uint8)
    found = ground(vlm, frame, ["person"], image_max_side=1000)
    # Sent at 1000 x 562: a Qwen2.5 answer is in those pixels.
    assert found[0].box == pytest.approx((0.1, 50 / 562, 0.3, 250 / 562))
    assert vlm.calls[0]["image_max_side"] == 1000


def test_pixel_boxes_follow_the_size_the_local_model_saw():
    # The local processor scaled the 1000 px picture down to 644 x 364: its pixel answer is in
    # those pixels, not the ones sent (step 5.8).
    vlm = FakeVLM(
        [boxes(([64.4, 36.4, 322, 182], "person"))],
        model="Qwen/Qwen2.5-VL-3B-Instruct",
        image_size=(644, 364),
    )
    found = ground(vlm, np.zeros((1080, 1920, 3), np.uint8), ["person"], image_max_side=1000)
    assert found[0].box == pytest.approx((0.1, 0.1, 0.5, 0.5))


@pytest.mark.parametrize(
    ("text", "expected"),
    [("[2]", [2]), ("Numbers: [1, 3]", [1, 3]), ("[]", []), ("[3, 3, 9]", [3]), ("none", None)],
)
def test_pick_answers(text, expected):
    assert parse_pick(text, 3) == expected


def test_the_pick_prompt_quotes_the_question():
    prompt = pick_prompt("a person leaves a bag", 3, multi_view=True)
    assert '"a person leaves a bag"' in prompt and "1 to 3" in prompt and "side by side" in prompt


def test_views_are_the_largest_sightings_far_apart():
    small, big = (0.0, 0.0, 0.1, 0.1), (0.0, 0.0, 0.3, 0.3)
    sightings = [(0, big), (1, big), (2, small), (9, big)]
    assert pick_views(sightings, 2) == [(0, big), (9, big)]


def test_crops_and_candidate_sheets():
    frame = np.full((200, 400, 3), 100, np.uint8)
    view = crop(frame, (0.25, 0.25, 0.5, 0.75))
    assert view is not None and view.shape[2] == 3
    assert crop(frame, (0.5, 0.5, 0.5, 0.5)) is None
    sheet = candidate_image([view, view], 2)
    assert sheet.shape[1] > 2 * view.shape[1]
    assert tuple(sheet[2, 2]) == (0, 255, 255)  # the number's yellow corner


def test_pick_tracks_maps_the_numbers_to_track_ids():
    frame = np.full((100, 100, 3), 80, np.uint8)
    candidates = {7: [(0, (0.1, 0.1, 0.4, 0.9))], 9: [(0, (0.5, 0.5, 0.8, 0.9))]}
    vlm = FakeVLM(["[2]"])
    assert pick_tracks(vlm, "a bag is left", candidates, lambda _: frame) == {9}
    assert vlm.calls[0]["frames"] == 2


def test_an_unusable_pick_gives_none():
    frame = np.full((100, 100, 3), 80, np.uint8)
    candidates = {1: [(0, (0.1, 0.1, 0.4, 0.9))], 2: [(0, (0.5, 0.5, 0.8, 0.9))]}
    assert pick_tracks(FakeVLM(["I can't tell"]), "q", candidates, lambda _: frame) is None
    assert pick_tracks(FakeVLM([VLMUnavailable("down")]), "q", candidates, lambda _: frame) is None
