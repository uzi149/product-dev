"""VLM verification of one evidence result (scope §10 steps 9-10, ADR 0029).

The frames are the keyframes already stored for the result's windows (step 3.3), so no video is
decoded: up to QUERY_VERIFY_FRAMES from its best-scoring windows first, in time order. The VLM
gets the question, the frames with their times (gaps marked) and the windows' descriptions, and
answers {"match", "confidence", "reason"}; an unusable answer gets one stricter retry.
"""

import logging
from dataclasses import dataclass

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.query_engine import verify_prompt
from services.query_engine.evidence import EvidenceResult
from shared.database.models import Keyframe
from shared.model_adapters import VisionLanguageModel
from shared.model_adapters.images import resize_max_side
from shared.model_adapters.json_output import extract_json
from shared.schemas.query import GapPart, VideoPart
from shared.storage import StorageClient

log = logging.getLogger(__name__)


class InvalidVerdict(Exception):
    """The VLM's answer had no usable match / confidence."""


@dataclass(frozen=True)
class Verdict:
    match: bool
    confidence: float
    reason: str
    attempts: int = 1


@dataclass(frozen=True)
class Frame:
    seconds: float  # from the start of the result
    storage_uri: str


def spread(items: list, count: int) -> list:
    """`count` items spread evenly over the list, first and last included (all if fewer)."""
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]


def choose_frames(session: Session, result: EvidenceResult, count: int) -> list[Frame]:
    """Up to `count` of the result's stored keyframes, in time order, from its best hits first.

    Windows are taken best score first (context neighbours last) until they give `count`
    keyframes inside the played parts. Spreading frames over a whole long result instead would
    leave a short event with one frame or none, and the VLM would reject a correct match."""
    parts = [p for p in result.manifest.parts if isinstance(p, VideoPart)]
    ranked = sorted(result.windows, key=lambda w: (w.score is None, -(w.score or 0), w.start))
    rows = session.execute(
        select(Keyframe.analysis_window_id, Keyframe.timestamp, Keyframe.storage_uri)
        .where(Keyframe.analysis_window_id.in_([w.id for w in ranked]))
        .order_by(Keyframe.timestamp)
    ).all()
    by_window: dict = {}
    for window_id, timestamp, uri in rows:
        if any(p.start_time <= timestamp <= p.end_time for p in parts):
            by_window.setdefault(window_id, []).append((timestamp, uri))
    chosen: dict = {}
    for window in ranked:
        if len(chosen) >= count:
            break
        for timestamp, uri in by_window.get(window.id, []):
            chosen.setdefault(timestamp, uri)  # overlapping windows can share a moment
    frames = [
        Frame((timestamp - result.start).total_seconds(), uri)
        for timestamp, uri in sorted(chosen.items())
    ]
    return spread(frames, count)


def gaps_of(result: EvidenceResult) -> list[tuple[float, float]]:
    """(start, length) in seconds from the result's start, of each gap part."""
    gaps, previous_end = [], None
    for part in result.manifest.parts:
        if isinstance(part, GapPart) and previous_end is not None:
            gaps.append(((previous_end - result.start).total_seconds(), part.duration_ms / 1000))
        elif isinstance(part, VideoPart):
            previous_end = part.end_time
    return gaps


def parse_verdict(text: str) -> Verdict:
    data = extract_json(text, expect=dict)
    if data is None:
        raise InvalidVerdict("no JSON object found")
    match = data.get("match")
    if isinstance(match, str) and match.lower() in ("true", "false"):
        match = match.lower() == "true"
    if not isinstance(match, bool):
        raise InvalidVerdict("'match' must be true or false")
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError) as error:
        raise InvalidVerdict("'confidence' must be a number") from error
    reason = str(data.get("reason") or "").strip()[:500]
    return Verdict(match, min(1.0, max(0.0, confidence)), reason)


def load_frames(storage: StorageClient, frames: list[Frame], max_side: int) -> list[np.ndarray]:
    pictures = []
    for frame in frames:
        data = storage.download_bytes(storage.key_from_uri(frame.storage_uri))
        picture = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if picture is not None:
            pictures.append(resize_max_side(picture, max_side))
    return pictures


def verify(
    vlm: VisionLanguageModel,
    session: Session,
    storage: StorageClient,
    result: EvidenceResult,
    question: str,
    frame_count: int,
    max_side: int,
) -> Verdict:
    """Ask the VLM about the result. Raises InvalidVerdict after two unusable answers; VLM errors
    (unavailable, quota, rejected) pass through."""
    frames = choose_frames(session, result, frame_count)
    if not frames:
        raise InvalidVerdict("the result has no stored keyframes to show the model")
    pictures = load_frames(storage, frames, max_side)
    descriptions = [
        (
            (w.start - result.start).total_seconds(),
            (w.end - result.start).total_seconds(),
            w.description,
        )
        for w in result.windows
        if w.description
    ]
    prompt = verify_prompt.verify_prompt(
        question, [f.seconds for f in frames], gaps_of(result), descriptions
    )
    ask = prompt
    for attempt in (1, 2):
        reply = vlm.complete(
            pictures,
            ask,
            system=verify_prompt.SYSTEM_PROMPT,
            max_tokens=verify_prompt.MAX_ANSWER_TOKENS,
        )
        try:
            verdict = parse_verdict(reply.text)
            return Verdict(verdict.match, verdict.confidence, verdict.reason, attempt)
        except InvalidVerdict as error:
            if attempt == 2:
                raise
            log.info("%s gave an unusable verdict (%s); asking again", reply.model, error)
            ask = verify_prompt.retry_prompt(str(error), prompt)
    raise AssertionError("unreachable")
