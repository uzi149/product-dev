"""Opening a camera source with OpenCV."""

import os
from dataclasses import dataclass

# RTSP over TCP, with a socket timeout (microseconds). Must be set before the first capture.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000")

import cv2


@dataclass(frozen=True)
class ConnectionResult:
    ok: bool
    width: int | None = None
    height: int | None = None
    error: str | None = None


def check_connection(uri: str, timeout_s: float = 5.0) -> ConnectionResult:
    """Open the source, read one frame, and report its size."""
    timeout_ms = int(timeout_s * 1000)
    capture = cv2.VideoCapture(
        uri,
        cv2.CAP_FFMPEG,
        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms, cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms],
    )
    try:
        if not capture.isOpened():
            return ConnectionResult(ok=False, error="could not open the source")
        ok, frame = capture.read()
        if not ok or frame is None:
            return ConnectionResult(ok=False, error="opened the source but could not read a frame")
        height, width = frame.shape[:2]
        return ConnectionResult(ok=True, width=width, height=height)
    finally:
        capture.release()
