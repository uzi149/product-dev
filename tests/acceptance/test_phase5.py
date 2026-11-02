"""The Phase 5 acceptance test (scope §15, ADR 0036): a custom trigger end to end, automated.

    RUN_MODEL_TESTS=1 pytest -m acceptance -s tests/acceptance/test_phase5.py

"Assign a trigger to CAM-01 only. Confirm CAM-02 never evaluates it. Trigger an event on CAM-01.
Receive a live notification and playable evidence." On the public clips chosen for Phases 1-5
(CAVIAR, MEVA, ABODA; `docs/testing-guide/test-video-sources.md`), uploaded like test videos:

- the rule "Report when someone places an object and leaves it behind" is compiled by the
  configured VLM (5.1) and assigned to CAM-01 only;
- CAM-01 gets six clips where an object is left behind (positives) and two where it isn't
  (an object put down and picked up again; an object set down by someone who stays);
- CAM-02 gets a copy of a positive clip: it would match, but the trigger isn't on CAM-02.

Every segment is analysed in this process (3.3), and every window is evaluated as the analysis
worker does it (5.2). Pass: CAM-02's windows never load the trigger; at least one event on CAM-01,
alerted, with evidence footage in storage and a notice sent; no event on CAM-02. Reported, not
judged: positives found and false events against the MVP targets (>= 80 % found, <= 1 false
per hour of footage), verification, and each event's time from upload to alert (the live delay
is measured in the 5.5 live run). A report goes to data/acceptance/phase5-<time>.md. With an
OpenRouter key the clips' keyframes are sent to OpenRouter: test videos only.
"""

import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from services.analysis.tasks import Models, process_segment
from services.ingestion.file_ingest import ingest_file
from services.triggers import notices
from services.triggers.compile import compile_rule
from services.triggers.evaluate import enabled_triggers
from services.triggers.events import FinishTools
from shared.camera_status import get_redis
from shared.database.models import (
    AnalysisWindow,
    Camera,
    CompileStatus,
    Event,
    EventEvidence,
    Trigger,
    VideoSegment,
)
from shared.model_adapters import build_image_embedder, build_text_embedder, build_vlm
from tests.conftest import ROOT

pytestmark = [pytest.mark.acceptance, pytest.mark.models]

REPORTS = ROOT / "data" / "acceptance"
CLIPS = Path(
    os.environ.get("PHASE5_CLIPS")
    or ROOT
    / "test-data"
    / "The Real Test Data - according to the use cases required by this product-dev app"
    / "Clips by action  - test case"
)
RULE = "Report when someone places an object and leaves it behind"
BASE = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)


@dataclass
class Clip:
    name: str
    file: str
    camera: str
    positive: bool  # an object is left behind (for CAM-02: it would match)
    cut: tuple[float, float] | None = None  # (start, seconds) of a long file
    start_time: datetime | None = None
    end_time: datetime | None = None
    stored_at: datetime | None = None
    events: list[Event] = field(default_factory=list)
    windows: list[AnalysisWindow] = field(default_factory=list)


def clips() -> list[Clip]:
    return [
        Clip("CAVIAR LeftBag", "LeftBag.mpg", "CAM-01", True),
        Clip("CAVIAR LeftBag_AtChair", "LeftBag_AtChair.mpg", "CAM-01", True),
        Clip("MEVA abandoned package (bus.G331)", "meva-abandon-package.mp4", "CAM-01", True),
        # Clearer views (640-720 px) than CAVIAR's 384x288, added after the first runs (ADR 0036).
        Clip("ABODA video1 (bag dropped in a corridor)", "video1.avi", "CAM-01", True),
        Clip("ABODA video9 (bag left on a table)", "video9.avi", "CAM-01", True),
        Clip("ABODA video10 (bag left on a table)", "video10.avi", "CAM-01", True),
        Clip("CAVIAR LeftBag_PickedUp (negative)", "LeftBag_PickedUp.mpg", "CAM-01", False),
        Clip(
            "MEVA object set down, person stays (school.G421, negative)",
            "2018-03-11.11-30-01.11-35-01.school.G421.r13.avi",
            "CAM-01",
            False,
            cut=(20, 40),  # Set_Down_Object at 35-40 s of the original
        ),
        Clip("CAVIAR LeftBag on CAM-02 (not assigned)", "LeftBag.mpg", "CAM-02", True),
    ]


class Checks(logging.Handler):
    """Every evaluation's (window, camera, number of triggers checked), from the worker's log."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.records: list[tuple[str, str, int]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if str(record.msg).startswith("window %s (camera %s): checking"):
            window_id, camera_id, count = record.args[:3]
            self.records.append((str(window_id), str(camera_id), int(count)))


@dataclass
class Run:
    clips: list[Clip]
    cameras: dict[str, Camera]
    trigger: Trigger
    checks: list[tuple[str, str, int]]
    notices: list[dict]
    models: Models
    evidence: dict = field(default_factory=dict)  # event id -> [(segment key, start, end, length)]


def prepared(clip: Clip, tmp: Path) -> Path:
    source = CLIPS / clip.file
    if clip.cut is None:
        return source
    target = tmp / f"{source.stem}-{clip.cut[0]:g}-{clip.cut[1]:g}.mp4"
    if not target.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(clip.cut[0]), "-i", str(source),
             "-t", str(clip.cut[1]), "-c:v", "libx264", "-crf", "23", "-an", str(target)],
            check=True,
        )  # fmt: skip
    return target


@pytest.fixture(scope="module")
def phase5(acceptance_db, acceptance_storage, acceptance_settings, tmp_path_factory):
    chosen = clips()
    missing = sorted({c.file for c in chosen if not (CLIPS / c.file).exists()})
    if missing:
        pytest.skip(f"clips not downloaded into {CLIPS}: {', '.join(missing)}")
    settings = acceptance_settings
    published: list[dict] = []
    redis_client = get_redis()

    def publish(notice: dict) -> None:
        published.append(notice)
        notices.publish(redis_client, notice)  # also on the real channel, as the worker does

    models = Models(
        build_vlm(settings),
        build_text_embedder(settings),
        build_image_embedder(settings),
        FinishTools(
            settings=settings,
            publish=publish,
            schedule_finish=lambda event_id, seconds: None,  # uploads finish at once
            camera_status=lambda camera_id: None,
        ),
    )

    with acceptance_db() as session:
        cameras = {
            name: Camera(display_name=name, source_type="file") for name in ("CAM-01", "CAM-02")
        }
        session.add_all(cameras.values())
        compiled = compile_rule(models.vlm, RULE)
        trigger = Trigger(
            name="Object left behind",
            natural_language_rule=RULE,
            compiled_rule_json=compiled.model_dump(mode="json"),
            compile_status=CompileStatus.DONE,
            enabled=True,
        )
        trigger.cameras = [cameras["CAM-01"]]  # CAM-01 only
        session.add(trigger)
        session.commit()
        for camera in cameras.values():
            session.refresh(camera)
        session.refresh(trigger)
        session.expunge_all()

    checks = Checks()
    evaluate_log = logging.getLogger("services.triggers.evaluate")
    evaluate_log.addHandler(checks)
    evaluate_log.setLevel(logging.INFO)
    tmp = tmp_path_factory.mktemp("phase5")
    try:
        for n, clip in enumerate(chosen):
            clip.start_time = BASE + timedelta(hours=n)  # far apart: no cooldown between clips
            with acceptance_db() as session:
                camera = session.merge(cameras[clip.camera])
                ingested = ingest_file(
                    session, acceptance_storage, prepared(clip, tmp), camera, clip.start_time,
                    settings,
                )  # fmt: skip
                session.commit()
                segment_ids = [s.id for s in ingested.segments]
                clip.end_time = max(
                    (s.end_time for s in ingested.segments), default=clip.start_time
                )
            clip.stored_at = datetime.now(UTC)
            for segment_id in segment_ids:
                process_segment(segment_id, acceptance_db, acceptance_storage, settings, models)
    finally:
        evaluate_log.removeHandler(checks)

    run = Run(chosen, cameras, trigger, checks.records, published, models)
    with acceptance_db() as session:
        for clip in chosen:
            camera_id = cameras[clip.camera].id
            clip.events = list(
                session.scalars(
                    select(Event)
                    .where(
                        Event.camera_id == camera_id,
                        Event.start_time >= clip.start_time,
                        Event.start_time <= clip.end_time + timedelta(minutes=1),
                    )
                    .order_by(Event.start_time)
                )
            )
            clip.windows = list(
                session.scalars(
                    select(AnalysisWindow)
                    .where(
                        AnalysisWindow.camera_id == camera_id,
                        AnalysisWindow.start_time >= clip.start_time,
                        AnalysisWindow.start_time <= clip.end_time,
                    )
                    .order_by(AnalysisWindow.start_time)
                )
            )
            for event in clip.events:
                rows = session.execute(
                    select(EventEvidence, VideoSegment)
                    .join(VideoSegment, VideoSegment.id == EventEvidence.segment_id)
                    .where(EventEvidence.event_id == event.id)
                    .order_by(EventEvidence.sequence_order)
                ).all()
                run.evidence[event.id] = [
                    (
                        acceptance_storage.key_from_uri(segment.storage_uri),
                        evidence.start_offset_ms,
                        evidence.end_offset_ms,
                        segment.duration_ms,
                    )
                    for evidence, segment in rows
                ]
        session.expunge_all()
    path = report(run)
    print(f"\nreport: {path}")
    print(path.read_text(encoding="utf-8").split("\n## ")[0])
    return run


def found(clip: Clip) -> bool:
    return bool(clip.events)


def report(run: Run) -> Path:
    rule = run.trigger.compiled_rule_json
    cam01 = [c for c in run.clips if c.camera == "CAM-01"]
    positives = [c for c in cam01 if c.positive]
    negatives = [c for c in cam01 if not c.positive]
    false_events = sum(len(c.events) for c in negatives)
    hours = sum((c.end_time - c.start_time).total_seconds() for c in cam01) / 3600
    cam02 = run.cameras["CAM-02"].id
    cam02_checks = [r for r in run.checks if r[1] == str(cam02)]
    lines = [
        f"# Phase 5 acceptance, {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"VLM {run.models.vlm.name}; embeddings {run.models.text.name} + {run.models.image.name}.",
        "",
        (
            f'Rule "{RULE}" compiled as **{rule["mode"]}**: '
            f"{' -> '.join(rule['required_action_sequence']) or rule['search_text']}, entities "
            f"{', '.join(rule['required_entities']) or 'none'}, gap "
            f"{rule['maximum_sequence_gap_seconds']} s, verification "
            f"{'on' if rule['requires_vlm_verification'] else 'off'}. Assigned to CAM-01 only."
        ),
        "",
        (
            f"- CAM-02 windows checked: {len(cam02_checks)}, with the trigger loaded: "
            f"**{sum(r[2] > 0 for r in cam02_checks)}**"
        ),
        f"- CAM-02 events: **{sum(len(c.events) for c in run.clips if c.camera == 'CAM-02')}**",
        (
            f"- CAM-01 positives found: **{sum(found(c) for c in positives)}/{len(positives)}** "
            "(target >= 80 %)"
        ),
        (
            f"- CAM-01 false events: **{false_events}** in {hours * 60:.1f} min of footage "
            "(target <= 1 per hour)"
        ),
        f"- Notices sent: {len(run.notices)}",
        "",
    ]
    for clip in run.clips:
        expected = (
            "should not fire (not assigned)"
            if clip.camera == "CAM-02"
            else ("object left behind" if clip.positive else "should not fire")
        )
        lines += [f"## {clip.name} on {clip.camera}: {expected}", ""]
        for event in clip.events:
            details = event.details_json or {}
            verification = details.get("verification", {})
            delay = (
                (event.alerted_at - clip.stored_at).total_seconds() if event.alerted_at else None
            )
            lines.append(
                f"- **Event** {(event.start_time - clip.start_time).total_seconds():.0f}-"
                f"{(event.end_time - clip.start_time).total_seconds():.0f} s, confidence "
                f"{event.confidence:.2f}, {verification.get('status')}"
                + (f" ({verification.get('reason')})" if verification.get("reason") else "")
                + f", {len(details.get('matches', []))} match(es), "
                f"{len(run.evidence.get(event.id, []))} evidence part(s)"
                + (
                    f", alerted {delay:.0f} s after the upload was stored"
                    if delay is not None
                    else ""
                )
            )
        if not clip.events:
            lines.append("- No event")
        lines += ["", "Windows:"]
        for w in clip.windows:
            actions = [
                a.get("type") for a in (w.structured_observation_json or {}).get("actions", [])
            ]
            lines.append(
                f"- {(w.start_time - clip.start_time).total_seconds():.0f} s "
                f"[{', '.join(actions)}]: {w.vlm_description}"
            )
        lines.append("")
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"phase5-{datetime.now():%Y%m%d-%H%M}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_the_trigger_is_assigned_to_cam_01_only(phase5, acceptance_db):
    with acceptance_db() as session:
        assert enabled_triggers(session, phase5.cameras["CAM-02"].id) == []
        assert [t.id for t in enabled_triggers(session, phase5.cameras["CAM-01"].id)] == [
            phase5.trigger.id
        ]


def test_cam_02_never_evaluates_the_trigger(phase5):
    cam02 = str(phase5.cameras["CAM-02"].id)
    checked = [r for r in phase5.checks if r[1] == cam02]
    assert checked, "no CAM-02 window was analysed"
    assert all(count == 0 for _, _, count in checked), "a CAM-02 window loaded a trigger"


def test_no_event_on_cam_02(phase5):
    assert not [c.name for c in phase5.clips if c.camera == "CAM-02" and c.events]


def test_an_event_on_cam_01_with_playable_evidence_and_a_notice(phase5, acceptance_storage):
    events = [e for c in phase5.clips if c.camera == "CAM-01" for e in c.events]
    alerted = [e for e in events if e.alerted_at is not None]
    assert alerted, "no event on CAM-01"
    noticed = {n["event_id"] for n in phase5.notices}
    for event in alerted:
        assert str(event.id) in noticed, f"no notice for event {event.id}"
        parts = phase5.evidence[event.id]
        assert parts, f"event {event.id} has no evidence"
        for key, start, end, length in parts:
            assert acceptance_storage.exists(key), f"evidence footage missing: {key}"
            assert 0 <= start < end <= length
            assert acceptance_storage.presigned_get_url(key)
