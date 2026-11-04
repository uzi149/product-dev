"""The Phase 4 acceptance test (scope §15, ADR 0030): queries through the whole query pipeline.

    RUN_MODEL_TESTS=1 pytest -m acceptance -s tests/acceptance/test_phase4.py

"Query an event spanning overlapping windows; return one merged result; display gaps explicitly
when results are separated." On the VIRAT clips (test-data/acceptance/virat/):

- every query whose expected interval spans more than one analysis window runs as a real query
  (filters, hybrid search, merging, verification with the configured VLM, step 4.4). A merged
  result among the top 3 must cover the whole interval (2 s slack), and no other result may
  cover part of it: the event is one result, not pieces;
- the gap case: the first clip is stored twice on one camera, 5 minutes apart. Its first query
  must give one result with a video part in each copy and an explicit gap part of at least
  5 minutes between them.

Verification runs with an API backend and is reported, not judged (VLM answers vary); with the
local model it is skipped (ADR 0029). Everything runs in this process on the acceptance database
and bucket. A report goes to data/acceptance/phase4-<time>.md. With an OpenRouter key the clips'
keyframes are sent to OpenRouter: test videos only.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from services.analysis.tasks import Models, process_segment
from services.ingestion.file_ingest import ingest_file
from services.query_engine.run import Tools, process_query
from shared.database.models import Camera, QueryEvidence, QuerySession, VideoSegment
from shared.model_adapters import build_image_embedder, build_text_embedder, build_vlm
from tests.acceptance import ground_truth as gt
from tests.acceptance.conftest import acceptance_dirs
from tests.conftest import ROOT

pytestmark = [pytest.mark.acceptance, pytest.mark.models]

REPORTS = ROOT / "data" / "acceptance"
WINDOW_SECONDS = 12
SLACK = 2.0
GAP = timedelta(minutes=5)


@dataclass
class QueryOutcome:
    name: str
    query: gt.Query
    origin: datetime  # the clip's start time: result times are seconds from it
    results: list[QueryEvidence] = field(default_factory=list)
    status: str = ""
    verification: str | None = None

    def seconds(self, value: datetime) -> float:
        return (value - self.origin).total_seconds()

    def covering(self) -> QueryEvidence | None:
        """The first of the top 3 results that covers the whole expected interval."""
        for r in self.results[: gt.TOP]:
            if (
                self.seconds(r.start_time) <= self.query.start_s + SLACK
                and self.seconds(r.end_time) >= self.query.end_s - SLACK
            ):
                return r
        return None

    def pieces(self) -> int:
        """How many results overlap the expected interval (1 when it is one result)."""
        return sum(
            gt.overlaps(
                self.query.start_s,
                self.query.end_s,
                self.seconds(r.start_time),
                self.seconds(r.end_time),
            )
            for r in self.results
        )


def spans_windows(query: gt.Query) -> bool:
    """An interval longer than one window, or crossing the 9 s window starts, spans windows."""
    if query.end_s - query.start_s > WINDOW_SECONDS:
        return True
    return int(query.start_s // 9) != int(max(query.start_s, query.end_s - 0.01) // 9)


def store(truth, camera, start_time, session_factory, storage, settings, models) -> None:
    with session_factory() as session:
        camera = session.merge(camera)
        ingested = ingest_file(session, storage, truth.video, camera, start_time, settings)
        session.commit()
        segment_ids = [s.id for s in ingested.segments]
    for segment_id in segment_ids:
        process_segment(segment_id, session_factory, storage, settings, models)


def new_camera(session_factory, name: str) -> Camera:
    with session_factory() as session:
        camera = Camera(display_name=name, source_type="file")
        session.add(camera)
        session.commit()
        session.refresh(camera)
        session.expunge(camera)
        return camera


def run(query_text, camera_id, tools, session_factory) -> tuple[str, str | None, list]:
    with session_factory() as session:
        query = QuerySession(
            query_text=query_text, search_text=query_text, camera_filter=[camera_id]
        )
        session.add(query)
        session.commit()
        query_id = query.id
    process_query(query_id, tools)
    with session_factory() as session:
        query = session.get(QuerySession, query_id)
        rows = session.scalars(
            select(QueryEvidence)
            .where(QueryEvidence.query_session_id == query_id)
            .order_by(QueryEvidence.rank)
        ).all()
        session.expunge_all()
        return query.status, query.verification, rows


@dataclass
class Run:
    outcomes: list[QueryOutcome]
    gap: QueryOutcome | None
    models: Models


@pytest.fixture(scope="module")
def phase4(acceptance_db, acceptance_storage, acceptance_settings):
    truths = [t for folder in acceptance_dirs() for t in gt.load_folder(folder)]
    truths = [t for t in truths if t.video.exists()]
    if not truths:
        pytest.skip("no ground-truth clips downloaded (see test-data/acceptance/virat/README.md)")
    settings = acceptance_settings
    models = Models(
        build_vlm(settings), build_text_embedder(settings), build_image_embedder(settings)
    )
    tools = Tools(
        session_factory=acceptance_db,
        storage=acceptance_storage,
        settings=settings,
        publish=lambda _: None,
        vlm=lambda: models.vlm,
        text_embedder=models.text,
        image_embedder=models.image,
    )

    outcomes = []
    for truth in truths:
        camera = new_camera(acceptance_db, truth.name)
        store(truth, camera, truth.start_time, acceptance_db, acceptance_storage, settings, models)
        for query in truth.queries:
            if query.known_miss or not spans_windows(query):
                continue
            outcome = QueryOutcome(truth.name, query, truth.start_time)
            outcome.status, outcome.verification, outcome.results = run(
                query.text, camera.id, tools, acceptance_db
            )
            outcomes.append(outcome)

    # The gap case: the first clip twice on one camera, the second copy 5 minutes after the first.
    first = truths[0]
    camera = new_camera(acceptance_db, f"{first.name} twice")
    store(first, camera, first.start_time, acceptance_db, acceptance_storage, settings, models)
    with acceptance_db() as session:
        end = max(
            session.scalars(
                select(VideoSegment.end_time).where(VideoSegment.camera_id == camera.id)
            )
        )
    store(first, camera, end + GAP, acceptance_db, acceptance_storage, settings, models)
    gap = QueryOutcome(f"{first.name} twice", first.queries[0], first.start_time)
    gap.status, gap.verification, gap.results = run(gap.query.text, camera.id, tools, acceptance_db)

    result = Run(outcomes, gap, models)
    path = report(result)
    print(f"\nreport: {path}")
    print(path.read_text(encoding="utf-8").split("\n## ")[0])
    return result


def _parts(result: QueryEvidence) -> list[dict]:
    return result.manifest_json["parts"]


def report(run: Run) -> Path:
    covered = sum(o.covering() is not None and o.pieces() == 1 for o in run.outcomes)
    lines = [
        f"# Phase 4 acceptance, {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"VLM {run.models.vlm.name}; embeddings {run.models.text.name} + {run.models.image.name}.",
        "",
        f"- Multi-window events returned as one covering result: **{covered}/{len(run.outcomes)}**",
        f"- Gap case: **{'pass' if gap_passes(run.gap) else 'FAIL'}**",
        "",
    ]
    for o in [*run.outcomes, run.gap]:
        ok = (o.covering() is not None and o.pieces() == 1) if o is not run.gap else gap_passes(o)
        lines += [
            f'## {o.name}: "{o.query.text}"',
            "",
            (
                f"Expected {o.query.start_s:.0f}-{o.query.end_s:.0f} s; query {o.status}, "
                f"verification {o.verification}; **{'pass' if ok else 'FAIL'}**"
            ),
            "",
        ]
        for r in o.results:
            parts = ", ".join(
                f"gap {p['duration_ms'] / 1000:.0f} s"
                if p["type"] == "gap"
                else f"{o.seconds(datetime.fromisoformat(p['start_time'])):.0f}-"
                f"{o.seconds(datetime.fromisoformat(p['end_time'])):.0f} s"
                for p in _parts(r)
            )
            verdict = r.status + (
                f" {r.verification_confidence:.2f}" if r.verification_confidence is not None else ""
            )
            lines.append(
                f"{r.rank}. {o.seconds(r.start_time):.0f}-{o.seconds(r.end_time):.0f} s, "
                f"score {r.score:.3f}, {verdict} [{parts}]"
                + (f": {r.verification_reason}" if r.verification_reason else "")
            )
        lines.append("")
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"phase4-{datetime.now():%Y%m%d-%H%M}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def gap_passes(outcome: QueryOutcome) -> bool:
    for result in outcome.results[: gt.TOP]:
        parts = _parts(result)
        videos = [p for p in parts if p["type"] == "video"]
        gaps = [p for p in parts if p["type"] == "gap"]
        if len({p["segment_id"] for p in videos}) >= 2 and any(
            g["duration_ms"] >= GAP.total_seconds() * 1000 for g in gaps
        ):
            return True
    return False


def test_every_query_ran(phase4):
    assert phase4.outcomes, "no ground-truth query spans more than one window"
    assert all(o.status == "done" for o in [*phase4.outcomes, phase4.gap])


def test_an_event_over_overlapping_windows_is_one_merged_result(phase4):
    failed = [o.query.text for o in phase4.outcomes if o.covering() is None or o.pieces() != 1]
    assert not failed, f"not one covering result: {failed}"


def test_separated_clips_show_an_explicit_gap(phase4):
    assert gap_passes(phase4.gap), "no result with both copies and a gap of 5 minutes or more"
