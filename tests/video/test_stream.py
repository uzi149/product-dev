"""The live FFmpeg reader: PPM pipe, segment list, and a run over a generated video."""

import io
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from shared.video.ffmpeg import FFmpegError, join_fragments, probe
from shared.video.stream import (
    StreamProcess,
    build_command,
    explain_error,
    mask_credentials,
    parse_fragment_list,
    read_pnm,
)
from tests.api.conftest import FFMPEG

needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


def ppm(width: int, height: int, rgb=(10, 20, 30)) -> bytes:
    pixels = np.tile(np.array(rgb, np.uint8), (height, width, 1))
    return f"P6\n{width} {height}\n255\n".encode() + pixels.tobytes()


def test_read_pnm_returns_bgr_frames_one_after_another():
    stream = io.BytesIO(ppm(4, 2, (10, 20, 30)) + ppm(4, 2, (1, 2, 3)))
    first, second = read_pnm(stream), read_pnm(stream)
    assert first.shape == (2, 4, 3)
    assert tuple(first[0, 0]) == (30, 20, 10)  # RGB on the pipe, BGR for OpenCV
    assert tuple(second[1, 3]) == (3, 2, 1)
    assert read_pnm(stream) is None


def test_read_pnm_reads_grey_images():
    stream = io.BytesIO(b"P5\n3 2\n255\n" + bytes(range(6)))
    frame = read_pnm(stream)
    assert frame.shape == (2, 3)
    assert frame[1, 2] == 5


def test_read_pnm_handles_a_header_split_over_lines_and_comments():
    stream = io.BytesIO(b"P6\n# made by a test\n2\n1\n255\n" + bytes(6))
    assert read_pnm(stream).shape == (1, 2, 3)


def test_read_pnm_returns_none_when_the_stream_ends_mid_frame():
    assert read_pnm(io.BytesIO(ppm(4, 2)[:-5])) is None
    assert read_pnm(io.BytesIO(b"P6\n4 2\n")) is None


def test_read_pnm_rejects_anything_else():
    with pytest.raises(FFmpegError):
        read_pnm(io.BytesIO(b"P3\n1 1\n255\n0 0 0\n"))
    with pytest.raises(FFmpegError):
        read_pnm(io.BytesIO(b"P6\n1 1\n65535\n" + bytes(6)))


def test_parse_fragment_list_skips_a_row_still_being_written():
    text = "frag-000000.ts,0.000000,5.400000\nfrag-000001.ts,5.400000,10.4"
    assert parse_fragment_list(text) == [("frag-000000.ts", 0.0, 5.4)]
    assert parse_fragment_list("") == []


def test_mask_credentials_hides_user_and_password_in_ffmpeg_output():
    line = "[rtsp @ 0x1] method DESCRIBE failed: 401 rtsp://admin:s3cret@10.0.0.5:554/stream1"
    assert (
        mask_credentials(line)
        == "[rtsp @ 0x1] method DESCRIBE failed: 401 rtsp://***@10.0.0.5:554/stream1"
    )
    assert mask_credentials("rtsp://10.0.0.5/s") == "rtsp://10.0.0.5/s"


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        (
            "Error opening input files: Server returned 404 Not Found",
            "the stream path does not exist on the camera or server (404)",
        ),
        (
            "[rtsp @ 0x1] method DESCRIBE failed: 401 Unauthorized",
            "wrong user name or password (401)",
        ),
        (
            "Error opening input files: Error number -138 occurred",
            "the camera did not answer (timed out)",
        ),
        ("Error opening input files: Connection refused", "connection refused"),
        ("Error opening input files: Something new.", "Something new"),
    ],
)
def test_ffmpeg_errors_are_explained_in_plain_words(line, reason):
    assert explain_error(line) == reason


def test_rtsp_sources_use_tcp_and_a_timeout_files_dont(tmp_path):
    rtsp = build_command("ffmpeg", "rtsp://cam/1", tmp_path, 1, 5, 960)
    assert rtsp[rtsp.index("-rtsp_transport") + 1] == "tcp"
    assert "-timeout" in rtsp
    file = build_command("ffmpeg", "clip.mp4", tmp_path, 2, 5, 640)
    assert "-rtsp_transport" not in file
    assert "fps=2:start_time=0:round=up,scale='min(640,iw)':-2" in file


def test_copied_fragments_fill_a_missing_timestamp_and_keep_the_others(tmp_path):
    # A B-frame stream joined at a keyframe can start without one of the two timestamps.
    command = build_command("ffmpeg", "rtsp://cam/1", tmp_path, 1, 5, 960)
    setts = command[command.index("-bsf:v") + 1]
    assert setts == r"setts=pts=if(eq(PTS\,NOPTS)\,DTS\,PTS):dts=if(eq(DTS\,NOPTS)\,PTS\,DTS)"
    assert command.index("-bsf:v") < command.index("segment")  # on the fragments, not the frames


@pytest.fixture(scope="module")
def camera_clip(tmp_path_factory):
    from tests.ingestion.videos import camera_video

    if FFMPEG is None:
        pytest.skip("ffmpeg is not installed")
    return camera_video(tmp_path_factory.mktemp("stream") / "clip.mp4", 23, [(5, 8)])


def run_to_end(source: Path, folder: Path, fps: int = 1) -> tuple[list, list]:
    stream = StreamProcess(
        str(source), folder, fps=fps, fragment_seconds=5, width=160, label="test"
    )
    stream.start()
    frames, fragments = [], []
    while (item := stream.frames.get(timeout=30)) is not None:
        frames.append(item)
        fragments += stream.new_fragments()
    stream.stop()
    fragments += stream.new_fragments()
    return frames, fragments


@needs_ffmpeg
def test_a_run_gives_one_frame_per_second_and_back_to_back_fragments(camera_clip, tmp_path):
    frames, fragments = run_to_end(camera_clip, tmp_path)
    assert [t for t, _ in frames] == list(range(23))
    assert frames[0][1].shape == (120, 160, 3)  # scaled to the width, aspect kept
    assert [round(f.start) for f in fragments] == [0, 5, 10, 15, 20]
    assert fragments[-1].end == pytest.approx(23, abs=0.25)
    for before, after in pairwise(fragments):
        assert after.start == before.end
    assert all(f.path.exists() for f in fragments)


@needs_ffmpeg
def test_two_frames_per_second(camera_clip, tmp_path):
    frames, _ = run_to_end(camera_clip, tmp_path, fps=2)
    assert [t for t, _ in frames[:4]] == [0, 0.5, 1, 1.5]


@needs_ffmpeg
def test_fragments_join_into_a_playable_mp4_of_the_same_length(camera_clip, tmp_path):
    _, fragments = run_to_end(camera_clip, tmp_path / "run")
    source = probe(camera_clip)
    info = join_fragments([f.path for f in fragments[1:3]], tmp_path / "joined.mp4")
    assert (info.codec, info.pix_fmt) == ("h264", source.pix_fmt)  # copied, not re-encoded
    assert info.duration_s == pytest.approx(10, abs=0.25)
    assert probe(tmp_path / "joined.mp4").duration_s == pytest.approx(10, abs=0.25)
    assert not list(tmp_path.glob("*.txt"))  # the concat list is cleaned up


@needs_ffmpeg
def test_a_missing_source_ends_the_run_with_an_error(tmp_path):
    stream = StreamProcess(
        str(tmp_path / "missing.mp4"),
        tmp_path / "run",
        fps=1,
        fragment_seconds=5,
        width=160,
        label="t",
    )
    stream.start()
    assert stream.frames.get(timeout=30) is None
    stream.stop()
    assert stream.returncode != 0
    assert stream.last_error()


@needs_ffmpeg
def test_partial_fragment_after_a_kill_is_read_from_the_file(camera_clip, tmp_path):
    folder = tmp_path / "run"
    _, fragments = run_to_end(camera_clip, folder)
    # Pretend FFmpeg died while writing the last fragment: its list row is missing.
    last = fragments[-1]
    listing = folder / "fragments.csv"
    listing.write_text("".join(listing.read_text().splitlines(keepends=True)[:-1]))
    stream = StreamProcess(
        str(camera_clip), folder, fps=1, fragment_seconds=5, width=160, label="t"
    )
    assert len(stream.new_fragments()) == len(fragments) - 1
    partial = stream.partial_fragment()
    assert partial.index == last.index
    assert partial.start == pytest.approx(last.start)
    assert partial.end == pytest.approx(last.end, abs=0.25)
    assert stream.partial_fragment() is None


@needs_ffmpeg
def test_other_codecs_are_re_encoded_to_h264(tmp_path):
    from tests.api.conftest import make_video

    clip = make_video(tmp_path / "mpeg4.ts", codec="mpeg4", seconds=2)
    info = join_fragments([clip], tmp_path / "joined.mp4")
    assert (info.codec, info.pix_fmt) == ("h264", "yuv420p")
