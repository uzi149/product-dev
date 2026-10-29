"""Ground truth of a known video, and the rules that compare it with what the pipeline found.

A YAML file per video (scope §15, MVP plan §5):

    video: ../../virat/motion/VIRAT_S_050300_00_000000_000067.mp4   # relative to the YAML file
    start_time: 2026-03-07T09:00:00Z                                 # what the upload uses
    entities: [person, car]
    events:
      - action: run
        start_s: 15
        end_s: 36
        also: [walk]               # optional: other words accepted for this action
    queries:
      - text: "a person runs across the parking lot"
        start_s: 15                # the interval a top-3 hit must overlap
        end_s: 36
        known_miss: "…"            # optional: the VLM is known not to see it; reported, not failed
    actions_needed: 0.8            # optional: share of the actions that must be found

Times are seconds from the start of the video.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

# An action counts as found when its time overlaps the ground truth within this many seconds.
ACTION_SLACK_SECONDS = 2.0
TOP = 3
# Share of a video set's actions that must be found (the MVP quality target). A ground-truth file
# may set its own `actions_needed` (the VIRAT clips do, ADR 0023 amendment).
ACTIONS_NEEDED = 0.8

# Words a VLM may use for the same kind of thing. The plural and "people" folding follow the
# reference code's object-name matching.
SYNONYMS = {
    "person": {"person", "man", "woman", "people", "pedestrian", "child", "worker", "individual"},
    "car": {"car", "vehicle", "truck", "van", "suv", "pickup", "pickup_truck", "sedan", "bus"},
    "vehicle": {"vehicle", "car", "truck", "van", "suv", "pickup", "sedan", "bus"},
    "bag": {"bag", "backpack", "suitcase", "handbag", "box", "package", "luggage", "object"},
    "box": {"box", "package", "carton", "bag", "object"},
    "bicycle": {"bicycle", "bike"},
}


@dataclass(frozen=True)
class Event:
    action: str
    start_s: float
    end_s: float
    also: tuple[str, ...] = ()

    @property
    def words(self) -> set[str]:
        return {self.action, *self.also}


@dataclass(frozen=True)
class Query:
    text: str
    start_s: float
    end_s: float
    # Why the VLM is known not to describe this (ADR 0023 amendment): reported, not a failure.
    known_miss: str | None = None


@dataclass
class GroundTruth:
    path: Path
    video: Path
    start_time: datetime
    entities: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    queries: list[Query] = field(default_factory=list)
    actions_needed: float = ACTIONS_NEEDED

    @property
    def name(self) -> str:
        return self.path.stem


def load(path: Path) -> GroundTruth:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    start = data["start_time"]
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
    return GroundTruth(
        path=path,
        video=(path.parent / data["video"]).resolve(),
        start_time=start,
        entities=[str(e).lower() for e in data.get("entities") or []],
        events=[
            Event(e["action"], float(e["start_s"]), float(e["end_s"]), tuple(e.get("also") or ()))
            for e in data.get("events") or []
        ],
        queries=[
            Query(q["text"], float(q["start_s"]), float(q["end_s"]), q.get("known_miss"))
            for q in data.get("queries") or []
        ],
        actions_needed=float(data.get("actions_needed", ACTIONS_NEEDED)),
    )


def load_folder(folder: Path) -> list[GroundTruth]:
    return [load(path) for path in sorted(folder.glob("*.yaml"))]


def singular(word: str) -> str:
    word = word.strip().lower().replace(" ", "_")
    if word in {"people", "persons"}:
        return "person"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def same_entity(expected: str, found: str) -> bool:
    expected, found = singular(expected), singular(found)
    return found == expected or found in SYNONYMS.get(expected, set())


def missing_entities(expected: Iterable[str], found: Iterable[str]) -> list[str]:
    found = list(found)
    return [e for e in expected if not any(same_entity(e, f) for f in found)]


def overlaps(a_start: float, a_end: float, b_start: float, b_end: float, slack: float = 0) -> bool:
    return a_start < b_end + slack and b_start < a_end + slack


@dataclass(frozen=True)
class FoundAction:
    """An action the VLM reported, in seconds from the start of the video."""

    type: str
    start_s: float
    end_s: float


def action_found(event: Event, found: Iterable[FoundAction]) -> bool:
    return any(
        action.type in event.words
        and overlaps(event.start_s, event.end_s, action.start_s, action.end_s, ACTION_SLACK_SECONDS)
        for action in found
    )


def query_passes(query: Query, hits: list[tuple[float, float]]) -> bool:
    """`hits` are the (start, end) seconds of the ranked windows, best first."""
    return any(overlaps(query.start_s, query.end_s, s, e) for s, e in hits[:TOP])
