import cv2
import numpy as np
import pytest

from shared.video.frames import FrameReadError, sample_frames


@pytest.fixture(scope="module")
def counting_video(tmp_path_factory):
    """10 s at 25 fps; frame n has grey level n so the sampled frame can be identified."""
    path = tmp_path_factory.mktemp("frames") / "count.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25, (64, 48))
    for n in range(250):
        writer.write(np.full((48, 64, 3), n, np.uint8))
    writer.release()
    return path


def test_one_frame_per_second(counting_video):
    samples = list(sample_frames(counting_video, fps=1))
    assert [round(t, 3) for t, _ in samples] == [float(s) for s in range(10)]
    # The frame at t seconds is frame 25·t.
    for t, frame in samples:
        assert abs(int(frame.mean()) - round(25 * t)) <= 3


def test_other_sampling_rates(counting_video):
    assert len(list(sample_frames(counting_video, fps=2))) == 20
    assert len(list(sample_frames(counting_video, fps=0.5))) == 5
    # Asking for more than the file has gives every frame once.
    assert len(list(sample_frames(counting_video, fps=50))) == 250


def test_missing_file_and_bad_rate(tmp_path, counting_video):
    with pytest.raises(FrameReadError):
        list(sample_frames(tmp_path / "missing.mp4"))
    with pytest.raises(ValueError):
        list(sample_frames(counting_video, fps=0))
