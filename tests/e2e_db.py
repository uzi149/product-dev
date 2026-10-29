"""Changes the e2e database directly, for states the e2e stack never reaches by itself.

    python -m tests.e2e_db segment-status SEGMENT_ID failed

The e2e stack runs with analysis off, so no segment fails or gets stuck on its own. Refuses to
touch the development database.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shared.config import Settings
from shared.database.models import ProcessingStatus, VideoSegment
from tests.e2e_stack import e2e_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("segment-status", help="set a segment's analysis status")
    status.add_argument("segment", type=UUID)
    status.add_argument("status", choices=[*ProcessingStatus, "stuck"])
    args = parser.parse_args(argv)

    settings = e2e_settings()
    if settings.postgres_db == Settings().postgres_db:
        sys.exit("the e2e database is the development one (check .env); nothing was changed")
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            segment = session.get(VideoSegment, args.segment)
            if segment is None:
                sys.exit(f"no segment {args.segment}")
            if args.status == "stuck":
                long_ago = datetime.now(UTC) - timedelta(
                    minutes=settings.analysis_stuck_minutes + 1
                )
                segment.set_status(ProcessingStatus.PROCESSING, long_ago)
            else:
                segment.set_status(args.status)
            session.commit()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
