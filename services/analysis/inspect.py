"""Show what the VLM wrote for a segment, without SQL (Phase 3 has no UI).

    python -m services.analysis.inspect SEGMENT_ID
    python -m services.analysis.inspect --camera "Front door" --latest 3
    python -m services.analysis.inspect SEGMENT_ID --json

Per window: times, status, description, actions, visible objects, uncertainties, confidence,
model, any error, and links to its keyframes that open in a browser for an hour.
"""

import argparse
import json
import sys
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.database.models import AnalysisWindow, Camera, Keyframe, VideoSegment
from shared.database.session import get_sessionmaker
from shared.storage import StorageClient, get_storage

LINK_LIFETIME = timedelta(hours=1)


def _time(value) -> str:
    return value.isoformat(timespec="seconds")


def describe_segments(
    session: Session, storage: StorageClient, segment_ids: list[UUID]
) -> list[dict[str, Any]]:
    """The segments and their windows as plain data (also the --json output)."""
    result = []
    for segment_id in segment_ids:
        segment = session.get(VideoSegment, segment_id)
        if segment is None:
            raise LookupError(f"no segment {segment_id}")
        camera = session.get(Camera, segment.camera_id)
        windows = session.scalars(
            select(AnalysisWindow)
            .where(AnalysisWindow.segment_id == segment.id)
            .order_by(AnalysisWindow.start_time)
        ).all()
        result.append(
            {
                "segment_id": str(segment.id),
                "camera": camera.display_name if camera else None,
                "start_time": _time(segment.start_time),
                "end_time": _time(segment.end_time),
                "status": segment.status,
                "windows": [_window(session, storage, w) for w in windows],
            }
        )
    return result


def _window(session: Session, storage: StorageClient, window: AnalysisWindow) -> dict[str, Any]:
    keyframes = session.scalars(
        select(Keyframe)
        .where(Keyframe.analysis_window_id == window.id)
        .order_by(Keyframe.timestamp)
    ).all()
    observation = window.structured_observation_json or {}
    return {
        "window_id": str(window.id),
        "start_time": _time(window.start_time),
        "end_time": _time(window.end_time),
        "status": window.processing_status,
        "description": window.vlm_description,
        "actions": observation.get("actions", []),
        "visible_objects": observation.get("visible_objects", []),
        "uncertainties": observation.get("uncertainties", []),
        "confidence": window.confidence,
        "model": window.vlm_model,
        "prompt_version": observation.get("prompt_version"),
        "error": window.error,
        "keyframes": [
            {
                "time": _time(k.timestamp),
                "url": storage.presigned_get_url(
                    storage.key_from_uri(k.storage_uri), LINK_LIFETIME
                ),
                "has_vector": k.visual_embedding is not None,
            }
            for k in keyframes
        ],
    }


def _action(action: dict) -> str:
    who = " ".join(p for p in (action.get("subject"), action["type"], action.get("object")) if p)
    return f"{who} ({action['start_second']:.1f}-{action['end_second']:.1f} s)"


def render(segments: list[dict[str, Any]]) -> str:
    lines = []
    for segment in segments:
        lines.append(
            f"Segment {segment['segment_id']}  camera {segment['camera']}  "
            f"{segment['start_time']} -> {segment['end_time']}  [{segment['status']}]"
        )
        if not segment["windows"]:
            lines.append("  no windows yet")
        for n, window in enumerate(segment["windows"], 1):
            lines.append(
                f"  Window {n}  {window['start_time']} -> {window['end_time']}  [{window['status']}]"
            )
            if window["error"]:
                lines.append(f"    error: {window['error']}")
            if window["description"]:
                lines.append(f"    {window['description']}")
            if window["actions"]:
                lines.append(f"    actions: {', '.join(_action(a) for a in window['actions'])}")
            if window["visible_objects"]:
                lines.append(f"    visible: {', '.join(window['visible_objects'])}")
            if window["uncertainties"]:
                lines.append(f"    uncertain: {'; '.join(window['uncertainties'])}")
            if window["model"]:
                lines.append(
                    f"    confidence {window['confidence']:.2f}, {window['model']}, "
                    f"prompt v{window['prompt_version']}"
                )
            for k, keyframe in enumerate(window["keyframes"], 1):
                lines.append(f"    keyframe {k} {keyframe['time']}: {keyframe['url']}")
        lines.append("")
    return "\n".join(lines)


def latest_segments(session: Session, camera: str, count: int) -> list[UUID]:
    camera_ids = list(session.scalars(select(Camera.id).where(Camera.display_name == camera)))
    if not camera_ids:
        raise LookupError(f'no camera named "{camera}"')
    ids = session.scalars(
        select(VideoSegment.id)
        .where(VideoSegment.camera_id.in_(camera_ids))
        .order_by(VideoSegment.start_time.desc())
        .limit(count)
    ).all()
    return list(reversed(ids))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("segment", nargs="?", type=UUID, help="a segment id")
    parser.add_argument("--camera", help="display name of the camera (with --latest)")
    parser.add_argument("--latest", type=int, default=1, help="how many recent segments")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)
    if (args.segment is None) == (args.camera is None):
        parser.error("give a segment id, or --camera NAME")

    with get_sessionmaker()() as session:
        try:
            ids = (
                [args.segment]
                if args.segment
                else latest_segments(session, args.camera, args.latest)
            )
            segments = describe_segments(session, get_storage(), ids)
        except LookupError as error:
            print(error, file=sys.stderr)
            return 2
    print(json.dumps(segments, indent=2) if args.json else render(segments))
    return 0


if __name__ == "__main__":
    sys.exit(main())
