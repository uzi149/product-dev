"""Embedding interfaces without models: vector checks, settings, and the fakes."""

import numpy as np
import pytest

from shared.config import Settings
from shared.database.models import EMBEDDING_DIM
from shared.model_adapters import embeddings
from shared.model_adapters.embeddings import (
    EmbeddingError,
    build_image_embedder,
    build_text_embedder,
    checked,
    embedding_threads,
)
from shared.model_adapters.local_embeddings import BGE_QUERY_INSTRUCTION, LocalTextEmbedder
from tests.fakes import FakeImageEmbedder, FakeTextEmbedder


def unit_vectors(count: int, dim: int = EMBEDDING_DIM) -> np.ndarray:
    vectors = np.random.default_rng(0).standard_normal((count, dim))
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def test_good_vectors_pass_as_float32():
    vectors = checked(unit_vectors(3), 3, "m")
    assert vectors.dtype == np.float32 and vectors.shape == (3, EMBEDDING_DIM)


def test_no_vectors_pass():
    assert checked(np.empty((0, EMBEDDING_DIM)), 0, "m").shape == (0, EMBEDDING_DIM)


@pytest.mark.parametrize(
    ("vectors", "count", "message"),
    [
        (unit_vectors(2, 512), 2, "shape"),
        (unit_vectors(2), 3, "shape"),
        (unit_vectors(1)[0], 1, "shape"),
        (unit_vectors(2) * 2, 2, "not normalised"),
    ],
)
def test_vectors_that_cant_be_indexed_are_refused(vectors, count, message):
    with pytest.raises(EmbeddingError, match=message):
        checked(vectors, count, "m")


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_nan_and_infinite_values_are_refused(bad):
    vectors = unit_vectors(2)
    vectors[1, 5] = bad
    with pytest.raises(EmbeddingError, match="NaN"):
        checked(vectors, 2, "m")


def test_threads_default_to_half_the_cores(monkeypatch):
    monkeypatch.setattr(embeddings.os, "cpu_count", lambda: 8)
    assert embedding_threads(Settings(embedding_threads=0)) == 4
    assert embedding_threads(Settings(embedding_threads=3)) == 3
    monkeypatch.setattr(embeddings.os, "cpu_count", lambda: None)
    assert embedding_threads(Settings(embedding_threads=0)) == 1


def test_the_embedders_come_from_the_settings_without_loading_a_model():
    settings = Settings(text_embedding_model="BAAI/bge-base-en-v1.5", image_embedding_model="x/y")
    text = build_text_embedder(settings)
    assert text.name == "BAAI/bge-base-en-v1.5"
    assert text.query_instruction == BGE_QUERY_INSTRUCTION
    assert build_image_embedder(settings).name == "x/y"


def test_other_text_models_get_no_query_instruction():
    assert LocalTextEmbedder("some/other-model").query_instruction == ""


def test_empty_input_needs_no_model():
    assert LocalTextEmbedder("not/a-model").embed_documents([]).shape == (0, EMBEDDING_DIM)


def test_the_fake_embedders_are_deterministic_and_pass_the_checks():
    text = FakeTextEmbedder()
    documents = text.embed_documents(["a", "b", "a"])
    checked(documents, 3, text.name)
    assert np.array_equal(documents[0], documents[2])
    assert not np.array_equal(documents[0], documents[1])
    assert not np.array_equal(text.embed_query("a"), documents[0])

    image = FakeImageEmbedder()
    frames = [np.zeros((4, 4, 3), np.uint8), np.ones((4, 4, 3), np.uint8)]
    pictures = image.embed_images(frames + frames[:1])
    checked(pictures, 3, image.name)
    assert np.array_equal(pictures[0], pictures[2])
    assert image.embed_text("a").shape == (EMBEDDING_DIM,)
