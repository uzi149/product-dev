"""Shared fixtures for the search tests: cameras, analysed windows with hand-made vectors, and
fake embedders whose query vectors are fixed."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pytest

from shared.config import Settings
from shared.database.models import AnalysisWindow, Camera, Keyframe, TextEmbedding, VideoSegment
from tests.fakes import fake_vector

START = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)
QUERY = "a person rings the doorbell"


def unit(vector: np.ndarray) -> np.ndarray:
    return (vector / np.linalg.norm(vector)).astype(np.float32)


def near(vector: np.ndarray, closeness: float, seed: str) -> np.ndarray:
    """A unit vector whose cosine similarity to `vector` grows with `closeness` (0-1)."""
    return unit(closeness * vector + (1 - closeness) * fake_vector(seed.encode()))


Q_TEXT = fake_vector(b"text query")
Q_PICTURE = fake_vector(b"picture query")


class QueryEmbedder:
    """Fake embedders whose query vectors are fixed, so stored vectors can be placed near them."""

    def __init__(self, name: str, vector: np.ndarray):
        self.name = name
        self.vector = vector
        self.queries: list[str] = []

    def embed_query(self, query: str) -> np.ndarray:
        self.queries.append(query)
        return self.vector

    embed_text = embed_query


TEXT = QueryEmbedder("fake-text-embedder", Q_TEXT)
# Hand-made vectors don't have the real models' similarity ranges: map the whole -1..1 range
# to 0..1 and keep every hit, so these tests check candidates and filters, not calibration.
OPEN = Settings(
    _env_file=None,
    query_text_low=-1,
    query_text_high=1,
    query_visual_low=-1,
    query_visual_high=1,
    query_min_score=0,
)
IMAGE = QueryEmbedder("fake-image-embedder", Q_PICTURE)


@pytest.fixture
def cameras(session):
    front = Camera(display_name="Front door", source_type="file")
    yard = Camera(display_name="Yard", source_type="file")
    session.add_all([front, yard])
    session.commit()
    return front, yard


@pytest.fixture
def add_window(session):
    segments = {}

    def add(
        camera,
        offset_s: float,
        text_vector=None,
        picture_vectors=(),
        status="done",
        description="something happens",
        actions=("walk",),
    ):
        segment = segments.get(camera.id)
        if segment is None:
            segment = VideoSegment(
                camera_id=camera.id,
                start_time=START,
                end_time=START + timedelta(hours=2),
                duration_ms=7_200_000,
                storage_uri=f"s3://sentinel/segments/{camera.id}.mp4",
                status="done",
            )
            session.add(segment)
            session.flush()
            segments[camera.id] = segment
        window = AnalysisWindow(
            id=uuid4(),
            segment_id=segment.id,
            camera_id=camera.id,
            start_time=START + timedelta(seconds=offset_s),
            end_time=START + timedelta(seconds=offset_s + 12),
            vlm_description=description,
            structured_observation_json={
                "actions": [{"type": t, "subject": "person_1"} for t in actions]
            },
            processing_status=status,
        )
        session.add(window)
        if text_vector is not None:
            session.add(
                TextEmbedding(
                    analysis_window_id=window.id, embedding=text_vector, source_text=description
                )
            )
        for n, vector in enumerate(picture_vectors):
            session.add(
                Keyframe(
                    analysis_window_id=window.id,
                    timestamp=window.start_time + timedelta(seconds=2 * n + 1),
                    storage_uri=f"s3://sentinel/keyframes/{window.id}/{n}.jpg",
                    visual_embedding=vector,
                )
            )
        session.commit()
        return window

    return add
