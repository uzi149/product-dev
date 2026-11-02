"""The main configuration values, read-only (step 5.5): for the Settings page.

Only safe values: no keys, passwords or camera addresses, and the VLM server only by its host.
Configuration itself stays in `.env`.
"""

from urllib.parse import urlsplit

from fastapi import APIRouter
from pydantic import BaseModel

from shared.config import get_settings
from shared.model_adapters import resolve_vlm_backend

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingItem(BaseModel):
    label: str
    value: str
    env: str  # the .env name that changes it


class SettingSection(BaseModel):
    title: str
    items: list[SettingItem]


def _on(value: bool) -> str:
    return "on" if value else "off"


@router.get("", response_model=list[SettingSection])
def read_settings() -> list[SettingSection]:
    s = get_settings()
    backend = resolve_vlm_backend(s)
    vlm = (
        [
            (
                "Backend",
                f"API ({urlsplit(s.vlm_base_url).hostname or 'configured server'})",
                "VLM_BACKEND",
            ),
            ("Model", s.vlm_model, "VLM_MODEL"),
        ]
        if backend == "api"
        else [
            ("Backend", "local model in the worker", "VLM_BACKEND"),
            ("Model", s.vlm_local_model, "VLM_LOCAL_MODEL"),
        ]
    )
    sections = {
        "Vision-language model": [
            *vlm,
            ("Image size sent", f"{s.vlm_image_max_side} px (longest side)", "VLM_IMAGE_MAX_SIDE"),
        ],
        "Embeddings (local)": [
            ("Text", s.text_embedding_model, "TEXT_EMBEDDING_MODEL"),
            ("Pictures", s.image_embedding_model, "IMAGE_EMBEDDING_MODEL"),
        ],
        "Analysis": [
            ("Automatic analysis", _on(s.analysis_enabled), "ANALYSIS_ENABLED"),
            ("Window", f"{s.analysis_window_seconds} s", "ANALYSIS_WINDOW_SECONDS"),
            ("Window overlap", f"{s.window_overlap_seconds} s", "WINDOW_OVERLAP_SECONDS"),
            ("Keyframes per window", str(s.keyframes_per_window), "KEYFRAMES_PER_WINDOW"),
            ("Frames checked for change", f"{s.analysis_fps} per second", "ANALYSIS_FPS"),
        ],
        "Change detection (camera defaults)": [
            ("Changed share of the picture", f"{s.detect_changed_fraction:.2%}", "DETECT_CHANGED_FRACTION"),
            ("Pixel change", str(s.detect_pixel_delta), "DETECT_PIXEL_DELTA"),
            ("Similarity check (SSIM)", f"{s.detect_ssim_threshold:g}", "DETECT_SSIM_THRESHOLD"),
            ("Footage kept before / after activity",
             f"{s.pre_roll_seconds} s / {s.post_roll_seconds} s", "PRE_ROLL_SECONDS"),
            ("Longest live segment", f"{s.max_segment_seconds} s", "MAX_SEGMENT_SECONDS"),
        ],
        "Search": [
            ("Minimum score", f"{s.query_min_score:g}", "QUERY_MIN_SCORE"),
            ("Results verified by the VLM", str(s.query_verify_top), "QUERY_VERIFY_TOP"),
            ("Verified at confidence", f"{s.query_verify_min_confidence:.0%}", "QUERY_VERIFY_MIN_CONFIDENCE"),
            ("Separated clips grouped within", f"{s.query_group_gap_minutes:g} min", "QUERY_GROUP_GAP_MINUTES"),
        ],
        "Triggers": [
            ("Live evaluation", _on(s.triggers_enabled), "TRIGGERS_ENABLED"),
            ("Similarity rules match at", f"{s.trigger_semantic_min_score:g}", "TRIGGER_SEMANTIC_MIN_SCORE"),
            ("Matches verified per test", str(s.trigger_test_verify_max), "TRIGGER_TEST_VERIFY_MAX"),
        ],
        "Retention": [
            ("Footage kept", "14 days by default, set per camera; clean-up comes with Phase 6", "—"),
        ],
    }  # fmt: skip
    return [
        SettingSection(
            title=title,
            items=[SettingItem(label=label, value=value, env=env) for label, value, env in items],
        )
        for title, items in sections.items()
    ]
