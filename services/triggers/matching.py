"""A compiled rule against analysed windows (scope §12.3, step 5.2, ADR 0033): no database, no
models, so live evaluation and trigger tests (5.4) share it.

Structured rules: the required actions in order on one camera's timeline, each step at most the
rule's gap after the previous one, the last step in the window being evaluated (so a match is
reported once, by the window that completes it). Windows overlap by a few seconds, so the same
action can be seen by two windows: a copy within the overlap counts once.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from shared.schemas.entities import entity_class
from shared.schemas.observation import ACTION_VOCABULARY


@dataclass(frozen=True)
class TimedAction:
    type: str
    start: datetime
    end: datetime
    window_id: UUID


@dataclass(frozen=True)
class WindowFacts:
    """What the analysis saw in one window, in absolute time."""

    id: UUID
    segment_id: UUID
    start: datetime
    end: datetime
    description: str | None
    confidence: float | None
    actions: tuple[TimedAction, ...]
    classes: frozenset[str]  # entity classes of its entities and visible objects


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def window_facts(
    window_id: UUID,
    segment_id: UUID,
    start: datetime,
    end: datetime,
    description: str | None,
    confidence: float | None,
    observation: dict[str, Any] | None,
) -> WindowFacts:
    observation = observation or {}
    length = (end - start).total_seconds()
    actions = []
    for action in observation.get("actions") or []:
        if not isinstance(action, dict) or action.get("type") not in ACTION_VOCABULARY:
            continue
        begin = min(_seconds(action.get("start_second")) or 0.0, length)
        finish = min(max(_seconds(action.get("end_second")) or begin, begin), length)
        actions.append(
            TimedAction(
                action["type"],
                start + timedelta(seconds=begin),
                start + timedelta(seconds=finish),
                window_id,
            )
        )
    types = [e.get("type") for e in observation.get("entities") or [] if isinstance(e, dict)]
    types += list(observation.get("visible_objects") or [])
    classes = {entity_class(t) for t in types if isinstance(t, str)}
    classes.discard(None)
    return WindowFacts(
        window_id,
        segment_id,
        start,
        end,
        description,
        confidence,
        tuple(sorted(actions, key=lambda a: a.start)),
        frozenset(classes),
    )


def timeline(windows: Sequence[WindowFacts], overlap_seconds: float) -> list[TimedAction]:
    """The windows' actions in time order. An action that an earlier window already reported
    within `overlap_seconds` (the same moment seen by two overlapping windows) counts once."""
    tolerance = timedelta(seconds=overlap_seconds)
    kept: list[TimedAction] = []
    for window in sorted(windows, key=lambda w: (w.start, w.end)):
        for action in window.actions:
            seen = any(
                k.type == action.type
                and k.window_id != action.window_id
                and abs(k.start - action.start) <= tolerance
                for k in kept
            )
            if not seen:
                kept.append(action)
    return sorted(kept, key=lambda a: (a.start, a.end))


def _steps_before(
    actions: list[TimedAction], steps: Sequence[str], later: TimedAction, gap: timedelta
) -> list[TimedAction] | None:
    if not steps:
        return []
    candidates = [
        a
        for a in actions
        if a.type == steps[-1]
        and a is not later
        and a.start <= later.start
        and later.start - a.end <= gap
    ]
    for action in sorted(candidates, key=lambda a: a.start, reverse=True):  # closest first
        rest = _steps_before(actions, steps[:-1], action, gap)
        if rest is not None:
            return [*rest, action]
    return None


def match_sequence(
    actions: list[TimedAction],
    sequence: Sequence[str],
    gap_seconds: float,
    last_in: UUID,
) -> list[TimedAction] | None:
    """The actions that make up `sequence` in order, each at most `gap_seconds` after the end
    of the previous one, the last one in window `last_in`; None when there is no such chain."""
    if not sequence:
        return None
    gap = timedelta(seconds=gap_seconds)
    finals = [a for a in actions if a.type == sequence[-1] and a.window_id == last_in]
    for final in finals:
        chain = _steps_before(actions, sequence[:-1], final, gap)
        if chain is not None:
            return [*chain, final]
    return None


def classes_seen(windows: Iterable[WindowFacts], start: datetime, end: datetime) -> set[str]:
    """The entity classes of the windows that overlap [start, end]."""
    return {c for w in windows if w.start <= end and w.end >= start for c in w.classes}
