"""Frames prepared for a model: scaled down to a longest side, and JPEG data URLs.

Adapted from the reference repository's `_resize` and `image_to_data_url`, working on OpenCV
frames (numpy BGR arrays) instead of PIL images.
"""

import base64

import cv2
import numpy as np

JPEG_QUALITY = 85


def resize_max_side(frame: np.ndarray, max_side: int) -> np.ndarray:
    """Scale down so the longest side is at most `max_side`; smaller frames are returned as is."""
    height, width = frame.shape[:2]
    scale = max_side / max(height, width)
    if scale >= 1:
        return frame
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


def to_jpeg(frame: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("could not encode the frame as JPEG")
    return encoded.tobytes()


def jpeg_data_url(frame: np.ndarray, quality: int = JPEG_QUALITY) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(to_jpeg(frame, quality)).decode()
