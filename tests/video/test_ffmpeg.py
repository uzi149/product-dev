import pytest

from shared.video.ffmpeg import FFmpegError, VideoInfo, parse_probe, probe, to_h264_mp4
from tests.api.conftest import make_video


def test_parse_probe_reads_the_video_stream():
    data = {
        "streams": [
            {"codec_type": "audio", "codec_name": "aac"},
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 1280,
                "height": 720,
                "duration": "12.5",
            },
        ],
        "format": {"duration": "12.6"},
    }
    info = parse_probe(data)
    assert info == VideoInfo(12.5, "h264", "yuv420p", 1280, 720)
    assert info.duration_ms == 12500


def test_parse_probe_falls_back_to_the_container_duration():
    data = {"streams": [{"codec_type": "video", "codec_name": "vp9"}], "format": {"duration": "4"}}
    assert parse_probe(data).duration_ms == 4000


def test_parse_probe_without_video_or_duration_fails():
    with pytest.raises(FFmpegError):
        parse_probe({"streams": [{"codec_type": "audio"}]})
    with pytest.raises(FFmpegError):
        parse_probe({"streams": [{"codec_type": "video"}], "format": {}})


def test_probe_and_conversion(tmp_path):
    source = make_video(tmp_path / "a.avi", codec="mpeg4")
    info = probe(source)
    assert (info.codec, info.width, info.height) == ("mpeg4", 320, 240)

    out = tmp_path / "out.mp4"
    to_h264_mp4(source, out, info)
    converted = probe(out)
    assert (converted.codec, converted.pix_fmt) == ("h264", "yuv420p")


def test_h264_yuv420p_is_copied_not_reencoded(tmp_path):
    source = make_video(tmp_path / "a.mp4", extra=("-pix_fmt", "yuv420p"))
    out = tmp_path / "out.mp4"
    to_h264_mp4(source, out)
    assert abs(probe(out).duration_s - probe(source).duration_s) < 0.2


def test_probe_of_garbage_fails(tmp_path):
    bogus = tmp_path / "x.mp4"
    bogus.write_text("nope")
    with pytest.raises(FFmpegError):
        probe(bogus)
