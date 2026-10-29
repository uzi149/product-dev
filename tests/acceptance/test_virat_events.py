"""VIRAT annotations -> draft ground truth (no models)."""

import cv2
import numpy as np
import pytest
import yaml

from tests.acceptance import virat_events

# eventID eventType duration startFrame endFrame currentFrame x y w h (one line per frame)
EVENTS = """\
1 9 50 120 170 120 10 10 20 60
1 9 50 120 170 121 11 10 20 60
2 10 30 600 630 600 10 10 20 60
3 7 20 200 220 200 10 10 20 60
4 9 40 150 240 150 10 10 20 60
5 5 100 9000 9100 9000 10 10 20 60
"""


def test_events_become_our_actions_with_the_files_own_frame_rate():
    events = virat_events.events_from_annotations(EVENTS, fps=23.97, length=40)
    by_action = {e["action"]: e for e in events}
    # Event 1 (frames 120-170) and event 4 (150-240) are both carrying: merged.
    assert by_action["carry_object"]["start_s"] == pytest.approx(120 / 23.97, abs=0.05)
    assert by_action["carry_object"]["end_s"] == pytest.approx(240 / 23.97, abs=0.05)
    assert by_action["run"]["start_s"] == pytest.approx(600 / 23.97, abs=0.05)
    assert "walk" in by_action["run"]["also"]
    assert len([e for e in events if e["action"] == "carry_object"]) == 1


def test_events_without_an_action_and_outside_the_clip_are_left_out():
    actions = {e["action"] for e in virat_events.events_from_annotations(EVENTS, 23.97, 40)}
    assert actions == {"carry_object", "run"}  # no gesturing (7); getting in at 375 s is outside


def test_at_30_fps_the_same_frames_are_earlier_seconds():
    events = virat_events.events_from_annotations(EVENTS, fps=29.97, length=40)
    run = next(e for e in events if e["action"] == "run")
    assert run["start_s"] == pytest.approx(20.0, abs=0.05)


def objects(rows):
    # objectID duration currentFrame x y w h type
    return "\n".join(" ".join(map(str, r)) for r in rows)


def test_walking_comes_from_moving_person_tracks():
    fps = 10
    walking = [(1, 100, f, 100 + 5 * f, 300, 20, 80, 1) for f in range(20, 80)]  # 50 px/s, 2-8 s
    standing = [(2, 100, f, 500, 300, 20, 80, 1) for f in range(100)]
    tiny = [(3, 100, f, 100 + 5 * f, 600, 5, 20, 1) for f in range(100)]  # too small
    events, entities = virat_events.movement_from_tracks(
        objects(walking + standing + tiny), fps, 10
    )
    walks = [e for e in events if e["action"] == "walk"]
    assert len(walks) == 1 and walks[0]["start_s"] == 2 and walks[0]["end_s"] == 7
    assert entities == ["person"]


def test_a_draft_names_its_video_relative_to_the_yaml(tmp_path):
    video = tmp_path / "virat" / "motion" / "clip.avi"
    video.parent.mkdir(parents=True)
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (32, 24))
    for _ in range(50):
        writer.write(np.zeros((24, 32, 3), np.uint8))
    writer.release()
    events = tmp_path / "events.txt"
    events.write_text(EVENTS)
    output = tmp_path / "acceptance" / "virat" / "clip.yaml"
    assert virat_events.main([str(video), str(events), "-o", str(output)]) == 0
    data = yaml.safe_load(output.read_text())
    assert data["video"] == "../../virat/motion/clip.avi"
    assert data["fps"] == 10 and data["length_s"] == 5
    assert data["queries"] == [] and data["events"] == []  # every sample event is after 5 s
