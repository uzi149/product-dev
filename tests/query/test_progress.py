"""Progress notices through the real Redis (skipped when it isn't running)."""

import asyncio
from uuid import uuid4

import pytest
import redis

from services.query_engine.progress import QueryEvents, publish
from shared.camera_status import get_redis


@pytest.fixture
def client():
    client = get_redis()
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip("Redis is not reachable (run `docker compose up -d`)")
    return client


def test_a_published_notice_wakes_the_subscriber(client):
    query_id = uuid4()

    async def scenario():
        async with QueryEvents().subscribe(query_id) as subscription:
            assert await subscription.wait(0.2) is False  # nothing yet
            publish(client, query_id)
            return await subscription.wait(2)

    assert asyncio.run(scenario()) is True


def test_publishing_without_redis_does_not_raise():
    class Down:
        def publish(self, *args):
            raise redis.ConnectionError("down (test)")

    publish(Down(), uuid4())
