"""The size the local model saw a picture at (step 5.8), without loading a model."""

import numpy as np

from shared.model_adapters.local_transformers import seen_size


def test_the_seen_size_is_the_patch_grid_times_the_patch_size():
    # One picture, 26 x 46 patches of 14 px: the model saw 644 x 364.
    assert seen_size(np.array([[1, 26, 46]]), 14) == (644, 364)


def test_no_seen_size_without_exactly_one_picture_or_a_patch_size():
    assert seen_size(np.array([[1, 26, 46], [1, 26, 46]]), 14) is None
    assert seen_size(np.array([[1, 26, 46]]), None) is None
    assert seen_size(None, 14) is None
