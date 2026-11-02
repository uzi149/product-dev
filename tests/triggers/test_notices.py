"""Event notices through the real Redis (skipped when it isn't running), step 5.2."""

import asyncio
import json

import pytest
import redis

from services.triggers.notices import EventNotices, publish
from shared.camera_status import get_redis


@pytest.fixture
def client():
    client = get_redis()
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip("Redis is not reachable (run `docker compose up -d`)")
    return client


def test_a_published_notice_reaches_a_listener(client):
    notice = {"event_id": "e-1", "trigger_name": "Object left behind"}

    async def scenario():
        listening = EventNotices().listen()
        first = asyncio.ensure_future(anext(listening))
        await asyncio.sleep(0.3)  # subscribed
        publish(client, notice)
        received = await asyncio.wait_for(first, 5)
        await listening.aclose()
        return received

    assert asyncio.run(scenario()) == notice


def test_publishing_without_redis_does_not_raise():
    class Down:
        def publish(self, *args):
            raise redis.ConnectionError("down (test)")

    publish(Down(), {"event_id": "e-1"})


def test_dates_are_sent_as_text():
    sent = []

    class Recording:
        def publish(self, channel, message):
            sent.append((channel, json.loads(message)))

    from datetime import UTC, datetime

    publish(Recording(), {"event_id": "e-1", "at": datetime(2026, 3, 7, tzinfo=UTC)})
    assert sent == [("events", {"event_id": "e-1", "at": "2026-03-07 00:00:00+00:00"})]
