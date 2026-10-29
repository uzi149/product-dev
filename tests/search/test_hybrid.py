"""The hybrid score of step 4.2: calibration, weights, and candidates from three sources."""

from datetime import timedelta

import pytest

from services.query_engine import hybrid
from services.query_engine.retrieval import _filters, search
from shared.config import Settings
from shared.database.models import Event, EventEvidence, Trigger
from tests.search.conftest import IMAGE, OPEN, Q_PICTURE, Q_TEXT, START, TEXT, near

SETTINGS = Settings(_env_file=None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 0.0), (0.3, 0.0), (0.45, 0.0), (0.6, 0.5), (0.75, 1.0), (0.9, 1.0)],
)
def test_calibration_is_linear_and_clipped(value, expected):
    assert hybrid.calibrate(value, 0.45, 0.75) == pytest.approx(expected)


def test_weights_cover_only_the_signals_that_apply():
    all_on = hybrid.weights(SETTINGS, actions=True, events=True, keywords=True)
    assert sum(all_on.values()) == pytest.approx(1.0)
    assert all_on["text"] == pytest.approx(0.45 / 1.10)

    plain = hybrid.weights(SETTINGS, actions=False, events=False, keywords=True)
    assert plain["action"] == plain["event"] == 0.0
    assert sum(plain.values()) == pytest.approx(1.0)
    assert plain["text"] == pytest.approx(0.45 / 0.85)

    no_words = hybrid.weights(SETTINGS, actions=False, events=False, keywords=False)
    assert no_words["keyword"] == 0.0 and no_words["text"] == pytest.approx(0.45 / 0.70)

    zero = Settings(
        _env_file=None,
        query_weight_text=0,
        query_weight_visual=0,
        query_weight_keyword=0,
        query_weight_action=0,
        query_weight_event=0,
    )
    assert set(hybrid.weights(zero, actions=True, events=True, keywords=True).values()) == {0.0}


def test_settings_refuse_an_upside_down_calibration():
    with pytest.raises(ValueError, match="query_text_low"):
        Settings(_env_file=None, query_text_low=0.8, query_text_high=0.5)


def run(session, query, **kw):
    kw.setdefault("settings", OPEN)
    return search(session, query, text_embedder=TEXT, image_embedder=IMAGE, **kw)


def test_a_window_found_only_by_its_words_is_a_candidate_with_every_signal(
    session, cameras, add_window
):
    front, _ = cameras
    # Fill both vector lists with other windows, so only full-text search can find this one.
    for n in range(5):
        add_window(front, 100 + 9 * n, near(Q_TEXT, 0.9, f"t{n}"), [near(Q_PICTURE, 0.9, f"p{n}")])
    hidden = add_window(
        front,
        0,
        near(Q_TEXT, 0.1, "h"),
        [near(Q_PICTURE, 0.1, "hp")],
        description="A courier rings the doorbell and waits.",
    )
    hits = run(session, "doorbell courier", candidates=5, limit=10)
    hit = next(h for h in hits if h.window_id == hidden.id)
    assert hit.text_rank is None and hit.visual_rank is None  # not in either vector list
    assert hit.keyword_score > 0 and hit.signals["keyword"] > 0
    assert hit.text_score is not None and hit.visual_score is not None and hit.keyframe_id


def test_keyword_candidates_keep_the_filters(session, cameras, add_window):
    front, yard = cameras
    add_window(yard, 0, near(Q_TEXT, 0.1, "y"), description="A courier rings the doorbell.")
    mine = add_window(front, 0, near(Q_TEXT, 0.1, "f"), description="A courier rings the doorbell.")
    conditions = _filters([front.id], None, None)
    found = hybrid.keyword_candidates(session, "courier", conditions, 20)
    assert [w for w, _ in found] == [mine.id]
    assert hybrid.keyword_candidates(session, "the a of", conditions, 20) == []  # stop words


def test_the_action_signal_counts_the_querys_actions(session, cameras, add_window):
    front, _ = cameras
    vector = near(Q_TEXT, 0.6, "same")
    walking = add_window(front, 0, vector, actions=("walk",))
    ringing = add_window(front, 9, vector, actions=("walk", "ring_doorbell"))
    hits = run(session, "someone rings the doorbell")
    by_id = {h.window_id: h for h in hits}
    assert by_id[ringing.id].action_score == 1.0 and by_id[walking.id].action_score == 0.0
    assert hits[0].window_id == ringing.id


def test_a_window_covered_by_an_event_gets_the_event_signal(session, cameras, add_window):
    front, _ = cameras
    vector = near(Q_TEXT, 0.6, "same")
    plain = add_window(front, 0, vector)
    flagged = add_window(front, 30, vector)
    trigger = Trigger(name="bell", natural_language_rule="someone rings the bell")
    session.add(trigger)
    session.flush()
    event = Event(
        trigger_id=trigger.id,
        camera_id=front.id,
        start_time=START + timedelta(seconds=30),
        end_time=START + timedelta(seconds=40),
        confidence=0.9,
    )
    session.add(event)
    session.flush()
    session.add(
        EventEvidence(
            event_id=event.id,
            segment_id=flagged.segment_id,
            start_offset_ms=31_000,
            end_offset_ms=38_000,
        )
    )
    session.commit()
    hits = {h.window_id: h for h in run(session, "something")}
    assert hits[flagged.id].event_score == pytest.approx(0.9)
    assert hits[plain.id].event_score == 0.0
    assert hits[flagged.id].fused_score > hits[plain.id].fused_score


def test_weak_matches_are_dropped_by_the_minimum_score(session, cameras, add_window):
    front, _ = cameras
    strong = add_window(front, 0, near(Q_TEXT, 0.95, "s"), [near(Q_PICTURE, 0.95, "sp")])
    add_window(front, 9, near(Q_TEXT, 0.05, "w"))
    strict = OPEN.model_copy(update={"query_min_score": 0.6})
    assert [h.window_id for h in run(session, "q", settings=strict)] == [strong.id]


def test_the_real_calibration_finds_nothing_for_unrelated_vectors(session, cameras, add_window):
    front, _ = cameras
    add_window(front, 0, near(Q_TEXT, 0.05, "w"), [near(Q_PICTURE, 0.05, "wp")])
    assert run(session, "a giraffe eating ice cream", settings=SETTINGS) == []


def test_rrf_is_still_available(session, cameras, add_window):
    front, _ = cameras
    best = add_window(front, 0, near(Q_TEXT, 0.9, "b"))
    add_window(front, 9, near(Q_TEXT, 0.3, "o"))
    hits = run(session, "q", ranking="rrf")
    assert hits[0].window_id == best.id and hits[0].signals == {}
    with pytest.raises(ValueError, match="unknown ranking"):
        run(session, "q", ranking="magic")
