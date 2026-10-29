"""The search command (`python -m services.query_engine.search`)."""

import json

import pytest
from sqlalchemy.orm import Session

from services.query_engine import retrieval, search
from tests.search.conftest import IMAGE, Q_TEXT, TEXT, near


@pytest.fixture
def run(monkeypatch, engine, storage, capsys):
    monkeypatch.setattr(search, "get_sessionmaker", lambda: lambda: Session(engine))
    monkeypatch.setattr(search, "get_storage", lambda: storage)
    monkeypatch.setattr(retrieval, "get_text_embedder", lambda: TEXT)
    monkeypatch.setattr(retrieval, "get_image_embedder", lambda: IMAGE)

    def run_search(*args):
        code = search.main(list(args))
        out = capsys.readouterr()
        return code, out.out, out.err

    return run_search


@pytest.fixture
def windows(cameras, add_window):
    front, yard = cameras
    return (
        add_window(front, 9, near(Q_TEXT, 0.9, "a"), description="A person rings the bell."),
        add_window(yard, 0, near(Q_TEXT, 0.5, "b"), description="A cat crosses the lawn."),
    )


def test_hits_are_printed_with_scores_and_a_playback_link(windows, run):
    code, out, _ = run("a person rings the doorbell")
    assert code == 0
    assert out.index("1. Front door") < out.index("2. Yard")
    assert "A person rings the bell." in out and "actions: person_1 walk" in out
    assert "#t=9.0,21.0" in out and "X-Amz-Signature" in out


def test_json_matches_the_hits(windows, run):
    code, out, _ = run("doorbell", "--json", "--limit", "1")
    (hit,) = json.loads(out)
    assert code == 0 and hit["window_id"] == str(windows[0].id)
    assert hit["camera_name"] == "Front door" and hit["text_rank"] == 1
    assert hit["playback_url"].endswith("#t=9.0,21.0")
    assert (hit["segment_start_seconds"], hit["segment_end_seconds"]) == (9.0, 21.0)


def test_camera_names_are_not_case_sensitive_and_can_repeat(windows, run):
    _, out, _ = run("doorbell", "--camera", "yard", "--json")
    assert [h["camera_name"] for h in json.loads(out)] == ["Yard"]
    _, out, _ = run("doorbell", "--camera", "YARD", "--camera", "front door", "--json")
    assert len(json.loads(out)) == 2


def test_an_unknown_camera_lists_the_names(windows, run):
    code, _, err = run("doorbell", "--camera", "Garage")
    assert code == 2 and 'no camera named "Garage"' in err and "Front door, Yard" in err


def test_time_flags(windows, run):
    # The windows are in March 2026, so the last hour has nothing. The yard window ends at
    # 09:00:12, so it does not overlap a range starting then; the front door one (9-21 s) does.
    _, out, _ = run("doorbell", "--since", "1h")
    assert "no matching windows" in out
    _, out, _ = run("doorbell", "--start", "2026-03-07T09:00:12+00:00", "--json")
    assert [h["camera_name"] for h in json.loads(out)] == ["Front door"]
    with pytest.raises(SystemExit):
        run("doorbell", "--since", "1h", "--end", "2026-03-07T10:00")
