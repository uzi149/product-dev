"""Query progress notices through Redis pub/sub (step 4.4).

The task stores every change in PostgreSQL and then publishes a short notice on `query:{id}`;
a WebSocket that hears it reads the new state from the database. A lost notice only delays an
update: the WebSocket also re-reads the state every few seconds.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import redis
import redis.asyncio as aioredis

from shared.config import get_settings

log = logging.getLogger(__name__)


def channel(query_id: UUID) -> str:
    return f"query:{query_id}"


def publish(client: redis.Redis, query_id: UUID) -> None:
    """Tell listeners the query changed. Never raises: the WebSocket re-reads anyway."""
    try:
        client.publish(channel(query_id), "changed")
    except redis.RedisError as error:
        log.debug("could not publish progress of query %s: %s", query_id, error)


class Subscription:
    def __init__(self, pubsub: aioredis.client.PubSub | None):
        self._pubsub = pubsub

    async def wait(self, timeout: float) -> bool:
        """True when a notice arrived within `timeout` seconds."""
        if self._pubsub is None:
            await asyncio.sleep(timeout)
            return False
        try:
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=timeout
            )
        except (redis.RedisError, OSError):
            await asyncio.sleep(timeout)
            return False
        return message is not None


class QueryEvents:
    """Subscribes to a query's notices; without Redis, `wait` just sleeps (the WebSocket then
    only re-reads on its timer)."""

    def notify(self, query_id: UUID) -> None:
        """Wake the query's listeners from the API (a deleted query). Never raises."""
        client = redis.Redis.from_url(
            get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2
        )
        publish(client, query_id)
        client.close()

    @asynccontextmanager
    async def subscribe(self, query_id: UUID) -> AsyncIterator[Subscription]:
        client = aioredis.from_url(get_settings().redis_url)
        pubsub = client.pubsub()
        try:
            try:
                await pubsub.subscribe(channel(query_id))
            except (redis.RedisError, OSError) as error:
                log.debug("no progress notices for query %s: %s", query_id, error)
                yield Subscription(None)
                return
            yield Subscription(pubsub)
        finally:
            try:
                await pubsub.aclose()
                await client.aclose()
            except (redis.RedisError, OSError):
                pass
