"""The real embedding models (marked `models`): only with RUN_MODEL_TESTS=1, never in CI.

They download about 2 GB on first use (`python -m shared.model_adapters.download_models`) and
need about 2 GB of free memory. Run with `-s` to see the timings.
"""

import time

import numpy as np
import pytest

from shared.config import Settings
from shared.database.models import EMBEDDING_DIM
from shared.model_adapters import build_image_embedder, build_text_embedder
from shared.model_adapters.check_embeddings import DESCRIPTIONS, QUERY, sample_pictures

pytestmark = pytest.mark.models


@pytest.fixture(scope="module")
def text():
    return build_text_embedder(Settings())


@pytest.fixture(scope="module")
def image():
    return build_image_embedder(Settings())


def assert_unit_vectors(vectors: np.ndarray, count: int):
    assert vectors.shape == (count, EMBEDDING_DIM) and vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-3)


def test_descriptions_give_unit_vectors_the_same_alone_or_in_a_batch(text):
    vectors = text.embed_documents(DESCRIPTIONS)
    assert_unit_vectors(vectors, len(DESCRIPTIONS))
    alone = text.embed_documents([DESCRIPTIONS[2]])[0]
    assert np.allclose(alone, vectors[2], atol=1e-4)


def test_similar_sentences_are_closer(text):
    doorbell, front_door, car = text.embed_documents(
        [
            "a person rings the doorbell",
            "someone presses the bell at the front door",
            "a red car parks in the street",
        ]
    )
    assert doorbell @ front_door > doorbell @ car + 0.1


def test_a_query_gets_the_instruction_and_finds_the_matching_description(text):
    query = text.embed_query(QUERY)
    assert_unit_vectors(query[None], 1)
    assert not np.allclose(query, text.embed_documents([QUERY])[0], atol=1e-3)

    started = time.monotonic()
    scores = text.embed_documents(DESCRIPTIONS) @ query
    print(f"\n{(time.monotonic() - started) * 1000 / len(DESCRIPTIONS):.0f} ms per description")
    assert int(np.argmax(scores)) == 0, scores


def test_pictures_give_unit_vectors_and_each_caption_finds_its_picture(image):
    pictures = sample_pictures()
    image.embed_text("load the model before timing")
    started = time.monotonic()
    vectors = image.embed_images(list(pictures.values()))
    print(f"\n{(time.monotonic() - started) * 1000 / len(pictures):.0f} ms per picture")
    assert_unit_vectors(vectors, len(pictures))
    for index, caption in enumerate(pictures):
        query = image.embed_text(caption)
        assert_unit_vectors(query[None], 1)
        assert int(np.argmax(vectors @ query)) == index, caption
