import cv2
import pytest

from shared.config import Settings
from shared.video.change_detector import resolve_params
from shared.video.detect import find_intervals, main
from tests.video.scenes import HEIGHT, WIDTH, Camera, background, with_object

FPS = 5


def scene_at(scene, second):
    """Still 0-9 s, walking 10-14 s, still 15-39 s, walking 40-43 s, still to 60 s."""
    if 10 <= second < 15:
        return with_object(scene, 20 + 15 * (second - 10))
    if 40 <= second < 44:
        return with_object(scene, 200 - 15 * (second - 40))
    return scene


@pytest.fixture(scope="module")
def two_bursts(tmp_path_factory):
    path = tmp_path_factory.mktemp("detect") / "two-bursts.avi"
    scene, camera = background(), Camera()
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (WIDTH, HEIGHT))
    for n in range(60 * FPS):
        writer.write(cv2.cvtColor(camera(scene_at(scene, n // FPS)), cv2.COLOR_GRAY2BGR))
    writer.release()
    return path


@pytest.fixture
def params():
    return resolve_params({}, Settings(_env_file=None))


def test_find_intervals_matches_the_scripted_activity(two_bursts, params):
    intervals = find_intervals(two_bursts, params)
    # Each burst ends one second after the last walking frame, when the object vanishes.
    assert [(i.start, i.end) for i in intervals] == [(10.0, 15.0), (40.0, 44.0)]
    assert all(0 < i.score <= 1 for i in intervals)


def test_an_interval_open_at_the_end_is_closed(two_bursts, params, tmp_path):
    # Cut the file in the middle of the second burst.
    cut = tmp_path / "cut.avi"
    reader = cv2.VideoCapture(str(two_bursts))
    writer = cv2.VideoWriter(str(cut), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (WIDTH, HEIGHT))
    for _ in range(42 * FPS):
        writer.write(reader.read()[1])
    writer.release()
    reader.release()

    intervals = find_intervals(cut, params)
    assert [(i.start, i.end) for i in intervals] == [(10.0, 15.0), (40.0, 41.0)]


def test_command_prints_the_intervals(two_bursts, capsys):
    main([str(two_bursts)], settings=Settings(_env_file=None))
    out = capsys.readouterr().out
    assert "10.0 s ->     15.0 s" in out
    assert "2 activity interval(s)" in out


def test_command_accepts_a_config_to_try(two_bursts, capsys):
    # Requiring 90% of the image to change finds nothing.
    main(
        [str(two_bursts), "--config", '{"changed_fraction": 0.9}'],
        settings=Settings(_env_file=None),
    )
    assert "0 activity interval(s)" in capsys.readouterr().out
