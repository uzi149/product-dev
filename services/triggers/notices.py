"""Live event notices through Redis pub/sub (step 5.2, scope §13 `WS /live/events`).

The worker publishes a notice on channel `events` once an event's evidence is stored; the API's
WebSocket forwards every notice to the connected browsers. Without Redis the event is still
stored and listed; only the live toast is lost.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import redis
import redis.asyncio as aioredis

from shared.config import get_settings

log = logging.getLogger(__name__)

CHANNEL = "events"
# Without Redis, the WebSocket tries to subscribe again this often.
RESUBSCRIBE_SECONDS = 5.0


def publish(client: redis.Redis, notice: dict[str, Any]) -> None:
    """Never raises: a lost notice only loses the toast."""
    try:
        client.publish(CHANNEL, json.dumps(notice, default=str))
    except redis.RedisError as error:
        log.warning("could not publish the notice of event %s: %s", notice.get("event_id"), error)


class EventNotices:
    """Every notice published from now on, for as long as the caller listens."""

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            client = aioredis.from_url(get_settings().redis_url)
            pubsub = client.pubsub()
            try:
                await pubsub.subscribe(CHANNEL)
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30)
                    if message is None:
                        continue
                    try:
                        yield json.loads(message["data"])
                    except (TypeError, ValueError):
                        log.warning("ignored an unreadable event notice: %r", message["data"])
            except (redis.RedisError, OSError) as error:
                log.debug("no event notices (%s); trying again", error)
                await asyncio.sleep(RESUBSCRIBE_SECONDS)
            finally:
                try:
                    await pubsub.aclose()
                    await client.aclose()
                except (redis.RedisError, OSError):
                    pass
