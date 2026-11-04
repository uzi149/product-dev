"""Marks job tests (step 5.7): the event fixtures of step 5.3, footage really in storage."""

import pytest

from shared.storage.keys import segment_key

# The event fixtures, and the API client for the route tests.
from tests.events.conftest import (  # noqa: F401
    add_event,
    add_segment,
    add_window,
    camera,
    client,
    fake_redis,
    fernet_key,
    queued,
    trigger,
)
from tests.marks.videos import write_clip


@pytest.fixture
def stored_clip(add_segment, storage, camera, tmp_path):  # noqa: F811 - fixtures
    """A segment whose video (`movers` over `seconds`) is really in storage."""
    keys = []

    def add(start_s: float, seconds: float, movers):
        path = write_clip(tmp_path / f"segment-{start_s:g}.avi", seconds, movers)
        segment = add_segment(start_s, seconds)
        key = segment_key(camera.id, segment.start_time, segment.id)
        segment.storage_uri = storage.upload_file(key, path, content_type="video/x-msvideo")
        keys.append(key)
        return segment

    yield add
    for key in keys:
        storage.delete(key)
