"""Custom triggers (scope §12.1-12.2, §13; step 5.1).

A trigger is a rule in plain English and the cameras it watches. Its rule is compiled into
structured criteria by the query worker (ADR 0032): a new or reworded trigger is `pending`, and
can only be enabled once the compile is `done`. Deleting a trigger also deletes its events
(ADR 0034).
"""

from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from services.api.deps import get_session, get_trigger_queue
from shared.database.models import (
    Camera,
    CompileStatus,
    Event,
    EventStatus,
    Trigger,
    TriggerCamera,
)
from shared.schemas.trigger import (
    CompiledRule,
    EventCounts,
    TriggerCreate,
    TriggerRead,
    TriggerUpdate,
)
from shared.schemas.trigger import TriggerCamera as TriggerCameraRead

router = APIRouter(prefix="/triggers", tags=["triggers"])


def _get_trigger(session: Session, trigger_id: UUID) -> Trigger:
    trigger = session.get(Trigger, trigger_id)
    if trigger is None:
        raise HTTPException(404, "trigger not found")
    return trigger


def _cameras(session: Session, camera_ids: list[UUID]) -> list[Camera]:
    ids = list(dict.fromkeys(camera_ids))
    found = {c.id: c for c in session.scalars(select(Camera).where(Camera.id.in_(ids)))}
    unknown = [str(i) for i in ids if i not in found]
    if unknown:
        raise HTTPException(422, f"unknown camera: {', '.join(unknown)}")
    return [found[i] for i in ids]


def _event_counts(session: Session, trigger_ids: list[UUID]) -> dict[UUID, EventCounts]:
    counts: dict[UUID, Counter] = {i: Counter() for i in trigger_ids}
    if trigger_ids:
        rows = session.execute(
            select(Event.trigger_id, Event.status, func.count())
            .where(Event.trigger_id.in_(trigger_ids))
            .group_by(Event.trigger_id, Event.status)
        )
        for trigger_id, status, count in rows:
            counts[trigger_id][status] = count
    return {
        i: EventCounts(new=c[EventStatus.NEW], total=sum(c.values())) for i, c in counts.items()
    }


def _read(trigger: Trigger, events: EventCounts) -> TriggerRead:
    return TriggerRead(
        id=trigger.id,
        name=trigger.name,
        natural_language_rule=trigger.natural_language_rule,
        cameras=[
            TriggerCameraRead(id=c.id, display_name=c.display_name)
            for c in sorted(trigger.cameras, key=lambda c: c.display_name.lower())
        ],
        enabled=trigger.enabled,
        compile_status=trigger.compile_status,
        compile_error=trigger.compile_error,
        compiled=CompiledRule.model_validate(trigger.compiled_rule_json)
        if trigger.compiled_rule_json
        else None,
        minimum_confidence=trigger.minimum_confidence,
        cooldown_seconds=trigger.cooldown_seconds,
        pre_roll_seconds=trigger.pre_roll_seconds,
        post_roll_seconds=trigger.post_roll_seconds,
        events=events,
        created_at=trigger.created_at,
        updated_at=trigger.updated_at,
    )


def _read_one(session: Session, trigger: Trigger) -> TriggerRead:
    return _read(trigger, _event_counts(session, [trigger.id])[trigger.id])


def _queue_compile(session: Session, trigger: Trigger, queue) -> None:
    """Mark the rule as compiling and queue it. With the broker down the trigger is kept as
    `failed`, so Compile again can be used once Redis is back."""
    trigger.compile_status = CompileStatus.PENDING
    trigger.compile_error = None
    trigger.enabled = False
    trigger.updated_at = datetime.now(UTC)
    session.commit()
    try:
        queue(trigger.id)
    except Exception as error:  # noqa: BLE001 - any broker failure
        trigger.compile_status = CompileStatus.FAILED
        trigger.compile_error = f"the compile queue is not reachable (is Redis running?): {error}"
        session.commit()


@router.post("", response_model=TriggerRead, status_code=201)
def create_trigger(
    body: TriggerCreate,
    session: Session = Depends(get_session),
    queue=Depends(get_trigger_queue),
) -> TriggerRead:
    """Stored disabled; the rule is compiled in the background (scope §12.1: test, then enable)."""
    trigger = Trigger(
        name=body.name,
        natural_language_rule=body.natural_language_rule,
        minimum_confidence=body.minimum_confidence,
        cooldown_seconds=body.cooldown_seconds,
        pre_roll_seconds=body.pre_roll_seconds,
        post_roll_seconds=body.post_roll_seconds,
        enabled=False,
        compile_status=CompileStatus.PENDING,
    )
    trigger.cameras = _cameras(session, body.camera_ids)
    session.add(trigger)
    session.commit()
    _queue_compile(session, trigger, queue)
    session.refresh(trigger)
    return _read_one(session, trigger)


@router.get("", response_model=list[TriggerRead])
def list_triggers(session: Session = Depends(get_session)) -> list[TriggerRead]:
    """Newest first, with each trigger's event counts."""
    triggers = list(
        session.scalars(select(Trigger).order_by(Trigger.created_at.desc(), Trigger.id.desc()))
    )
    counts = _event_counts(session, [t.id for t in triggers])
    return [_read(t, counts[t.id]) for t in triggers]


@router.get("/{trigger_id}", response_model=TriggerRead)
def get_trigger(trigger_id: UUID, session: Session = Depends(get_session)) -> TriggerRead:
    return _read_one(session, _get_trigger(session, trigger_id))


@router.patch("/{trigger_id}", response_model=TriggerRead)
def update_trigger(
    trigger_id: UUID,
    body: TriggerUpdate,
    session: Session = Depends(get_session),
    queue=Depends(get_trigger_queue),
) -> TriggerRead:
    """A changed rule is compiled again, and the trigger disabled until it is (ADR 0032)."""
    trigger = _get_trigger(session, trigger_id)
    fields = body.model_dump(exclude_unset=True)
    for name in ("name", "minimum_confidence", "cooldown_seconds"):
        if name in fields and fields[name] is not None:
            setattr(trigger, name, fields[name])
    for name in ("pre_roll_seconds", "post_roll_seconds"):
        if name in fields and fields[name] is not None:
            setattr(trigger, name, fields[name])
    if fields.get("camera_ids") is not None:
        trigger.cameras = _cameras(session, fields["camera_ids"])
    if fields.get("requires_vlm_verification") is not None:
        if not trigger.compiled_rule_json:
            raise HTTPException(409, "the rule isn't compiled yet")
        compiled = CompiledRule.model_validate(trigger.compiled_rule_json)
        compiled.requires_vlm_verification = fields["requires_vlm_verification"]
        compiled.verification_set_by_operator = True
        trigger.compiled_rule_json = compiled.model_dump(mode="json")
    rule = fields.get("natural_language_rule")
    reworded = rule is not None and rule != trigger.natural_language_rule
    if reworded:
        trigger.natural_language_rule = rule
    trigger.updated_at = datetime.now(UTC)
    session.commit()
    if reworded:
        _queue_compile(session, trigger, queue)
    session.refresh(trigger)
    return _read_one(session, trigger)


@router.post("/{trigger_id}/compile", response_model=TriggerRead, status_code=202)
def compile_again(
    trigger_id: UUID,
    session: Session = Depends(get_session),
    queue=Depends(get_trigger_queue),
) -> TriggerRead:
    """Compile the rule again (after a failure, or to pick up a newer compiler)."""
    trigger = _get_trigger(session, trigger_id)
    _queue_compile(session, trigger, queue)
    session.refresh(trigger)
    return _read_one(session, trigger)


@router.post("/{trigger_id}/enable", response_model=TriggerRead)
def enable_trigger(trigger_id: UUID, session: Session = Depends(get_session)) -> TriggerRead:
    trigger = _get_trigger(session, trigger_id)
    if trigger.compile_status == CompileStatus.PENDING:
        raise HTTPException(409, "the rule is still being compiled")
    if trigger.compile_status == CompileStatus.FAILED:
        raise HTTPException(409, "the rule couldn't be compiled; compile it again first")
    if not trigger.cameras:
        raise HTTPException(409, "assign at least one camera first")
    trigger.enabled = True
    trigger.updated_at = datetime.now(UTC)
    session.commit()
    return _read_one(session, trigger)


@router.post("/{trigger_id}/disable", response_model=TriggerRead)
def disable_trigger(trigger_id: UUID, session: Session = Depends(get_session)) -> TriggerRead:
    trigger = _get_trigger(session, trigger_id)
    trigger.enabled = False
    trigger.updated_at = datetime.now(UTC)
    session.commit()
    return _read_one(session, trigger)


@router.delete("/{trigger_id}", status_code=204)
def delete_trigger(trigger_id: UUID, session: Session = Depends(get_session)) -> Response:
    """The trigger, its camera assignments and its events with their evidence rows (ADR 0034).
    The events go first: `events.trigger_id` has no delete rule. Their footage is no longer
    protected afterwards."""
    trigger = _get_trigger(session, trigger_id)
    session.execute(delete(Event).where(Event.trigger_id == trigger.id))
    session.execute(delete(TriggerCamera).where(TriggerCamera.trigger_id == trigger.id))
    session.delete(trigger)
    session.commit()
    return Response(status_code=204)
