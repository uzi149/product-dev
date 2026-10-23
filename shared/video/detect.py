"""Activity intervals of a whole video file.

Also a command for checking thresholds on real footage:

    python -m shared.video.detect test-data/camera-01/static-room.mp4 [--fps 1] [--config '{...}']
"""

import argparse
import json
from pathlib import Path

from shared.config import Settings, get_settings
from shared.video.change_detector import (
    ActivityEnded,
    ChangeDetector,
    DetectionParams,
    resolve_params,
)
from shared.video.frames import sample_frames


def find_intervals(
    path: str | Path, params: DetectionParams, fps: float = 1.0
) -> list[ActivityEnded]:
    """Run the change detector over the file; an interval still open at the end is closed there."""
    detector = ChangeDetector(params)
    intervals = []
    for t, frame in sample_frames(path, fps):
        event = detector.update(frame, t).event
        if isinstance(event, ActivityEnded):
            intervals.append(event)
    last = detector.finish()
    if last is not None:
        intervals.append(last)
    return intervals


def main(argv: list[str] | None = None, settings: Settings | None = None) -> None:
    parser = argparse.ArgumentParser(description="Print the activity intervals of a video file.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--fps", type=float, default=1.0, help="frames sampled per second")
    parser.add_argument("--config", default="{}", help="detection_config JSON to try")
    args = parser.parse_args(argv)

    params = resolve_params(json.loads(args.config), settings or get_settings())
    intervals = find_intervals(args.video, params, args.fps)
    for interval in intervals:
        print(f"{interval.start:8.1f} s -> {interval.end:8.1f} s   score {interval.score:.3f}")
    print(f"{len(intervals)} activity interval(s)")


if __name__ == "__main__":
    main()
