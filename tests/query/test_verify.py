"""Verification frames, prompt and answers (step 4.4)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from services.query_engine import verify_prompt
from services.query_engine.evidence import (
    EvidenceResult,
    EvidenceSequence,
    Window,
    build_manifest,
    load_segments,
)
from services.query_engine.verify import (
    InvalidVerdict,
    choose_frames,
    gaps_of,
    parse_verdict,
    spread,
)
from tests.search.conftest import START


@pytest.mark.parametrize(
    ("n", "count", "expected"),
    [(5, 12, [0, 1, 2, 3, 4]), (13, 5, [0, 3, 6, 9, 12]), (10, 1, [5]), (0, 3, [])],
)
def test_frames_are_spread_evenly_first_and_last_included(n, count, expected):
    assert spread(list(range(n)), count) == expected


@pytest.mark.parametrize(
    ("text", "match", "confidence"),
    [
        ('{"match": true, "confidence": 0.9, "reason": "yes"}', True, 0.9),
        ('Sure! ```json\n{"match": false, "confidence": 0.2, "reason": "no"}\n```', False, 0.2),
        ('{"match": "true", "confidence": "0.7", "reason": "ok"}', True, 0.7),
        ('{"match": true, "confidence": 1.4, "reason": "very"}', True, 1.0),  # clipped
    ],
)
def test_answers_are_read(text, match, confidence):
    verdict = parse_verdict(text)
    assert (verdict.match, verdict.confidence) == (match, confidence)


@pytest.mark.parametrize(
    "text",
    [
        "no JSON here",
        '{"match": "maybe", "confidence": 0.5}',
        '{"match": true, "confidence": "high"}',
    ],
)
def test_unusable_answers_are_refused(text):
    with pytest.raises(InvalidVerdict):
        parse_verdict(text)


def test_the_prompt_names_the_question_the_frame_times_and_the_gaps():
    prompt = verify_prompt.verify_prompt(
        "someone leaves a bag",
        [0, 6, 330, 336],
        [(12, 312)],
        [(0, 12, "A person places a bag."), (324, 336, "The person walks away.")],
    )
    assert '"someone leaves a bag"' in prompt
    lines = prompt.splitlines()
    gap = next(i for i, line in enumerate(lines) if "gap: 5:12" in line)
    assert "frame 2: 0:06" in lines[gap - 1] and "frame 3: 5:30" in lines[gap + 1]
    assert "0:00-0:12: A person places a bag." in prompt
    assert '"match"' in prompt and '"confidence"' in prompt


@pytest.fixture
def two_clips(session, cameras, add_window):
    """Front door windows at 0-12 s and 330-342 s (keyframes every 2 s from 1 s), one result."""
    front, _ = cameras
    first = add_window(front, 0, None, [None] * 6)
    second = add_window(front, 330, None, [None] * 6)
    windows = [
        Window(w.id, w.camera_id, w.segment_id, w.start_time, w.end_time, w.vlm_description, 0.5)
        for w in (first, second)
    ]
    result = EvidenceResult(
        front.id,
        [EvidenceSequence(front.id, w.start, w.end, [w]) for w in windows],
    )
    result.manifest = build_manifest(result, load_segments(session, [first.segment_id]))
    return result


def test_frames_come_from_the_best_hits_first_in_time_order(session, two_clips):
    # Both windows score 0.5: the earlier one is taken first, and its 6 keyframes suffice.
    frames = choose_frames(session, two_clips, 4)
    seconds = [f.seconds for f in frames]
    assert seconds == sorted(seconds) and len(frames) == 4
    assert all(s < 12 for s in seconds)
    # Asking for more than one window has brings in the next one, still in time order.
    seconds = [f.seconds for f in choose_frames(session, two_clips, 12)]
    assert len(seconds) == 12 and seconds == sorted(seconds)
    assert seconds[0] == 1 and seconds[-1] == 341


def test_a_short_event_in_a_long_result_gets_its_frames(session, two_clips):
    """The better hit is the later window: its frames are chosen, not ones spread over 342 s."""
    early, late = two_clips.sequences
    late.windows[0] = replace(late.windows[0], score=0.9)
    early.windows[0] = replace(early.windows[0], score=None)  # context only
    seconds = [f.seconds for f in choose_frames(session, two_clips, 6)]
    assert len(seconds) == 6 and all(s > 330 for s in seconds)


def test_gaps_are_measured_from_the_start_of_the_result(two_clips):
    assert gaps_of(two_clips) == [(12.0, 318.0)]
    assert two_clips.end - two_clips.start == timedelta(seconds=342)
    assert two_clips.start == START
