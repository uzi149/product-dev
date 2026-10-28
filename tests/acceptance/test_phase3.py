"""The Phase 3 acceptance test (scope §15): known videos through the real pipeline.

    RUN_MODEL_TESTS=1 pytest -m acceptance -s tests/acceptance

For each ground-truth YAML (test-data/acceptance/virat/ and, once recorded, test-data/camera-01/
and camera-02/): ingest the video as an upload is ingested, analyse every segment with the
configured VLM and the real embedders, then check

- the entities: listed when missing, not a failure (a VLM may call a bag a backpack);
- the actions: found when a window has the same action (or an `also` word) overlapping its time
  within 2 s; at least 80 % of all actions must be found;
- the queries: a window overlapping the expected interval must be in the top 3.

Everything runs in this process on its own database and bucket. A report goes to
data/acceptance/phase3-<time>.md. With an OpenRouter key the clips' keyframes are sent to
OpenRouter: test videos only.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from services.analysis.tasks import Models, process_segment
from services.ingestion.file_ingest import ingest_file
from services.query_engine.retrieval import search
from shared.database.models import AnalysisWindow, Camera
from shared.model_adapters import build_image_embedder, build_text_embedder, build_vlm
from tests.acceptance import ground_truth as gt
from tests.acceptance.conftest import acceptance_dirs
from tests.conftest import ROOT

pytestmark = [pytest.mark.acceptance, pytest.mark.models]

REPORTS = ROOT / "data" / "acceptance"


@dataclass
class VideoResult:
    truth: gt.GroundTruth
    windows: list[dict] = field(default_factory=list)
    found_actions: list[gt.FoundAction] = field(default_factory=list)
    events: list[tuple[gt.Event, bool]] = field(default_factory=list)
    missing_entities: list[str] = field(default_factory=list)
    queries: list[tuple[gt.Query, bool, list]] = field(default_factory=list)


def _seconds(value: datetime, start: datetime) -> float:
    return (value - start).total_seconds()


def run_video(truth, session_factory, storage, settings, models) -> VideoResult:
    if not truth.video.exists():
        pytest.skip(f"{truth.video} is not downloaded (see {truth.path.parent / 'README.md'})")
    with session_factory() as session:
        camera = Camera(display_name=truth.name, source_type="file")
        session.add(camera)
        session.commit()
        ingested = ingest_file(session, storage, truth.video, camera, truth.start_time, settings)
        session.commit()
        segment_ids = [s.id for s in ingested.segments]
        camera_id = camera.id
    for segment_id in segment_ids:
        process_segment(segment_id, session_factory, storage, settings, models)

    result = VideoResult(truth)
    names: list[str] = []
    with session_factory() as session:
        windows = session.scalars(
            select(AnalysisWindow)
            .where(AnalysisWindow.camera_id == camera_id)
            .order_by(AnalysisWindow.start_time)
        ).all()
        for window in windows:
            start = _seconds(window.start_time, truth.start_time)
            end = _seconds(window.end_time, truth.start_time)
            observation = window.structured_observation_json or {}
            for action in observation.get("actions", []):
                result.found_actions.append(
                    gt.FoundAction(
                        action["type"], start + action["start_second"], start + action["end_second"]
                    )
                )
            names += [e["type"] for e in observation.get("entities", [])]
            names += observation.get("visible_objects", [])
            result.windows.append(
                {
                    "start": start,
                    "end": end,
                    "status": window.processing_status,
                    "description": window.vlm_description or window.error,
                    "actions": observation.get("actions", []),
                }
            )
        result.events = [(e, gt.action_found(e, result.found_actions)) for e in truth.events]
        result.missing_entities = gt.missing_entities(truth.entities, names)
        for query in truth.queries:
            hits = search(
                session,
                query.text,
                camera_ids=[camera_id],
                limit=gt.TOP,
                text_embedder=models.text,
                image_embedder=models.image,
            )
            spans = [
                (
                    _seconds(h.start_time, truth.start_time),
                    _seconds(h.end_time, truth.start_time),
                    h,
                )
                for h in hits
            ]
            passed = gt.query_passes(query, [(s, e) for s, e, _ in spans])
            result.queries.append((query, passed, spans))
    return result


def _verdict(query: gt.Query, ok: bool) -> str:
    if query.known_miss:
        return f"{'PASS' if ok else 'MISS'} (known miss: {query.known_miss})"
    return "PASS" if ok else "**FAIL**"


def report(results: list[VideoResult], models) -> Path:
    found = sum(ok for r in results for _, ok in r.events)
    total = sum(len(r.events) for r in results)
    queries_ok = sum(ok for r in results for _, ok, _ in r.queries)
    queries = sum(len(r.queries) for r in results)
    lines = [
        f"# Phase 3 acceptance, {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"VLM {models.vlm.name}; embeddings {models.text.name} + {models.image.name}.",
        "",
        f"- Queries in the top {gt.TOP}: **{queries_ok}/{queries}**",
        f"- Actions found: **{found}/{total}** ({found / max(total, 1):.0%})",
        *(
            f"  - clips needing {bar:.0%}: {share:.0%} found"
            for bar, share in action_shares(results).items()
        ),
        "",
    ]
    for r in results:
        lines += [f"## {r.truth.name}", ""]
        if r.missing_entities:
            lines += [f"Entities not mentioned: {', '.join(r.missing_entities)}", ""]
        lines += ["| Action | Time | Found |", "|---|---|---|"]
        lines += [
            f"| {e.action} | {e.start_s:.0f}-{e.end_s:.0f} s | {'yes' if ok else '**no**'} |"
            for e, ok in r.events
        ]
        lines += ["", "Queries:", ""]
        for query, ok, spans in r.queries:
            lines.append(
                f'- {_verdict(query, ok)} "{query.text}" '
                f"(expected {query.start_s:.0f}-{query.end_s:.0f} s)"
            )
            for rank, (s, e, hit) in enumerate(spans, 1):
                lines.append(
                    f"  {rank}. {s:.0f}-{e:.0f} s, fused {hit.fused_score:.4f} "
                    f"(text {hit.text_score or 0:.3f}, visual {hit.visual_score or 0:.3f}): "
                    f"{hit.description}"
                )
        lines += ["", "Windows:", ""]
        for w in r.windows:
            words = ", ".join(
                f"{a['type']} {a['start_second']:.0f}-{a['end_second']:.0f}" for a in w["actions"]
            )
            lines.append(
                f"- {w['start']:.0f}-{w['end']:.0f} s [{w['status']}] {w['description']}"
                + (f" ({words})" if words else "")
            )
        lines.append("")
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"phase3-{datetime.now():%Y%m%d-%H%M}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def results(acceptance_db, acceptance_storage, acceptance_settings):
    truths = [t for folder in acceptance_dirs() for t in gt.load_folder(folder)]
    if not truths:
        pytest.skip("no ground-truth YAML files found")
    settings = acceptance_settings
    models = Models(
        build_vlm(settings), build_text_embedder(settings), build_image_embedder(settings)
    )
    results = [run_video(t, acceptance_db, acceptance_storage, settings, models) for t in truths]
    path = report(results, models)
    print(f"\nreport: {path}")
    print(path.read_text(encoding="utf-8").split("\n## ")[0])
    return results


def test_every_query_finds_its_interval_in_the_top_3(results):
    failed = [q.text for r in results for q, ok, _ in r.queries if not ok and not q.known_miss]
    assert not failed, f"queries without a top-{gt.TOP} hit: {failed}"
    found_now = [q.text for r in results for q, ok, _ in r.queries if ok and q.known_miss]
    if found_now:
        print(f"\nknown misses that were found this time (drop `known_miss`?): {found_now}")


def action_shares(results: list[VideoResult]) -> dict[float, float]:
    """For each pass mark in use, the share of those clips' actions that were found."""
    outcomes: dict[float, list[bool]] = {}
    for r in results:
        outcomes.setdefault(r.truth.actions_needed, []).extend(ok for _, ok in r.events)
    return {bar: sum(oks) / len(oks) for bar, oks in sorted(outcomes.items()) if oks}


def test_most_actions_are_found(results):
    missed = [f"{r.truth.name}: {e.action}" for r in results for e, ok in r.events if not ok]
    for bar, share in action_shares(results).items():
        assert share >= bar, f"{share:.0%} of the actions found, needed {bar:.0%}; missed {missed}"


def test_every_window_was_analysed(results):
    failed = [
        (r.truth.name, w["start"]) for r in results for w in r.windows if w["status"] != "done"
    ]
    assert not failed, f"windows not done: {failed}"
