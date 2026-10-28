"""The embedding check command (`python -m shared.model_adapters.check_embeddings`), with fakes."""

import numpy as np

from shared.model_adapters import check_embeddings
from tests.fakes import FakeImageEmbedder, FakeTextEmbedder


class RankingTextEmbedder(FakeTextEmbedder):
    """The query lands on the description at `match`."""

    def __init__(self, match: int):
        super().__init__()
        self.match = match

    def embed_query(self, text):
        return self.embed_documents([check_embeddings.DESCRIPTIONS[self.match]])[0]


class RankingImageEmbedder(FakeImageEmbedder):
    """Each caption lands on its own generated picture."""

    def embed_text(self, text):
        return self.embed_images([check_embeddings.sample_pictures()[text]])[0]


def run(monkeypatch, capsys, text, image):
    monkeypatch.setattr(check_embeddings, "build_text_embedder", lambda settings: text)
    monkeypatch.setattr(check_embeddings, "build_image_embedder", lambda settings: image)
    code = check_embeddings.main([])
    return code, capsys.readouterr().out


def test_good_rankings_pass_and_the_sizes_and_timings_are_printed(monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, RankingTextEmbedder(0), RankingImageEmbedder())
    assert code == 0
    assert "fake-text-embedder: 768 values" in out and "fake-image-embedder: 768 values" in out
    assert "ms per description" in out and "per window of 6" in out
    assert "WRONG" not in out


def test_a_wrong_text_ranking_fails(monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, RankingTextEmbedder(1), RankingImageEmbedder())
    assert code == 1 and "the doorbell description should be first" in out


def test_a_wrong_picture_ranking_fails(monkeypatch, capsys):
    class Opposite(RankingImageEmbedder):
        def embed_text(self, text):
            return -super().embed_text(text)

    code, out = run(monkeypatch, capsys, RankingTextEmbedder(0), Opposite())
    assert code == 1 and "each caption should pick its own picture" in out


def test_the_sample_pictures_differ():
    pictures = list(check_embeddings.sample_pictures().values())
    assert len(pictures) == 3
    assert not np.array_equal(pictures[0], pictures[1])
