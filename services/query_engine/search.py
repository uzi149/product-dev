"""Search the analysed footage with a plain-English query (until the Investigate page, 4.5).

    python -m services.query_engine.search "a person rings the doorbell"
    python -m services.query_engine.search "a car parks on Car park in the last 24 hours"
    python -m services.query_engine.search "a car parks" --camera "Car park" --since 24h
    python -m services.query_engine.search "someone runs" --start 2026-10-28T09:00 --end 2026-10-28T10:00
    python -m services.query_engine.search "a red car yesterday" --limit 5 --json

Camera names and time phrases in the query become filters (step 4.1; "yesterday" in this
machine's time zone, or --timezone); --camera, --since, --start and --end win over them, and
--no-parse searches the text as it is. Each hit shows the camera, the window's time, the scores,
what the VLM wrote, and a link that plays exactly that window (it opens in the browser for an
hour). Times without a zone are local.
"""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.query_engine.filters import CameraRef, ParsedQuery, apply_explicit, parse_query
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


def all_cameras(session: Session) -> list[CameraRef]:
    return [CameraRef(c.id, c.display_name) for c in session.scalars(select(Camera))]


def describe_filters(parsed: ParsedQuery) -> str:
    """What the query was understood as, e.g. `camera SIM CAM-01, last 2 hours`."""
    parts = [
        f"camera {u.label}" if u.kind == "camera" else u.label.lower() for u in parsed.understood
    ]
    return ", ".join(parts)


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


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise argparse.ArgumentTypeError(f"unknown time zone: {name}") from error


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
    parser.add_argument("--timezone", type=_zone, help="for time phrases, e.g. Asia/Karachi")
    parser.add_argument(
        "--no-parse", action="store_true", help="don't read camera names and times from the query"
    )
    args = parser.parse_args(argv)
    if args.since is not None and args.end is not None:
        parser.error("--end goes with --start, not with --since")
    start = datetime.now(UTC) - args.since if args.since is not None else args.start

    storage = get_storage()
    tz = args.timezone or datetime.now().astimezone().tzinfo
    with get_sessionmaker()() as session:
        try:
            cameras = camera_ids_for(session, args.camera) if args.camera else None
            if args.no_parse:
                parsed = ParsedQuery(args.query)
            else:
                parsed = parse_query(args.query, all_cameras(session), datetime.now(UTC), tz)
            parsed = apply_explicit(parsed, cameras, start, args.end)
            if not parsed.search_text:
                print("add what you're looking for, not only cameras and times", file=sys.stderr)
                return 2
            if parsed.understood and not args.json:
                print(
                    f"understood: {describe_filters(parsed)}; searching for: {parsed.search_text}"
                )
            hits = search(
                session,
                parsed.search_text,
                camera_ids=parsed.camera_ids or None,
                start=parsed.start,
                end=parsed.end,
                limit=args.limit,
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
