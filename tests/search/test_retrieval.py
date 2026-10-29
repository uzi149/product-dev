"""Filtered search over both indexes (scratch database, hand-made vectors)."""

from datetime import timedelta

import pytest
from sqlalchemy import text

from services.query_engine.retrieval import search
from shared.database.index_settings import IndexSettingsMismatch, ensure_index_settings
from shared.database.models import EMBEDDING_DIM, Keyframe
from tests.search.conftest import IMAGE, OPEN, Q_PICTURE, Q_TEXT, QUERY, START, TEXT, near


def run(session, **filters):
    filters.setdefault("settings", OPEN)
    return search(session, QUERY, text_embedder=TEXT, image_embedder=IMAGE, **filters)


def test_the_nearest_description_comes_first(session, cameras, add_window):
    front, _ = cameras
    far = add_window(front, 0, near(Q_TEXT, 0.2, "a"))
    best = add_window(front, 9, near(Q_TEXT, 0.9, "b"))
    middle = add_window(front, 18, near(Q_TEXT, 0.5, "c"))
    hits = run(session)
    assert [h.window_id for h in hits] == [best.id, middle.id, far.id]
    assert hits[0].text_rank == 1 and hits[0].text_score > hits[1].text_score
    assert hits[0].camera_name == "Front door" and hits[0].description == "something happens"
    assert (hits[0].segment_start_seconds, hits[0].segment_end_seconds) == (9, 21)
    assert hits[0].actions == [{"type": "walk", "subject": "person_1"}]


def test_a_window_found_only_by_its_pictures_is_returned_with_its_best_keyframe(
    session, cameras, add_window
):
    front, _ = cameras
    add_window(front, 0, near(Q_TEXT, 0.3, "a"))
    pictures = [near(Q_PICTURE, c, f"p{c}") for c in (0.2, 0.9, 0.4)]
    seen = add_window(front, 9, None, pictures)
    hits = run(session)
    hit = next(h for h in hits if h.window_id == seen.id)
    assert hit.text_rank is None and hit.visual_rank == 1
    assert hit.visual_score == pytest.approx(float(pictures[1] @ Q_PICTURE), abs=1e-4)
    best = session.get(Keyframe, hit.keyframe_id)
    assert best.timestamp == seen.start_time + timedelta(seconds=3)


def test_found_by_both_indexes_ranks_first(session, cameras, add_window):
    front, _ = cameras
    add_window(front, 0, near(Q_TEXT, 0.95, "a"))  # best text, no pictures
    add_window(front, 9, None, [near(Q_PICTURE, 0.95, "b")])  # best picture, no text
    both = add_window(front, 18, near(Q_TEXT, 0.9, "c"), [near(Q_PICTURE, 0.9, "d")])
    assert run(session)[0].window_id == both.id


def test_the_camera_filter_keeps_only_those_cameras(session, cameras, add_window):
    front, yard = cameras
    add_window(yard, 0, near(Q_TEXT, 0.9, "a"))
    mine = add_window(front, 0, near(Q_TEXT, 0.3, "b"))
    assert [h.window_id for h in run(session, camera_ids=[front.id])] == [mine.id]


def test_the_time_filter_keeps_overlapping_windows(session, cameras, add_window):
    front, _ = cameras
    before = add_window(front, 0, near(Q_TEXT, 0.9, "a"))  # 09:00:00-09:00:12
    overlapping = add_window(front, 55, near(Q_TEXT, 0.8, "b"))  # 09:00:55-09:01:07
    inside = add_window(front, 70, near(Q_TEXT, 0.7, "c"))  # 09:01:10-09:01:22
    after = add_window(front, 200, near(Q_TEXT, 0.6, "d"))  # 09:03:20-
    found = {
        h.window_id
        for h in run(session, start=START + timedelta(minutes=1), end=START + timedelta(minutes=3))
    }
    assert found == {overlapping.id, inside.id}
    assert before.id not in found and after.id not in found


def test_only_done_windows_are_searched(session, cameras, add_window):
    front, _ = cameras
    add_window(front, 0, near(Q_TEXT, 0.9, "a"), status="pending")
    add_window(front, 9, near(Q_TEXT, 0.9, "b"), status="failed")
    done = add_window(front, 18, near(Q_TEXT, 0.2, "c"))
    assert [h.window_id for h in run(session)] == [done.id]


def test_a_camera_with_few_windows_still_gets_them_all(session, cameras, add_window):
    """500 closer windows on another camera; the HNSW index is forced, so without iterative
    scans the filter would leave next to nothing."""
    front, yard = cameras
    for n in range(500):
        add_window(yard, 12 * n, near(Q_TEXT, 0.9, f"yard-{n}"), [near(Q_PICTURE, 0.9, f"yp-{n}")])
    mine = {
        add_window(
            front, 12 * n, near(Q_TEXT, 0.3, f"front-{n}"), [near(Q_PICTURE, 0.3, f"fp-{n}")]
        ).id
        for n in range(5)
    }
    # Force the HNSW plan (Postgres would rather filter and sort exactly at this size).
    session.execute(text("SET enable_seqscan = off"))
    session.execute(text("SET enable_sort = off"))
    hits = run(session, camera_ids=[front.id])
    assert {h.window_id for h in hits} == mine


def test_the_limit_and_an_empty_database(session, cameras, add_window):
    assert run(session) == []
    front, _ = cameras
    for n in range(4):
        add_window(front, 12 * n, near(Q_TEXT, 0.5, f"w{n}"))
    assert len(run(session, limit=2)) == 2


def test_indexes_built_with_other_models_are_refused(session, cameras, add_window):
    ensure_index_settings(session, "other/text-model", "fake-image-embedder", EMBEDDING_DIM)
    session.commit()
    with pytest.raises(IndexSettingsMismatch):
        run(session)
