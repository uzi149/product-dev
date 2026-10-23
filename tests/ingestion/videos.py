"""Generated test videos with activity at known times: no video file is committed."""

from pathlib import Path

import cv2

from tests.video.scenes import HEIGHT, WIDTH, Camera, background, with_object

FPS = 5


def activity_video(path: Path, seconds: int, bursts: list[tuple[int, int]]) -> Path:
    """A still textured scene; during each (start, end) burst an object walks across it.

    The object is gone at `end`, so the detector sees the last motion at `end`.
    """
    scene, camera = background(), Camera()
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (WIDTH, HEIGHT))
    for n in range(seconds * FPS):
        second = n // FPS
        frame = scene
        for start, end in bursts:
            if start <= second < end:
                frame = with_object(scene, 20 + 15 * ((second - start) % 18))
        writer.write(cv2.cvtColor(camera(frame), cv2.COLOR_GRAY2BGR))
    writer.release()
    return path
