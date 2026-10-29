"""Search the analysed footage with a plain-English query (Phase 3 has no UI).

    python -m services.query_engine.search "a person rings the doorbell"
    python -m services.query_engine.search "a car parks" --camera "Car park" --since 24h
    python -m services.query_engine.search "someone runs" --start 2026-10-28T09:00 --end 2026-10-28T10:00
    python -m services.query_engine.search "a red car" --limit 5 --json

Each hit shows the camera, the window's time, the scores, what the VLM wrote, and a link that
plays exactly that window (it opens in the browser for an hour). Times without a zone are local.
"""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.query_engine.retrieval import WindowHit, search
from shared.database.index_settings import IndexSettingsMismatch
from shared.database.models import Camera, VideoSegment
from shared.database.session import get_sessionmaker
from shared.durations import parse_age
from shared.storage import StorageClient, get_storage

LINK_LIFETIME = timedelta(hours=1)


def camera_ids_for(session: Session, names: list[str]) -> list:
    """Ids of the cameras with these display names (not case-sensitive). Raises LookupError
    naming the known cameras when one doesn't exist."""
    ids = []
    for name in names:
        found = list(
            session.scalars(
                select(Camera.id).where(func.lower(Camera.display_name) == name.lower())
            )
        )
        if not found:
            known = sorted(session.scalars(select(Camera.display_name)))
            raise LookupError(f'no camera named "{name}"; cameras: {", ".join(known) or "none"}')
        ids.extend(found)
    return ids


def playback_url(session: Session, storage: StorageClient, hit: WindowHit) -> str:
    """The segment's video, starting at the window and stopping at its end (a media fragment)."""
    segment = session.get(VideoSegment, hit.segment_id)
    url = storage.presigned_get_url(storage.key_from_uri(segment.storage_uri), LINK_LIFETIME)
    return f"{url}#t={hit.segment_start_seconds:.1f},{hit.segment_end_seconds:.1f}"


def _local(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _score(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def render(hits: list[dict]) -> str:
    if not hits:
        return "no matching windows (is the footage analysed? see services.analysis.inspect)"
    lines = []
    for rank, hit in enumerate(hits, 1):
        start = datetime.fromisoformat(hit["start_time"])
        end = datetime.fromisoformat(hit["end_time"])
        lines.append(
            f"{rank}. {hit['camera_name']}  {_local(start)} - {_local(end)[11:]}  "
            f"fused {hit['fused_score']:.4f} (text {_score(hit['text_score'])} "
            f"#{hit['text_rank'] or '-'}, visual {_score(hit['visual_score'])} "
            f"#{hit['visual_rank'] or '-'})"
        )
        if hit["description"]:
            lines.append(f"   {hit['description']}")
        if hit["actions"]:
            words = ", ".join(
                " ".join(p for p in (a.get("subject"), a["type"], a.get("object")) if p)
                for a in hit["actions"]
            )
            lines.append(f"   actions: {words}")
        lines.append(f"   play: {hit['playback_url']}")
        lines.append("")
    return "\n".join(lines)


def _time(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.astimezone()  # no zone: local time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query")
    parser.add_argument("--camera", action="append", default=[], help="display name; can repeat")
    when = parser.add_mutually_exclusive_group()
    when.add_argument("--since", type=parse_age, help="only the last 30m, 2h, 1d, ...")
    when.add_argument("--start", type=_time, help="ISO time, e.g. 2026-10-28T09:00")
    parser.add_argument("--end", type=_time, help="ISO time (with --start, or alone)")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)
    if args.since is not None and args.end is not None:
        parser.error("--end goes with --start, not with --since")
    start = datetime.now(UTC) - args.since if args.since is not None else args.start

    storage = get_storage()
    with get_sessionmaker()() as session:
        try:
            cameras = camera_ids_for(session, args.camera) if args.camera else None
            hits = search(
                session, args.query, camera_ids=cameras, start=start, end=args.end, limit=args.limit
            )
        except (LookupError, IndexSettingsMismatch) as error:
            print(error, file=sys.stderr)
            return 2
        results = [
            {**hit.to_json(), "playback_url": playback_url(session, storage, hit)} for hit in hits
        ]
    print(json.dumps(results, indent=2) if args.json else render(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
