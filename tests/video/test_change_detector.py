from pathlib import Path

import cv2
import numpy as np
import pytest

from shared.config import Settings
from shared.video.change_detector import (
    ActivityEnded,
    ActivityStarted,
    ChangeDetector,
    State,
    build_mask,
    resolve_params,
)
from shared.video.frames import sample_frames
from tests.video.scenes import HEIGHT, WIDTH, Camera, background, with_object

STATIC_ROOM = Path(__file__).resolve().parents[2] / "test-data" / "camera-01" / "static-room.mp4"


@pytest.fixture
def settings():
    return Settings(_env_file=None)


@pytest.fixture
def detector(settings):
    return ChangeDetector(resolve_params({}, settings))


@pytest.fixture
def scene():
    return background()


@pytest.fixture
def camera():
    return Camera()


def run(detector, frames, start=0):
    """Feed one frame per second; returns the samples."""
    return [detector.update(frame, float(start + i)) for i, frame in enumerate(frames)]


def events(samples):
    return [s.event for s in samples if s.event is not None]


def walking(scene, camera, seconds, start_x=20, speed=15):
    return [camera(with_object(scene, start_x + speed * i)) for i in range(seconds)]


def test_static_noisy_scene_stays_idle(detector, scene, camera):
    samples = run(detector, [camera(scene) for _ in range(120)])
    assert events(samples) == []
    assert all(s.state == State.IDLE for s in samples)
    assert max(s.changed for s in samples) < 0.001


def test_moving_object_activates_after_two_samples_from_the_first(detector, scene, camera):
    frames = [camera(scene)] * 5 + walking(scene, camera, 10)
    samples = run(detector, frames)
    assert [s.state for s in samples[4:8]] == [State.IDLE, State.IDLE, State.ACTIVE, State.ACTIVE]
    # Activated on the second change sample (t=6), backdated to the first one (t=5).
    assert samples[6].event == ActivityStarted(start=5.0)
    assert samples[5].is_change and samples[6].is_change


def test_one_sample_flash_never_activates(detector, scene, camera):
    frames = [camera(scene)] * 5 + [camera(with_object(scene, 100))] + [camera(scene)] * 20
    samples = run(detector, frames)
    assert samples[5].is_change
    assert events(samples) == []


@pytest.mark.parametrize(
    "relight",
    [lambda s: s + 40, lambda s: s * 1.5, lambda s: s * 0.6],
    ids=["brighter by 40", "gain x1.5", "darker x0.6"],
)
def test_lighting_change_does_not_activate(detector, scene, camera, relight):
    lit = relight(scene)
    # Pixel difference alone would fire: most of the image changes by more than pixel_delta...
    assert np.mean(np.abs(lit - scene) > 25) > 0.3
    samples = run(detector, [camera(scene)] * 5 + [camera(lit) for _ in range(20)])
    # ...but the structure is unchanged, so it isn't a change.
    assert events(samples) == []


def test_motion_inside_a_mask_is_ignored(settings, scene, camera):
    # Ignore the left half; the object walks inside it (x from 20 to 125 of 320).
    masked = resolve_params({"masks": [[[0, 0], [0.5, 0], [0.5, 1], [0, 1]]]}, settings)
    frames = [camera(scene)] * 3 + walking(scene, camera, 8, start_x=20, speed=15)
    assert events(run(ChangeDetector(masked), frames)) == []

    unmasked = resolve_params({}, settings)
    assert any(
        isinstance(e, ActivityStarted) for e in events(run(ChangeDetector(unmasked), frames))
    )


def test_goes_idle_after_ten_still_seconds_ending_at_the_last_motion(detector, scene, camera):
    # Idle 0-4, the object walks 5-14, it is gone from 15 on (the vanishing counts as motion).
    frames = [camera(scene)] * 5 + walking(scene, camera, 10) + [camera(scene)] * 20
    samples = run(detector, frames)
    ended = [s for s in samples if isinstance(s.event, ActivityEnded)]
    assert len(ended) == 1
    event = ended[0].event
    assert (event.start, event.end) == (5.0, 15.0)
    assert ended[0].t == 25.0  # 10 s after the last motion
    assert 0 < event.score <= 1
    assert all(s.state == State.IDLE for s in samples[26:])


def test_object_left_behind_becomes_the_background(detector, scene, camera):
    # The object walks 5-9 and then stays where it stopped.
    still = with_object(scene, 20 + 15 * 4)
    frames = [camera(scene)] * 5 + walking(scene, camera, 5) + [camera(still) for _ in range(40)]
    samples = run(detector, frames)
    assert events(samples) == [
        ActivityStarted(5.0),
        ActivityEnded(5.0, 9.0, events(samples)[1].score),
    ]
    assert samples[19].event is not None  # ended 10 s after the last motion
    # The object is still there, but it is now part of the background.
    assert not any(s.is_change for s in samples[20:])


def test_finish_closes_an_open_interval(detector, scene, camera):
    samples = run(detector, [camera(scene)] * 3 + walking(scene, camera, 6))
    assert samples[-1].state == State.ACTIVE
    event = detector.finish()
    assert (event.start, event.end) == (3.0, 8.0)
    assert detector.state == State.IDLE
    assert detector.finish() is None


def test_activity_can_start_again_after_ending(detector, scene, camera):
    frames = (
        [camera(scene)] * 3
        + walking(scene, camera, 4)
        + [camera(scene)] * 15
        + walking(scene, camera, 4, start_x=200, speed=-15)
    )
    found = events(run(detector, frames))
    assert [type(e) for e in found] == [ActivityStarted, ActivityEnded, ActivityStarted]
    assert found[2].start == 22.0


def test_activation_samples_from_the_camera_config(settings, scene, camera):
    params = resolve_params({"activation_samples": 1}, settings)
    samples = run(ChangeDetector(params), [camera(scene)] * 3 + walking(scene, camera, 3))
    assert samples[3].event == ActivityStarted(3.0)


def test_colour_and_large_frames_are_handled(detector, scene, camera):
    def big_colour(gray):
        large = cv2.resize(gray, (WIDTH * 4, HEIGHT * 4), interpolation=cv2.INTER_NEAREST)
        return cv2.cvtColor(large, cv2.COLOR_GRAY2BGR)

    frames = [big_colour(camera(scene))] * 3 + [big_colour(f) for f in walking(scene, camera, 3)]
    assert run(detector, frames)[4].event == ActivityStarted(3.0)


def test_a_resolution_change_reseeds_the_background(detector, scene, camera):
    run(detector, [camera(scene)] * 3)
    smaller = cv2.resize(scene, (WIDTH // 2, HEIGHT // 2))
    samples = run(detector, [camera(smaller) for _ in range(5)], start=3)
    assert events(samples) == []


def test_masks_covering_everything_are_rejected(settings, scene, camera):
    params = resolve_params({"masks": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}, settings)
    with pytest.raises(ValueError, match="whole image"):
        ChangeDetector(params).update(camera(scene), 0.0)


@pytest.mark.parametrize(("width", "height"), [(320, 240), (1920, 1080), (64, 48)])
def test_build_mask_scales_normalised_polygons(width, height):
    include = build_mask([[(0, 0), (0.5, 0), (0.5, 1), (0, 1)]], width, height)
    assert include.shape == (height, width)
    assert include[:, : width // 2 - 1].sum() == 0
    assert include[:, width // 2 + 1 :].all()


def test_build_mask_without_polygons_includes_everything():
    assert build_mask([], 32, 24).all()


def test_resolve_params_uses_settings_defaults_and_camera_overrides(settings):
    defaults = resolve_params({}, settings)
    assert defaults.pixel_delta == settings.detect_pixel_delta
    assert defaults.changed_fraction == settings.detect_changed_fraction
    assert defaults.ssim_threshold == settings.detect_ssim_threshold
    assert defaults.activation_samples == 2
    assert defaults.stable_timeout_s == settings.stable_timeout_seconds == 10
    assert defaults.masks == ()

    custom = resolve_params({"changed_fraction": 0.05, "pixel_delta": 40}, settings)
    assert (custom.changed_fraction, custom.pixel_delta) == (0.05, 40)
    assert custom.ssim_threshold == settings.detect_ssim_threshold


@pytest.mark.skipif(
    not STATIC_ROOM.exists(), reason="test-data/camera-01/static-room.mp4 not recorded yet"
)
def test_real_static_room_has_no_activity(settings):
    detector = ChangeDetector(resolve_params({}, settings))
    for t, frame in sample_frames(STATIC_ROOM, fps=1):
        sample = detector.update(frame, t)
        assert not isinstance(sample.event, ActivityStarted), f"activity at {t:.0f} s"
