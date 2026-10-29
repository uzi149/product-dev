"""Queue segments for analysis on purpose: existing footage, or after fixing a failure.

    python -m services.analysis.requeue                          # every pending segment
    python -m services.analysis.requeue --since 2h               # pending, started in the last 2 h
    python -m services.analysis.requeue --status failed          # retry the failed ones
    python -m services.analysis.requeue --camera "Front door" --since 1d
    python -m services.analysis.requeue --segment SEGMENT_ID     # one segment that is not done

It works even when ANALYSIS_ENABLED is false. A worker on the analysis queue does the work:
    celery -A shared.jobs.celery_app worker --pool=solo -Q default,analysis
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from shared.database.models import Camera, ProcessingStatus, VideoSegment
from shared.database.session import get_sessionmaker
from shared.durations import parse_age
from shared.jobs import celery_app as jobs


def find_segments(
    session,
    status: str,
    camera: str | None = None,
    since: timedelta | None = None,
    now: datetime | None = None,
) -> list[UUID]:
    """Ids of the matching segments, oldest first. Raises LookupError for an unknown camera."""
    query = select(VideoSegment.id).where(VideoSegment.status == status)
    if camera is not None:
        camera_ids = list(session.scalars(select(Camera.id).where(Camera.display_name == camera)))
        if not camera_ids:
            raise LookupError(f'no camera named "{camera}"')
        query = query.where(VideoSegment.camera_id.in_(camera_ids))
    if since is not None:
        query = query.where(VideoSegment.start_time >= (now or datetime.now(UTC)) - since)
    return list(session.scalars(query.order_by(VideoSegment.start_time)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", choices=["pending", "failed"], default="pending")
    parser.add_argument("--camera", help="display name of the camera")
    parser.add_argument("--since", type=parse_age, help="only segments that started this recently")
    parser.add_argument("--segment", type=UUID, help="one segment (pending or failed)")
    args = parser.parse_args(argv)

    with get_sessionmaker()() as session:
        if args.segment is not None:
            segment = session.get(VideoSegment, args.segment)
            if segment is None:
                print(f"no segment {args.segment}", file=sys.stderr)
                return 2
            if segment.status == ProcessingStatus.DONE:
                print(f"segment {args.segment} is already analysed; nothing to do")
                return 0
            ids = [args.segment]
        else:
            try:
                ids = find_segments(session, args.status, args.camera, args.since)
            except LookupError as error:
                print(error, file=sys.stderr)
                return 2

    for segment_id in ids:
        try:
            jobs.queue_analyze_segment(segment_id)
        except Exception as error:  # noqa: BLE001 - any broker failure
            print(f"could not queue (is Redis running?): {error}", file=sys.stderr)
            return 1
    print(f"queued {len(ids)} segment(s) for analysis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
