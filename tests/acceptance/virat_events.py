"""Draft ground-truth YAML from VIRAT's annotations, to be checked by eye afterwards.

    python -m tests.acceptance.virat_events VIDEO EVENTS_TXT [OBJECTS_TXT] -o test-data/acceptance/virat/NAME.yaml

VIRAT's event annotations (`*.viratdata.events.txt`) become actions of our vocabulary; the object
tracks (`*.viratdata.objects.txt`) add walking people and moving or parking vehicles. Frame
numbers become seconds with the video's own frame rate (scenes 0100-0102 are 23.97 fps, not 30).
Overlapping events of one action are merged, because the pipeline is judged per window.
Queries are left for a person to write after watching the clip.
"""

import argparse
import itertools
import os
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import yaml

# VIRAT event type -> (our action, other words accepted for it). Types without an action in our
# vocabulary (3 open trunk, 4 close trunk, 7 gesturing, 8 digging) are left out.
EVENT_ACTIONS = {
    1: (
        "place_object",
        ("carry_object", "leave_object_behind"),
    ),  # loading an object into a vehicle
    2: ("pick_up_object", ("carry_object",)),  # unloading an object from a vehicle
    5: ("enter", ("leave",)),  # getting into a vehicle
    6: ("leave", ("enter", "walk")),  # getting out of a vehicle
    9: ("carry_object", ("pick_up_object", "walk")),
    10: ("run", ("walk",)),
    11: ("enter", ()),  # entering a facility
    12: ("leave", ()),  # exiting a facility
}
PERSON, CAR, VEHICLE = 1, 2, 3
# A second counts as movement when a box centre moves more than this (as in step 2.2).
MOVE_PIXELS = 10
# Movement shorter than this is ignored; gaps up to MERGE_GAP are bridged.
MIN_SPAN_SECONDS = 3
MERGE_GAP_SECONDS = 2
# Objects smaller than this (box height in pixels) are too small to expect the VLM to see.
MIN_HEIGHT = 40
START_TIME = "2026-03-07T09:00:00Z"


def video_info(path: Path) -> tuple[float, float]:
    """(frames per second, length in seconds) of the file itself."""
    capture = cv2.VideoCapture(str(path))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
    finally:
        capture.release()
    if not fps:
        raise ValueError(f"could not read the frame rate of {path}")
    return fps, frames / fps


def _rows(text: str, columns: int) -> list[list[int]]:
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= columns:
            rows.append([int(float(p)) for p in parts[:columns]])
    return rows


def merge(spans: list[tuple[float, float]], gap: float = 0) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def events_from_annotations(text: str, fps: float, length: float) -> list[dict]:
    """VIRAT events -> our actions, merged per action and clipped to the video."""
    first: dict[int, tuple[int, int, int]] = {}
    for event_id, event_type, _, start, end in _rows(text, 5):
        first.setdefault(event_id, (event_type, start, end))
    spans: dict[str, list[tuple[float, float]]] = defaultdict(list)
    also: dict[str, tuple[str, ...]] = {}
    for event_type, start, end in first.values():
        if event_type not in EVENT_ACTIONS:
            continue
        action, words = EVENT_ACTIONS[event_type]
        start_s, end_s = start / fps, end / fps
        if start_s >= length or end_s <= 0:
            continue  # outside the clip
        spans[action].append((max(0.0, start_s), min(length, end_s)))
        also[action] = words
    return [
        {"action": action, "start_s": round(s, 1), "end_s": round(e, 1), "also": list(also[action])}
        for action, action_spans in spans.items()
        for s, e in merge(action_spans)
    ]


def _moving_spans(
    tracks: dict[int, list[tuple[int, int, int, int]]], fps: float, length: float
) -> list[tuple[float, float]]:
    """Seconds in which any of the tracks moves, merged into spans."""
    seconds: set[int] = set()
    for points in tracks.values():
        by_second: dict[int, tuple[float, float]] = {}
        for frame, cx, cy, _ in sorted(points):
            by_second.setdefault(int(frame / fps), (cx, cy))
        ordered = sorted(by_second.items())
        for (s0, (x0, y0)), (s1, (x1, y1)) in itertools.pairwise(ordered):
            if s1 - s0 == 1 and ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 > MOVE_PIXELS:
                seconds.add(s0)
    spans = merge([(s, s + 1) for s in seconds if s < length], MERGE_GAP_SECONDS)
    return [(s, min(e, length)) for s, e in spans if e - s >= MIN_SPAN_SECONDS]


def tracks_from_annotations(text: str) -> dict[int, dict[int, list[tuple[int, int, int, int]]]]:
    """object type -> object id -> [(frame, centre x, centre y, height)], big enough objects only."""
    tracks: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for object_id, _, frame, x, y, w, h, object_type in _rows(text, 8):
        if h >= MIN_HEIGHT:
            tracks[object_type][object_id].append((frame, x + w / 2, y + h / 2, h))
    return tracks


def movement_from_tracks(text: str, fps: float, length: float) -> tuple[list[dict], list[str]]:
    """Walking people and moving vehicles from the object tracks, and the entities seen."""
    tracks = tracks_from_annotations(text)
    events = [
        {
            "action": "walk",
            "start_s": round(s, 1),
            "end_s": round(e, 1),
            "also": ["run", "carry_object", "enter", "leave"],
        }
        for s, e in _moving_spans(tracks.get(PERSON, {}), fps, length)
    ]
    vehicles = {**tracks.get(CAR, {}), **tracks.get(VEHICLE, {})}
    events += [
        {
            "action": "vehicle_arrive",
            "start_s": round(s, 1),
            "end_s": round(e, 1),
            "also": ["vehicle_depart", "vehicle_park"],
        }
        for s, e in _moving_spans(vehicles, fps, length)
    ]
    entities = (["person"] if tracks.get(PERSON) else []) + (["car"] if vehicles else [])
    return events, entities


def draft(video: Path, events_text: str, objects_text: str | None, yaml_path: Path) -> dict:
    fps, length = video_info(video)
    events = events_from_annotations(events_text, fps, length)
    entities: list[str] = []
    if objects_text is not None:
        moves, entities = movement_from_tracks(objects_text, fps, length)
        events += moves
    events.sort(key=lambda e: (e["start_s"], e["action"]))
    return {
        "video": Path(os.path.relpath(video.resolve(), yaml_path.resolve().parent)).as_posix(),
        "start_time": START_TIME,
        "fps": round(fps, 3),
        "length_s": round(length, 1),
        "entities": entities,
        "events": events,
        "queries": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video", type=Path)
    parser.add_argument("events", type=Path, help="*.viratdata.events.txt")
    parser.add_argument("objects", type=Path, nargs="?", help="*.viratdata.objects.txt")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args(argv)
    data = draft(
        args.video,
        args.events.read_text(),
        args.objects.read_text() if args.objects else None,
        args.output,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "# Draft from VIRAT's annotations: check every event by eye, then add queries.\n"
        + yaml.safe_dump(data, sort_keys=False),
        encoding="utf-8",
    )
    print(
        f"wrote {args.output}: {len(data['events'])} event(s), {data['length_s']} s at {data['fps']} fps"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
