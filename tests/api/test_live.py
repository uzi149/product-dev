"""`WS /live/events` forwards every event notice (step 5.2)."""

import asyncio

from services.api.deps import get_event_notices


class FakeNotices:
    """Two notices, then silence (as when nothing more happens)."""

    async def listen(self):
        yield {"event_id": "e-1", "trigger_name": "Object left behind", "camera_name": "CAM-01"}
        yield {"event_id": "e-2", "trigger_name": "Someone falls", "camera_name": "CAM-02"}
        while True:
            await asyncio.sleep(3600)


def test_notices_are_forwarded_to_the_browser(client):
    client.app.dependency_overrides[get_event_notices] = FakeNotices
    with client.websocket_connect("/live/events") as ws:
        assert ws.receive_json()["event_id"] == "e-1"
        assert ws.receive_json() == {
            "event_id": "e-2",
            "trigger_name": "Someone falls",
            "camera_name": "CAM-02",
        }
