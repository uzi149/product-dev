"""Query tests: analysed windows with hand-made vectors (from the search tests), keyframe pictures
in MinIO for verification, and the tools a query run needs."""

import cv2
import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.query_engine.run import Tools
from shared.database.models import Keyframe
from tests.search.conftest import (  # noqa: F401 - fixtures
    IMAGE,
    OPEN,
    TEXT,
    add_window,
    cameras,
)

# An API backend, so verification runs (the local model skips it, ADR 0029).
SETTINGS = OPEN.model_copy(update={"vlm_backend": "api"})


@pytest.fixture
def pictures(session, storage):
    """Puts a small JPEG in MinIO for every keyframe row; call it after adding the windows."""
    keys = []

    def store():
        jpeg = cv2.imencode(".jpg", np.full((48, 64, 3), 128, np.uint8))[1].tobytes()
        for uri in session.scalars(select(Keyframe.storage_uri)):
            key = storage.key_from_uri(uri)
            storage.upload_bytes(key, jpeg, "image/jpeg")
            keys.append(key)

    yield store
    for key in keys:
        storage.delete(key)


@pytest.fixture
def make_tools(engine, storage):
    def make(vlm, settings=SETTINGS, published=None):
        return Tools(
            session_factory=lambda: Session(engine),
            storage=storage,
            settings=settings,
            publish=(published.append if published is not None else lambda _: None),
            vlm=lambda: vlm,
            text_embedder=TEXT,
            image_embedder=IMAGE,
        )

    return make
