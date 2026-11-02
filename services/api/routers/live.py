"""Live notifications (scope §13 `WS /live/events`, step 5.2).

Every event notice the workers publish once an event's evidence is stored is forwarded to each
connected browser as JSON. Nothing old is sent on connect: the Events page lists those.
"""

import asyncio
import contextlib

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from services.api.deps import get_event_notices
from services.triggers.notices import EventNotices

router = APIRouter(prefix="/live", tags=["live"])


@router.websocket("/events")
async def live_events(
    websocket: WebSocket, notices: EventNotices = Depends(get_event_notices)
) -> None:
    await websocket.accept()

    async def forward() -> None:
        async for notice in notices.listen():
            await websocket.send_json(notice)

    async def until_closed() -> None:
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass

    tasks = [asyncio.create_task(forward()), asyncio.create_task(until_closed())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task
