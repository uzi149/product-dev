"""One FFmpeg process per live camera (ADR 0014).

It holds a single RTSP connection and writes two outputs:

- the video stream, copied without re-encoding, into MPEG-TS fragments of about
  `fragment_seconds`, plus FFmpeg's segment list (CSV) with each fragment's start and end;
- frames at the analysis rate, as PPM images on stdout, for change detection and snapshots.

Both outputs share one timeline, "stream time": seconds since this run started. Frame n is at
n / fps. `round=up` makes frame n show the picture at n s instead of about n + 0.5 s.
"""

import logging
import queue
import re
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import cv2
import numpy as np

from shared.video.ffmpeg import FFmpegError, _require, probe

log = logging.getLogger(__name__)

LIST_NAME = "fragments.csv"
_FRAGMENT_PATTERN = "frag-%06d.ts"
# Frames waiting for the camera worker. At 1 fps it never fills; when it does, FFmpeg waits.
_FRAME_QUEUE = 30
_CREDENTIALS = re.compile(r"(rtsps?://)[^/@\s]+@", re.IGNORECASE)


# FFmpeg's wording (and Windows error numbers) for the usual RTSP failures, for the operator.
_EXPLAINED = (
    (re.compile(r"404 Not Found"), "the stream path does not exist on the camera or server (404)"),
    (re.compile(r"401 Unauthorized"), "wrong user name or password (401)"),
    (re.compile(r"Connection refused|Error number -(107|111|10061) "), "connection refused"),
    (
        re.compile(r"timed out|Error number -(138|110|10060) "),
        "the camera did not answer (timed out)",
    ),
    (re.compile(r"No route to host|Error number -(113|118|10065) "), "no route to the camera"),
    (re.compile(r"Invalid data found when processing input"), "not a readable video stream"),
)
_PREFIXES = re.compile(r"^(\[[^\]]+\]\s*)?(Error opening input files?:?\s*)?")


def explain_error(line: str) -> str:
    """A short, plain reason from an FFmpeg error line (credentials already masked)."""
    for pattern, reason in _EXPLAINED:
        if pattern.search(line + " "):
            return reason
    return _PREFIXES.sub("", line).rstrip(".") or line


def fragment_name(index: int) -> str:
    return _FRAGMENT_PATTERN % index


def mask_credentials(text: str) -> str:
    """rtsp://user:pw@host/... -> rtsp://***@host/... anywhere in a line of FFmpeg output."""
    return _CREDENTIALS.sub(r"\1***@", text)


@dataclass(frozen=True)
class Fragment:
    """A finished fragment; times are stream time in seconds."""

    index: int
    path: Path
    start: float
    end: float


def is_live(source: str) -> bool:
    return source.lower().startswith(("rtsp://", "rtsps://"))


def input_options(source: str) -> list[str]:
    if is_live(source):
        # TCP, and give up after 5 s without data (microseconds), so a dead stream ends the run.
        return ["-rtsp_transport", "tcp", "-timeout", "5000000"]
    return []  # a file (tests): read as fast as possible


def build_command(
    ffmpeg: str, source: str, folder: Path, fps: int, fragment_seconds: int, width: int
) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        *input_options(source),
        "-i",
        source,
        # 1. Fragments: the video stream as it is, split at the first keyframe after each interval.
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        # With B-frames, an RTSP stream joined right at a keyframe can give the first packet
        # only one of its two timestamps, and the MPEG-TS muxer refuses that first packet,
        # ending the run (about 1 join in 7). Fill a missing one from the other; packets that
        # have both, and files, are left as they are.
        "-bsf:v",
        r"setts=pts=if(eq(PTS\,NOPTS)\,DTS\,PTS):dts=if(eq(DTS\,NOPTS)\,PTS\,DTS)",
        "-an",
        "-f",
        "segment",
        "-segment_time",
        str(fragment_seconds),
        "-segment_format",
        "mpegts",
        "-segment_list",
        str(folder / LIST_NAME),
        "-segment_list_type",
        "csv",
        "-segment_list_flags",
        "+live",
        "-reset_timestamps",
        "1",
        # Keep the stream's own timestamps: the default shifts them by the B-frame delay, and
        # the list times would no longer match the frames.
        "-avoid_negative_ts",
        "disabled",
        str(folder / _FRAGMENT_PATTERN),
        # 2. Frames for change detection and snapshots, on the same timeline.
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        f"fps={fps}:start_time=0:round=up,scale='min({width},iw)':-2",
        "-c:v",
        "ppm",
        "-f",
        "image2pipe",
        "pipe:1",
    ]


def read_pnm(stream: BinaryIO) -> np.ndarray | None:
    """Read one binary PPM (P6, returned as BGR) or PGM (P5, grey) image.

    Returns None at the end of the stream, also when it ends in the middle of an image.
    """
    tokens: list[bytes] = []
    while len(tokens) < 4:
        line = stream.readline()
        if not line:
            return None
        tokens += line.split(b"#", 1)[0].split()
    magic, width, height, maxval = tokens[0], int(tokens[1]), int(tokens[2]), int(tokens[3])
    if magic not in (b"P5", b"P6") or maxval != 255 or len(tokens) != 4:
        raise FFmpegError(f"unexpected image header from ffmpeg: {b' '.join(tokens)[:40]!r}")
    channels = 3 if magic == b"P6" else 1
    size = width * height * channels
    data = stream.read(size)
    if len(data) < size:
        return None
    if channels == 1:
        return np.frombuffer(data, np.uint8).reshape(height, width)
    return cv2.cvtColor(np.frombuffer(data, np.uint8).reshape(height, width, 3), cv2.COLOR_RGB2BGR)


def parse_fragment_list(text: str) -> list[tuple[str, float, float]]:
    """Rows of FFmpeg's CSV segment list. A last line still being written is left out."""
    rows = []
    for line in text.split("\n")[:-1]:
        name, start, end = line.strip().rsplit(",", 2)
        rows.append((name, float(start), float(end)))
    return rows


class StreamProcess:
    """One FFmpeg run for one camera.

    `frames` yields (stream time, BGR frame) and then None when FFmpeg has stopped.
    `new_fragments()` returns the fragments finished since the last call.
    """

    def __init__(
        self, source: str, folder: Path, fps: int, fragment_seconds: int, width: int, label: str
    ):
        self.source = source
        self.folder = folder
        self.fps = fps
        self.fragment_seconds = fragment_seconds
        self.width = width
        self.label = label
        self.frames: queue.Queue[tuple[float, np.ndarray] | None] = queue.Queue(_FRAME_QUEUE)
        self.errors: deque[str] = deque(maxlen=20)
        self._process: subprocess.Popen | None = None
        self._threads: list[threading.Thread] = []
        self._closing = threading.Event()
        self._seen = 0
        self._last_end = 0.0

    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        command = build_command(
            _require("ffmpeg"),
            self.source,
            self.folder,
            self.fps,
            self.fragment_seconds,
            self.width,
        )
        # Own process group: Ctrl+C in the console reaches only Python, which stops FFmpeg
        # cleanly with `q`, so the last fragment and its list row are written.
        if sys.platform == "win32":
            group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            group = {"start_new_session": True}
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **group,
        )
        for target in (self._read_frames, self._read_errors):
            thread = threading.Thread(
                target=target, name=f"{self.label}-{target.__name__}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    @property
    def returncode(self) -> int | None:
        return None if self._process is None else self._process.poll()

    def last_error(self) -> str | None:
        return explain_error(self.errors[-1]) if self.errors else None

    def new_fragments(self) -> list[Fragment]:
        try:
            text = (self.folder / LIST_NAME).read_text()
        except OSError:
            return []  # not written yet
        new = []
        for name, start, end in parse_fragment_list(text)[self._seen :]:
            path = self.folder / name
            if self._seen == 0:
                start = self._first_start(path, start, end)
            new.append(Fragment(self._seen, path, start, end))
            self._seen += 1
            self._last_end = end
        return new

    def partial_fragment(self) -> Fragment | None:
        """After FFmpeg was killed: the fragment it was writing (no list row), if it can be read."""
        path = self.folder / fragment_name(self._seen)
        if not path.exists():
            return None
        try:
            duration = probe(path).duration_s
        except FFmpegError:
            return None
        if duration <= 0:
            return None
        fragment = Fragment(self._seen, path, self._last_end, self._last_end + duration)
        self._seen += 1
        self._last_end = fragment.end
        return fragment

    def stop(self, timeout: float = 5.0) -> None:
        """Ask FFmpeg to finish (`q`), kill it after `timeout`, and wait for the reader threads."""
        self._closing.set()  # from now on frames are read and dropped, so FFmpeg never blocks
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.stdin.write(b"q")
                process.stdin.flush()
            except OSError:
                pass
            try:
                process.wait(timeout)
            except subprocess.TimeoutExpired:
                log.warning(
                    "%s: ffmpeg did not stop within %.0f s; killing it", self.label, timeout
                )
                process.kill()
                process.wait()
        for thread in self._threads:
            thread.join(timeout)
        if process is not None:
            for pipe in (process.stdin, process.stdout, process.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass

    def _first_start(self, path: Path, start: float, end: float) -> float:
        # The list gives the first fragment a start of 0, but its first picture is the first
        # keyframe received, a moment later: start = end - its duration.
        try:
            return max(start, end - probe(path).duration_s)
        except FFmpegError:
            return start

    def _put(self, item) -> None:
        while not self._closing.is_set():
            try:
                self.frames.put(item, timeout=0.5)
                return
            except queue.Full:
                continue

    def _read_frames(self) -> None:
        n = 0
        try:
            while (frame := read_pnm(self._process.stdout)) is not None:
                self._put((n / self.fps, frame))
                n += 1
        except (FFmpegError, ValueError, OSError) as error:
            self.errors.append(f"bad frame data from ffmpeg: {error}")
        finally:
            # The end marker must arrive even when the queue is full and the worker is closing.
            if self._closing.is_set():
                try:
                    self.frames.put_nowait(None)
                except queue.Full:
                    pass
            else:
                self._put(None)

    def _read_errors(self) -> None:
        # Decoder notices can come every frame on a poor stream: kept at debug level. The last
        # line becomes the run's error, which the camera worker logs when it reconnects.
        for raw in self._process.stderr:
            line = mask_credentials(raw.decode(errors="replace").strip())
            if line:
                self.errors.append(line)
                log.debug("%s: ffmpeg: %s", self.label, line)
