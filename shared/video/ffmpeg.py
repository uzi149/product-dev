"""Thin wrappers around the ffprobe and ffmpeg programs (they must be on PATH)."""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FFmpegError(Exception):
    pass


class FFmpegNotInstalled(FFmpegError):
    pass


@dataclass(frozen=True)
class VideoInfo:
    duration_s: float
    codec: str
    pix_fmt: str | None
    width: int
    height: int

    @property
    def duration_ms(self) -> int:
        return round(self.duration_s * 1000)


def _require(program: str) -> str:
    path = shutil.which(program)
    if path is None:
        raise FFmpegNotInstalled(f"{program} not found on PATH (winget install Gyan.FFmpeg)")
    return path


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def parse_probe(data: dict) -> VideoInfo:
    """Turn ffprobe's JSON into a VideoInfo; raises FFmpegError when there is no video."""
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise FFmpegError("the file has no video stream")
    duration = video.get("duration") or data.get("format", {}).get("duration")
    try:
        duration_s = float(duration)
    except (TypeError, ValueError) as error:
        raise FFmpegError("the video has no readable duration") from error
    return VideoInfo(
        duration_s=duration_s,
        codec=video.get("codec_name", "unknown"),
        pix_fmt=video.get("pix_fmt"),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
    )


def probe(path: str | Path) -> VideoInfo:
    result = _run(
        [
            _require("ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe could not read the file: {result.stderr.strip()[-300:]}")
    return parse_probe(json.loads(result.stdout or "{}"))


def to_h264_mp4(src: str | Path, dst: str | Path, info: VideoInfo | None = None) -> None:
    """Write `dst` as a browser-playable MP4 (H.264, yuv420p, moov atom first, no audio).

    The video is only re-encoded when it isn't already H.264 yuv420p; otherwise it is copied.
    Audio is dropped: the analysis only uses frames.
    """
    info = info or probe(src)
    if info.codec == "h264" and info.pix_fmt == "yuv420p":
        video = ["-c:v", "copy"]
    else:
        video = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    result = _run(
        [
            _require("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-map",
            "0:v:0",
            *video,
            "-an",
            "-movflags",
            "+faststart",
            str(dst),
        ]
    )
    if result.returncode != 0:
        raise FFmpegError(f"ffmpeg failed: {result.stderr.strip()[-300:]}")
