"""The inspect command (`python -m services.analysis.inspect`)."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from services.analysis import inspect, prompts
from services.analysis.tasks import Models, process_segment
from shared.config import Settings
from shared.database.models import Camera, VideoSegment
from shared.storage.keys import segment_key
from tests.fakes import FakeImageEmbedder, FakeTextEmbedder, FakeVLM
from tests.ingestion.videos import activity_video

START = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)


@pytest.fixture
def analysed(session, storage, engine, tmp_path):
    """A 20 s segment analysed with the fake models: two windows."""
    camera = Camera(display_name="Inspect cam", source_type="file")
    session.add(camera)
    session.commit()
    segment_id = uuid4()
    key = segment_key(camera.id, START, segment_id)
    uri = storage.upload_file(key, activity_video(tmp_path / "v.avi", 20, [(5, 10)]))
    session.add(
        VideoSegment(
            id=segment_id,
            camera_id=camera.id,
            start_time=START,
            end_time=START + timedelta(seconds=20),
            duration_ms=20_000,
            storage_uri=uri,
        )
    )
    session.commit()
    process_segment(
        segment_id,
        lambda: Session(engine),
        storage,
        Settings(_env_file=None),
        Models(FakeVLM([json.dumps(prompts.EXAMPLE)]), FakeTextEmbedder(), FakeImageEmbedder()),
    )
    yield segment_id
    for prefix in ("segments", "keyframes"):
        storage.delete_prefix(f"{prefix}/{camera.id}/")


@pytest.fixture
def run(monkeypatch, engine, storage, capsys):
    monkeypatch.setattr(inspect, "get_sessionmaker", lambda: lambda: Session(engine))
    monkeypatch.setattr(inspect, "get_storage", lambda: storage)

    def run_inspect(*args):
        code = inspect.main(list(args))
        out = capsys.readouterr()
        return code, out.out, out.err

    return run_inspect


def test_the_windows_are_printed_in_order(analysed, run):
    code, out, _ = run(str(analysed))
    assert code == 0
    assert "camera Inspect cam" in out and "[done]" in out
    assert out.index("Window 1") < out.index("Window 2")
    assert prompts.EXAMPLE["summary"] in out
    assert "person_1 place_object object_1 (5.1-7.4 s)" in out
    assert "visible: person, bag" in out
    assert "confidence 0.88, fake-vlm, prompt v1" in out
    assert out.count("keyframe ") == 12 and "X-Amz-Signature" in out


def test_json_matches_the_stored_rows(analysed, run):
    code, out, _ = run(str(analysed), "--json")
    (segment,) = json.loads(out)
    assert code == 0 and segment["status"] == "done"
    assert [w["status"] for w in segment["windows"]] == ["done", "done"]
    assert segment["windows"][0]["actions"][0]["type"] == "enter"
    assert all(k["has_vector"] for w in segment["windows"] for k in w["keyframes"])


def test_latest_segments_of_a_camera(analysed, run):
    code, out, _ = run("--camera", "Inspect cam", "--latest", "3")
    assert code == 0 and str(analysed) in out


@pytest.mark.parametrize("args", [(str(uuid4()),), ("--camera", "No such camera")])
def test_unknown_segments_and_cameras_are_errors(run, migrated_db, args):
    code, _, err = run(*args)
    assert code == 2 and "no " in err
