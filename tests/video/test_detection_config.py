import pytest

from shared.schemas.detection import normalise_detection_config


def test_empty_config_is_valid():
    assert normalise_detection_config({}) == {}


def test_overrides_are_kept_and_points_become_lists():
    config = {
        "changed_fraction": 0.02,
        "ssim_threshold": 0.6,
        "masks": [[[0, 0], [0.5, 0], [0.5, 1]]],
    }
    assert normalise_detection_config(config) == {
        "changed_fraction": 0.02,
        "ssim_threshold": 0.6,
        "masks": [[[0.0, 0.0], [0.5, 0.0], [0.5, 1.0]]],
    }


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"unknown": 1}, "Extra inputs"),
        ({"changed_fraction": 0}, "greater than 0"),
        ({"changed_fraction": 1.5}, "less than or equal to 1"),
        ({"pixel_delta": 300}, "less than or equal to 255"),
        ({"activation_samples": 0}, "greater than or equal to 1"),
        ({"masks": [[[0, 0], [1, 1]]]}, "at least 3 points"),
        ({"masks": [[[0, 0], [1, 0], [1.2, 1]]]}, "normalised"),
        ({"masks": [[[0, 0, 0], [1, 0], [1, 1]]]}, "masks"),
    ],
)
def test_invalid_configs_are_rejected(config, message):
    with pytest.raises(ValueError, match="invalid detection_config") as error:
        normalise_detection_config(config)
    assert message in str(error.value)
