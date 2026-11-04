"""Marks for a clip from generated footage and a fake VLM (step 5.7)."""

import itertools
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from services.marks.build import MarksError, build_marks, keyframe_interval, keyframe_times
from shared.config import get_settings
from shared.model_adapters.vlm import VLMUnavailable
from shared.schemas.query import GapPart, PlaybackManifest, VideoPart
from shared.video.draw import boxes_at
from tests.fakes import FakeVLM
from tests.marks.videos import Mover, grounder, write_clip

T0 = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)


def settings(**values):
    return get_settings().model_copy(update={"marks_concurrency": 2, **values})


def part(segment_id, start_ms, end_ms, offset_s=0.0):
    begin = T0 + timedelta(seconds=offset_s)
    return VideoPart(
        camera_id=uuid4(),
        segment_id=segment_id,
        source_start_ms=start_ms,
        source_end_ms=end_ms,
        start_time=begin,
        end_time=begin + timedelta(milliseconds=end_ms - start_ms),
    )


def manifest(*parts):
    videos = [p for p in parts if isinstance(p, VideoPart)]
    return PlaybackManifest(
        result_id=uuid4(),
        camera_id=uuid4(),
        start_time=videos[0].start_time,
        end_time=videos[-1].end_time,
        parts=list(parts),
    )


def test_keyframes_are_spread_and_capped():
    a, b = part(uuid4(), 0, 10_000), part(uuid4(), 2_000, 4_000)
    assert keyframe_interval([a, b], 1.0, 60) == 1.0
    times = keyframe_times([a, b], 1.0)
    assert len(times[0]) == 10 and times[0][0] == pytest.approx(0.5)
    assert times[1] == pytest.approx([2.5, 3.5])
    # 12 s with at most 4 keyframes: 3 s apart.
    assert keyframe_interval([a, b], 1.0, 4) == 3.0
    assert sum(len(t) for t in keyframe_times([a, b], 3.0)) == 4


def test_a_walker_is_one_track_that_follows_them(tmp_path):
    walker = Mover("person", seed=1, x0=10, speed=40)
    segment = uuid4()
    path = write_clip(tmp_path / "clip.avi", 6, [walker])
    vlm = FakeVLM([grounder([walker])])

    built = build_marks(manifest(part(segment, 0, 6_000)), {segment: path}, vlm, ["person"],
                        "a person walks by", settings())  # fmt: skip

    assert built.keyframes == 6 and built.grounded == 6
    assert built.pick_status == "none"  # one track: nothing to pick
    assert len(built.tracks) == 1
    track = built.tracks[0]
    assert (track["label"], track["kind"], track["picked"]) == ("person", "person", False)
    points = track["runs"][0]["points"]
    assert track["runs"][0]["segment_id"] == str(segment)
    gaps = [b[0] - a[0] for a, b in itertools.pairwise(points)]
    assert max(gaps) <= 300  # about 5 points per second
    for ms in (1_300, 2_700, 4_100):
        shown = boxes_at(built.tracks, built.pick_status, str(segment), ms)
        assert len(shown) == 1
        assert shown[0].box == pytest.approx(walker.box(ms / 1000), abs=0.03)
    assert all(c["image_max_side"] == 1024 for c in vlm.calls)


def test_the_pick_marks_the_one_the_question_is_about(tmp_path):
    person = Mover("person", seed=1, x0=10, speed=30, y=20)
    bag = Mover("bag", seed=5, x0=200, speed=0, y=150)
    segment = uuid4()
    path = write_clip(tmp_path / "clip.avi", 4, [person, bag])
    # Candidates are numbered in track order: the person was found first.
    vlm = FakeVLM([grounder([person, bag], pick="[2]")])

    built = build_marks(manifest(part(segment, 0, 4_000)), {segment: path}, vlm,
                        ["person", "bag"], "a bag is left", settings())  # fmt: skip

    assert built.pick_status == "done"
    assert {(t["label"], t["picked"]) for t in built.tracks} == {("person", False), ("bag", True)}
    assert "a bag is left" in vlm.calls[-1]["prompt"]


def test_an_unusable_pick_shows_every_track_the_same(tmp_path):
    person = Mover("person", seed=1, x0=10, speed=30, y=20)
    bag = Mover("bag", seed=5, x0=200, speed=0, y=150)
    segment = uuid4()
    path = write_clip(tmp_path / "clip.avi", 3, [person, bag])
    vlm = FakeVLM([grounder([person, bag], pick="no idea")])
    built = build_marks(manifest(part(segment, 0, 3_000)), {segment: path}, vlm, [], "q",
                        settings())  # fmt: skip
    assert built.pick_status == "failed"
    assert all(s.strong for s in boxes_at(built.tracks, built.pick_status, str(segment), 1_500))


def test_a_gap_ends_every_track(tmp_path):
    walker = Mover("person", seed=1, x0=10, speed=20)
    first, second = uuid4(), uuid4()
    one = write_clip(tmp_path / "one.avi", 4, [walker])
    two = write_clip(tmp_path / "two.avi", 4, [walker])
    clip = manifest(part(first, 0, 4_000), GapPart(duration_ms=60_000), part(second, 0, 4_000, 64))

    built = build_marks(clip, {first: one, second: two}, FakeVLM([grounder([walker])]),
                        ["person"], "q", settings())  # fmt: skip

    assert len(built.tracks) == 2
    assert [r["segment_id"] for t in built.tracks for r in t["runs"]] == [str(first), str(second)]


def test_some_failed_keyframes_are_bridged(tmp_path):
    walker = Mover("person", seed=1, x0=10, speed=20)
    segment = uuid4()
    path = write_clip(tmp_path / "clip.avi", 5, [walker])
    reply = grounder([walker])
    calls = {"n": 0}

    class Flaky(FakeVLM):
        def complete(self, frames, prompt, **kw):
            calls["n"] += 1
            if calls["n"] == 3:
                raise VLMUnavailable("timeout")
            return super().complete(frames, prompt, **kw)

    built = build_marks(manifest(part(segment, 0, 5_000)), {segment: path}, Flaky([reply]),
                        ["person"], "q", settings(marks_concurrency=1))  # fmt: skip
    assert (built.keyframes, built.grounded) == (5, 4)
    assert len(built.tracks) == 1


def test_every_keyframe_failing_fails_the_clip(tmp_path):
    segment = uuid4()
    path = write_clip(tmp_path / "clip.avi", 2, [])
    with pytest.raises(VLMUnavailable):
        build_marks(manifest(part(segment, 0, 2_000)), {segment: path},
                    FakeVLM([VLMUnavailable("down")]), ["person"], "q", settings())  # fmt: skip


def test_a_clip_without_footage_fails():
    clip = PlaybackManifest(result_id=uuid4(), camera_id=uuid4(), start_time=T0, end_time=T0,
                            parts=[])  # fmt: skip
    with pytest.raises(MarksError):
        build_marks(clip, {}, FakeVLM(["[]"]), [], "q", settings())


def test_one_object_named_differently_stays_one_track(tmp_path):
    """The model calls the same bag "bag", then "backpack": tracks match on the class, and the
    track shows the word used most."""
    bag = Mover("bag", seed=5, x0=150, speed=10, y=150)
    segment = uuid4()
    path = write_clip(tmp_path / "clip.avi", 5, [bag])
    reply = grounder([bag])
    names = iter(["bag", "backpack", "bag", "bag", "backpack"])

    def renamed(frames, prompt):
        return reply(frames, prompt).replace('"bag"', f'"{next(names)}"')

    built = build_marks(manifest(part(segment, 0, 5_000)), {segment: path},
                        FakeVLM([renamed]), ["bag"], "q", settings(marks_concurrency=1))  # fmt: skip

    assert [(t["label"], t["kind"]) for t in built.tracks] == [("bag", "portable_object")]
