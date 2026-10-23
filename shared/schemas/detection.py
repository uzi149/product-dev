"""Per-camera change detection settings, stored in `cameras.detection_config` (ADR 0010).

Every field is optional: a missing field falls back to the global default in `Settings`.
"""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# A point in normalised image coordinates: (0, 0) is top-left, (1, 1) is bottom-right.
Point = tuple[float, float]


class DetectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Grey-level difference (0-255) for a pixel to count as changed.
    pixel_delta: int | None = Field(default=None, ge=1, le=255)
    # Share of unmasked pixels that must change for the sample to count as a change.
    changed_fraction: float | None = Field(default=None, gt=0, le=1)
    # A changed pixel only counts when its local structural similarity is below this.
    ssim_threshold: float | None = Field(default=None, gt=0, le=1)
    # Consecutive change samples needed to go active.
    activation_samples: int | None = Field(default=None, ge=1, le=30)
    # How fast the background follows the scene while idle.
    background_alpha: float | None = Field(default=None, gt=0, le=1)
    # Polygons to ignore (trees, screens). At least 3 points each.
    masks: list[list[Point]] = Field(default_factory=list)

    @field_validator("masks")
    @classmethod
    def _check_masks(cls, masks: list[list[Point]]) -> list[list[Point]]:
        for polygon in masks:
            if len(polygon) < 3:
                raise ValueError("a mask polygon needs at least 3 points")
            for x, y in polygon:
                if not (0 <= x <= 1 and 0 <= y <= 1):
                    raise ValueError("mask points are normalised: x and y must be between 0 and 1")
        return masks


def normalise_detection_config(config: dict) -> dict:
    """Validate a raw `detection_config` and return it without the unset fields.

    Raises ValueError with a readable message, so it can be used inside other validators.
    """
    try:
        parsed = DetectionConfig.model_validate(config)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in e['loc']) or 'detection_config'}: {e['msg']}"
            for e in error.errors()
        )
        raise ValueError(f"invalid detection_config: {problems}") from None
    return parsed.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
