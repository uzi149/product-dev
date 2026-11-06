"""Making and storing evidence marks (step 5.7, ADR 0038).

**Events** are marked after their evidence is written (`process_finish`: the first alert and every
extension); **Investigate results** when the operator opens one. Both run as `mark_evidence` on
the query worker (ADR 0028), so marking never holds up an alert or live analysis. With the local
model marks are off unless `MARKS_LOCAL_MODEL` is on (ADR 0039): the row says `skipped` and
nothing is queued.

An event extended while it is being marked is marked again. An older run never overwrites a newer
one: the event's `updated_at` at the start of a run is stored with its marks
(`evidence_updated_at`), and a run whose event has changed since keeps its marks but leaves the
newer request's status, so the player shows them as "updating".
"""

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.events.playback import event_manifest, evidence_rows
from services.marks.build import MarksError, build_marks
from services.marks.labels import grounding_labels
from shared.config import Settings, get_settings
from shared.database.models import (
    Event,
    EvidenceMarks,
    MarksStatus,
    QueryEvidence,
    QuerySession,
    Trigger,
    VideoSegment,
)
from shared.database.session import get_sessionmaker
from shared.jobs.celery_app import MARK_EVIDENCE, celery_app
from shared.model_adapters import VisionLanguageModel, get_vlm
from shared.model_adapters.grounding import PROMPT_VERSION
from shared.model_adapters.vlm import VLMError
from shared.schemas.query import PlaybackManifest, VideoPart
from shared.storage import NotFound, StorageClient, StorageError, get_storage
from shared.video.frames import FrameReadError

log = logging.getLogger(__name__)

REASON_LENGTH = 500


@dataclass
class _Target:
    manifest: PlaybackManifest
    question: str
    labels: list[str]
    stamp: datetime | None  # the event's updated_at; None for a query result
    uris: dict[UUID, str]


def _target(session: Session, row: EvidenceMarks) -> _Target | None:
    if row.event_id is not None:
        event = session.get(Event, row.event_id)
        if event is None:
            return None
        trigger = session.get(Trigger, event.trigger_id)
        rows = evidence_rows(session, event.id)
        manifest = event_manifest(event, rows)
        rule = trigger.compiled_rule_json or {}
        question = trigger.natural_language_rule
        labels = grounding_labels(question, rule.get("required_entities") or [])
        return _Target(manifest, question, labels, event.updated_at,
                       {s.id: s.storage_uri for _, s in rows})  # fmt: skip
    evidence = session.get(QueryEvidence, row.query_evidence_id)
    if evidence is None:
        return None
    query = session.get(QuerySession, evidence.query_session_id)
    manifest = PlaybackManifest.model_validate(evidence.manifest_json)
    ids = {p.segment_id for p in manifest.parts if isinstance(p, VideoPart)}
    segments = session.scalars(select(VideoSegment).where(VideoSegment.id.in_(ids))) if ids else []
    question = query.search_text or query.query_text
    return _Target(manifest, question, grounding_labels(question), None,
                   {s.id: s.storage_uri for s in segments})  # fmt: skip


@dataclass
class MarkTools:
    """What marking needs; tests pass fakes."""

    settings: Settings
    storage: StorageClient
    vlm: Callable[[], VisionLanguageModel]
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


def default_mark_tools() -> MarkTools:
    return MarkTools(settings=get_settings(), storage=get_storage(), vlm=get_vlm)


def _lock(session: Session, marks_id: UUID) -> EvidenceMarks | None:
    return session.scalars(
        select(EvidenceMarks).where(EvidenceMarks.id == marks_id).with_for_update()
    ).first()


def _changed_since(session: Session, row: EvidenceMarks, stamp: datetime | None) -> bool:
    """The event was extended after this run read it: a newer run is (or will be) queued."""
    if stamp is None or row.event_id is None:
        return False
    event = session.get(Event, row.event_id)
    return event is not None and event.updated_at > stamp


def process_marks(
    marks_id: UUID,
    session_factory: Callable[[], Session] | None = None,
    tools: MarkTools | None = None,
) -> None:
    session_factory = session_factory or get_sessionmaker()
    tools = tools or default_mark_tools()
    with session_factory() as session:
        row = session.get(EvidenceMarks, marks_id)
        if row is None:
            log.info("marks %s no longer exist (deleted with their clip?)", marks_id)
            return
        if row.status in (MarksStatus.DONE, MarksStatus.SKIPPED) and row.event_id is None:
            return  # a result's marks asked twice
        target = _target(session, row)
        if target is None:
            return
        row.status = MarksStatus.RUNNING
        row.started_at = tools.now()
        session.commit()

    vlm = None
    try:
        vlm = tools.vlm()
        with tempfile.TemporaryDirectory(prefix="sentinel-marks-") as tmp:
            sources: dict[UUID, Path] = {}
            for part in target.manifest.parts:
                if isinstance(part, VideoPart) and part.segment_id not in sources:
                    path = Path(tmp, f"{part.segment_id}.mp4")
                    tools.storage.download_file(
                        tools.storage.key_from_uri(target.uris[part.segment_id]), path
                    )
                    sources[part.segment_id] = path
            built = build_marks(target.manifest, sources, vlm, target.labels, target.question,
                                tools.settings)  # fmt: skip
    except (
        VLMError,
        MarksError,
        FrameReadError,
        StorageError,
        NotFound,
        OSError,
        KeyError,
    ) as error:
        _store_failure(session_factory, marks_id, target.stamp, error, tools)
        return

    with session_factory() as session:
        row = _lock(session, marks_id)
        if row is None:
            session.rollback()
            return
        if (
            target.stamp is not None
            and row.evidence_updated_at is not None
            and row.evidence_updated_at > target.stamp
        ):
            session.rollback()  # a newer run already stored its marks
            return
        row.tracks_json = built.tracks
        row.pick_status = built.pick_status
        row.keyframes = built.keyframes
        row.model = vlm.name
        row.prompt_version = PROMPT_VERSION
        row.evidence_updated_at = target.stamp
        row.finished_at = tools.now()
        if not _changed_since(session, row, target.stamp):
            row.status = MarksStatus.DONE
            row.reason = None
        session.commit()
    log.info(
        "marks %s: %d track(s), %d/%d keyframe(s) answered, pick %s",
        marks_id,
        len(built.tracks),
        built.grounded,
        built.keyframes,
        built.pick_status,
    )


def _store_failure(
    session_factory: Callable[[], Session],
    marks_id: UUID,
    stamp: datetime | None,
    error: Exception,
    tools: MarkTools,
) -> None:
    log.warning("marks %s couldn't be made: %s", marks_id, error)
    with session_factory() as session:
        row = _lock(session, marks_id)
        if row is None or _changed_since(session, row, stamp):
            session.rollback()
            return
        row.status = MarksStatus.FAILED
        row.reason = (str(error) or type(error).__name__)[:REASON_LENGTH]
        row.finished_at = tools.now()
        session.commit()


@celery_app.task(name=MARK_EVIDENCE)
def mark_evidence(marks_id: str) -> None:
    process_marks(UUID(marks_id))
