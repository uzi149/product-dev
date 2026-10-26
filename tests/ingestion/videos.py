"""Generated test videos with activity at known times: no video file is committed.

Also makes the default clips of the simulated cameras (Compose profile `sim`):

    python -m tests.ingestion.videos test-data/sim
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import cv2

from shared.video.ffmpeg import _require
from tests.video.scenes import HEIGHT, WIDTH, Camera, background, with_object

FPS = 5

# The simulated cameras' default clips: (file, seconds, bursts of movement).
SIM_CLIPS = (
    ("cam-01.mp4", 60, [(20, 28)]),
    ("cam-02.mp4", 90, [(15, 22), (55, 65)]),
)


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


def camera_video(path: Path, seconds: int, bursts: list[tuple[int, int]]) -> Path:
    """`activity_video` as a camera would send it: H.264, yuv420p, a keyframe every second."""
    with tempfile.TemporaryDirectory(prefix="sentinel-video-") as tmp:
        raw = activity_video(Path(tmp, "raw.avi"), seconds, bursts)
        subprocess.run(
            [
                _require("ffmpeg"),
                "-y",
                "-v",
                "error",
                "-i",
                str(raw),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-force_key_frames",
                "expr:gte(t,n_forced*1)",
                str(path),
            ],
            check=True,
        )
    return path


def main(argv: list[str] | None = None) -> None:
    folder = Path((argv if argv is not None else sys.argv[1:] or ["test-data/sim"])[0])
    folder.mkdir(parents=True, exist_ok=True)
    for name, seconds, bursts in SIM_CLIPS:
        camera_video(folder / name, seconds, bursts)
        moves = ", ".join(f"{a}-{b} s" for a, b in bursts)
        print(f"{folder / name}: {seconds} s, movement at {moves}")


if __name__ == "__main__":
    main()
