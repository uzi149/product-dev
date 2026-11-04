"""Frames prepared for a model: longest side, JPEG data URLs."""

import base64

import cv2
import numpy as np
import pytest

from shared.model_adapters.images import jpeg_data_url, resize_max_side


@pytest.mark.parametrize(
    "shape, max_side, expected",
    [
        ((1080, 1920, 3), 640, (360, 640, 3)),
        ((1920, 1080, 3), 640, (640, 360, 3)),
        ((240, 320, 3), 640, (240, 320, 3)),  # smaller: unchanged
        ((640, 640, 3), 640, (640, 640, 3)),
        ((720, 1280), 320, (180, 320)),  # grey frames too
    ],
)
def test_resize_max_side(shape, max_side, expected):
    assert resize_max_side(np.zeros(shape, np.uint8), max_side).shape == expected


def test_a_small_frame_is_returned_as_it_is():
    frame = np.zeros((10, 10, 3), np.uint8)
    assert resize_max_side(frame, 640) is frame


def test_jpeg_data_url_decodes_back_to_the_picture():
    frame = np.zeros((90, 160, 3), np.uint8)
    frame[:, :80] = (0, 0, 255)  # left half red (BGR)
    url = jpeg_data_url(frame)
    assert url.startswith("data:image/jpeg;base64,")
    decoded = cv2.imdecode(np.frombuffer(base64.b64decode(url.split(",", 1)[1]), np.uint8), 1)
    assert decoded.shape == frame.shape
    assert decoded[45, 20, 2] > 200 and decoded[45, 140, 2] < 50
