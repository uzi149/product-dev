"""The real VLM describes real footage (marked `models`: only with RUN_MODEL_TESTS=1).

Uses the configured VLM (OpenRouter with a key in .env, else the local model: minutes on a CPU)
on the first window of a local VIRAT clip where people walk across a plaza and up the stairs.
Run with `-s` to see the observation.
"""

from pathlib import Path

import pytest

from services.analysis.observations import describe_window, index_text
from services.analysis.windows import keyframe_times, plan_windows
from shared.config import Settings
from shared.model_adapters import build_vlm
from shared.video.keyframes import frames_at

pytestmark = pytest.mark.models

CLIP = Path("test-data/virat/motion/VIRAT_S_010004_00_000064_000100.mp4")
MOVEMENT = {"walk", "run", "enter", "leave", "carry_object"}


def test_people_walking_are_described_as_people_moving():
    if not CLIP.exists():
        pytest.skip(f"{CLIP} is not downloaded (test-data/virat is local only)")
    settings = Settings()
    window = plan_windows(36, settings.analysis_window_seconds, settings.window_overlap_seconds)[0]
    times = keyframe_times(window, settings.keyframes_per_window)
    frames = frames_at(CLIP, times)
    described = describe_window(
        build_vlm(settings), [f for _, f in frames], "Plaza", [t for t, _ in frames], window.seconds
    )
    observation = described.observation
    print(f"\n{described.reply.model}, attempt {described.attempts}:\n{index_text(observation)}")
    print(observation.model_dump_json(indent=1))

    kinds = {e.type.lower() for e in observation.entities} | set(observation.visible_objects)
    assert kinds & {"person", "people", "pedestrian", "man", "woman"}, kinds
    assert {a.type for a in observation.actions} & MOVEMENT, observation.actions
