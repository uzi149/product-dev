"""Boxes from the vision-language model (step 5.7, ADR 0038): grounding and picking tracks.

Adapted from the reference repository's grounding detector and track matching:

- `grounding_prompt`: the reference wording, chosen there by measuring on Qwen2.5-VL (an explicit
  schema plus the "small, distant, partially occluded" hint finds the most instances; an "output
  [] if none" escape makes the model return empty lists far more often). Measure before changing.
- `parse_boxes`: JSON or, for a cut-off answer, a regex; boxes in the model's coordinate space
  (`bbox_space`) mapped to 0-1 of the picture; the model's repetition loops, near-duplicates and
  "group" boxes dropped.
- `pick_tracks`: each track is shown once (a few crops side by side, numbered), and one call per
  batch asks which candidates the trigger or question is about.

Everything here works on 0-1 boxes, so nothing depends on the size of the picture sent.
"""

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from shared.model_adapters.images import resize_max_side
from shared.model_adapters.json_output import extract_json
from shared.model_adapters.vlm import VisionLanguageModel

log = logging.getLogger(__name__)

PROMPT_VERSION = 1
GROUNDING_MAX_TOKENS = 1024
PICK_MAX_TOKENS = 60

Box = tuple[float, float, float, float]  # x1, y1, x2, y2, 0-1 of the picture

_BBOX_PATTERN = re.compile(
    r'"bbox_2d"\s*:\s*\[\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,'
    r'\s*([-\d.]+)\s*\]\s*,?\s*(?:"label"\s*:\s*"([^"]*)")?'
)

# Scene parts the model tends to "find" when asked for objects.
_NOT_OBJECTS = frozenset(
    [
        "wall", "floor", "ceiling", "window", "road", "ground", "sky", "grass", "room",
        "building", "hair", "face", "head", "hand", "shirt", "clothing", "shadow", "light",
        "background",
    ]
)  # fmt: skip

NMS_IOU = 0.6  # same-label boxes overlapping more are one
_CROSS_LABEL_IOU = 0.75  # a person in front of a car is two objects
_REPETITION_MIN_RUN = 5
_MIN_SIDE = 0.004  # boxes thinner than this share of the picture are noise
_GROUP_CONTAINMENT = 0.9


@dataclass(frozen=True)
class Detection:
    label: str
    box: Box


def bbox_space(model: str, setting: str = "auto") -> str:
    """Coordinate space of the model's boxes: "pixel" or "norm1000".

    Qwen2/2.5-VL answer in pixels of the picture they were sent; Qwen3 and later on a 0-1000
    grid whatever the picture's size.
    """
    if setting in ("pixel", "norm1000"):
        return setting
    name = model.lower()
    return "pixel" if ("qwen2" in name or "qwen-2" in name) else "norm1000"


def grounding_prompt(labels: Sequence[str]) -> str:
    """Ask for every instance of `labels`; with none, every distinct object."""
    if labels:
        task = f"Locate every instance of the following objects in the image: {', '.join(labels)}."
        names = "Use the object names given above as labels."
    else:
        task = (
            "Locate every distinct object in the image (people, vehicles, animals and other "
            "salient objects)."
        )
        names = "Use short, common, singular nouns as labels."
    return (
        f"{task} Include small, distant and partially occluded ones, one box per individual. "
        'Output their bbox coordinates in JSON format as a list of {"bbox_2d": [x1, y1, x2, y2], '
        f'"label": "<object name>"}}. {names}'
    )


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _raw_boxes(text: str) -> list[tuple[list, str]]:
    parsed = extract_json(text, expect=list)
    found = []
    if isinstance(parsed, list):
        found = [
            (item["bbox_2d"], str(item.get("label") or "object"))
            for item in parsed
            if isinstance(item, dict)
            and isinstance(item.get("bbox_2d"), list)
            and len(item["bbox_2d"]) == 4
        ]
    if found:
        return found
    # Cut off or slightly broken (the first complete list may then be one box's numbers): keep
    # what can be read.
    return [
        ([float(v) for v in m.groups()[:4]], m.group(5) or "object")
        for m in _BBOX_PATTERN.finditer(text)
    ]


def parse_boxes(
    text: str, width: int, height: int, space: str, wanted: Sequence[str] = ()
) -> list[Detection]:
    """The model's boxes as 0-1 detections. `width` x `height` is the picture as sent (used for
    "pixel" answers). Scene parts (wall, hair…) are dropped unless they were asked for."""
    scale_x, scale_y = (1000.0, 1000.0) if space == "norm1000" else (float(width), float(height))
    boxes: list[tuple[Box, str]] = []
    for raw, label in _raw_boxes(text):
        try:
            x1, y1, x2, y2 = (float(v) for v in raw)
        except (TypeError, ValueError):
            continue
        box = (
            min(1.0, max(0.0, x1 / scale_x)),
            min(1.0, max(0.0, y1 / scale_y)),
            min(1.0, max(0.0, x2 / scale_x)),
            min(1.0, max(0.0, y2 / scale_y)),
        )
        boxes.append((box, label.strip().lower() or "object"))
    boxes = drop_repetition_runs(boxes, _REPETITION_MIN_RUN)
    asked = {w.lower() for w in wanted}
    detections = [
        Detection(label, box)
        for box, label in boxes
        if box[2] - box[0] >= _MIN_SIDE
        and box[3] - box[1] >= _MIN_SIDE
        and (label not in _NOT_OBJECTS or label in asked)
    ]
    return drop_group_boxes(nms(detections, NMS_IOU))


def drop_repetition_runs(boxes: list[tuple[Box, str]], min_run: int) -> list[tuple[Box, str]]:
    """Drop the model's repetition loops.

    A degenerating model keeps writing same-sized boxes that tile a row or a column edge to edge
    (14 "chair" boxes marching across a room), usually with a few stray boxes in the same band. A
    run of `min_run` or more tiling boxes is removed with every other box filling that band.
    """
    if min_run <= 0 or len(boxes) < min_run:
        return boxes
    tol = 0.02

    def similar(a: float, b: float) -> bool:
        return a > 0 and b > 0 and 0.6 <= a / b <= 1.7

    def tiling(a: Box, b: Box) -> str | None:
        if (
            abs(a[1] - b[1]) <= tol
            and abs(a[3] - b[3]) <= tol
            and abs(b[0] - a[2]) <= tol
            and similar(b[2] - b[0], a[2] - a[0])
        ):
            return "row"
        if (
            abs(a[0] - b[0]) <= tol
            and abs(a[2] - b[2]) <= tol
            and abs(b[1] - a[3]) <= tol
            and similar(b[3] - b[1], a[3] - a[1])
        ):
            return "col"
        return None

    runs: list[tuple[int, int, str]] = []
    start, axis = 0, None
    for i in range(1, len(boxes) + 1):
        link = None
        if i < len(boxes) and boxes[i][1] == boxes[i - 1][1]:
            link = tiling(boxes[i - 1][0], boxes[i][0])
        if link is not None and axis in (None, link):
            axis = link
            continue
        if axis is not None and i - start >= min_run:
            runs.append((start, i, axis))
        # The box that broke the run may start a new one with the box before it.
        start, axis = (i - 1, link) if link is not None else (i, None)

    if not runs:
        return boxes
    drop: set[int] = set()
    for s, e, run_axis in runs:
        drop.update(range(s, e))
        run = [boxes[k][0] for k in range(s, e)]
        lo = min(b[0] if run_axis == "row" else b[1] for b in run)
        hi = max(b[2] if run_axis == "row" else b[3] for b in run)
        band = np.mean(run, axis=0)
        for k, (b, _) in enumerate(boxes):
            if run_axis == "row":
                in_band = abs(b[1] - band[1]) <= tol and abs(b[3] - band[3]) <= tol
                centre = (b[0] + b[2]) / 2
            else:
                in_band = abs(b[0] - band[0]) <= tol and abs(b[2] - band[2]) <= tol
                centre = (b[1] + b[3]) / 2
            if in_band and lo <= centre <= hi:
                drop.add(k)
    return [box for k, box in enumerate(boxes) if k not in drop]


def nms(detections: list[Detection], threshold: float) -> list[Detection]:
    """Drop duplicates: the same label above `threshold`; different labels on the same pixels
    (the model writing both "person" and "vehicle") only above a stricter one."""
    cross = max(threshold, _CROSS_LABEL_IOU)
    kept: list[Detection] = []
    for det in detections:
        if all(iou(k.box, det.box) < (threshold if k.label == det.label else cross) for k in kept):
            kept.append(det)
    return kept


def drop_group_boxes(detections: list[Detection]) -> list[Detection]:
    """Drop boxes enclosing two or more boxes of the same label (the model's "group" boxes)."""

    def contains(outer: Box, inner: Box) -> bool:
        ix1, iy1 = max(outer[0], inner[0]), max(outer[1], inner[1])
        ix2, iy2 = min(outer[2], inner[2]), min(outer[3], inner[3])
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        area = (inner[2] - inner[0]) * (inner[3] - inner[1])
        return area > 0 and inter / area >= _GROUP_CONTAINMENT

    return [
        det
        for i, det in enumerate(detections)
        if sum(
            1
            for j, other in enumerate(detections)
            if i != j and other.label == det.label and contains(det.box, other.box)
        )
        < 2
    ]


def ground(
    vlm: VisionLanguageModel,
    frame: np.ndarray,
    labels: Sequence[str],
    *,
    image_max_side: int,
    bbox_format: str = "auto",
) -> list[Detection]:
    """The boxes of `labels` in one BGR frame. Raises the VLM's errors."""
    sent = resize_max_side(frame, image_max_side)
    height, width = sent.shape[:2]
    reply = vlm.complete(
        [frame],
        grounding_prompt(labels),
        max_tokens=GROUNDING_MAX_TOKENS,
        image_max_side=image_max_side,
    )
    log.debug("grounding answer: %s", reply.text)
    return parse_boxes(reply.text, width, height, bbox_space(vlm.name, bbox_format), labels)


# ---------------------------------------------------------------- picking tracks

_CROP_PAD = 0.15
_CROP_SIDE = 448  # longest side of one view, in px
_GAP = 8  # px between the views of one candidate
PICK_BATCH = 12
PICK_VIEWS = 2


def pick_views(
    sightings: Sequence[tuple[int, Box]], n_views: int = PICK_VIEWS
) -> list[tuple[int, Box]]:
    """The clearest sightings of a track, spread in time: the largest box (the closest look),
    then other large ones as far as possible from those already chosen."""

    def area(b: Box) -> float:
        return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    if not sightings:
        return []
    ranked = sorted(sightings, key=lambda s: area(s[1]), reverse=True)
    views = [ranked[0]]
    big_enough = [s for s in ranked[1:] if area(s[1]) >= 0.6 * area(ranked[0][1])]
    while big_enough and len(views) < n_views:
        far = max(big_enough, key=lambda s: min(abs(s[0] - f) for f, _ in views))
        views.append(far)
        big_enough.remove(far)
    return sorted(views)


def crop(frame: np.ndarray, box: Box) -> np.ndarray | None:
    """The box with a margin, at most _CROP_SIDE px on its longest side."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box[0] * w, box[1] * h, box[2] * w, box[3] * h
    px, py = (x2 - x1) * _CROP_PAD, (y2 - y1) * _CROP_PAD
    ix1, iy1 = max(0, int(x1 - px)), max(0, int(y1 - py))
    ix2, iy2 = min(w, int(x2 + px)), min(h, int(y2 + py))
    if ix2 - ix1 < 4 or iy2 - iy1 < 4:
        return None
    return resize_max_side(frame[iy1:iy2, ix1:ix2], _CROP_SIDE)


def candidate_image(views: list[np.ndarray], number: int) -> np.ndarray:
    """Views side by side, with the candidate number drawn in the corner."""
    height = max(v.shape[0] for v in views)
    width = sum(v.shape[1] for v in views) + _GAP * (len(views) - 1)
    sheet = np.zeros((height, width, 3), np.uint8)
    x = 0
    for view in views:
        sheet[: view.shape[0], x : x + view.shape[1]] = view
        x += view.shape[1] + _GAP
    label = str(number)
    scale = max(0.8, height / 300)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    cv2.rectangle(sheet, (0, 0), (tw + 12, th + 14), (0, 255, 255), -1)
    cv2.putText(sheet, label, (6, th + 7), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 2)
    return sheet


def pick_prompt(question: str, n: int, multi_view: bool) -> str:
    """The reference's selection prompt, asking which candidates the trigger or question is about
    (the reference asked which match a description of one object)."""
    views = (
        " Panels side by side in one image are the same candidate at different moments."
        if multi_view
        else ""
    )
    return (
        f"These are people and objects seen in security camera footage. Each of the {n} images "
        f"shows one candidate, numbered 1 to {n} in the order given (the number is also drawn in "
        f'the corner).{views} The footage was found for this: "{question}". Which candidates '
        "is it about: the people or objects that take part in it? Look closely at each one. "
        "Answer only with a JSON list of their numbers, e.g. [2] or [1, 3], and [] if none."
    )


def parse_pick(text: str, n: int) -> list[int] | None:
    """1-based candidate numbers from the answer; None if it can't be read."""
    match = re.search(r"\[([^\[\]]*)\]", text)
    if not match:
        return None
    picked: list[int] = []
    for token in re.findall(r"-?\d+", match.group(1)):
        k = int(token)
        if 1 <= k <= n and k not in picked:
            picked.append(k)
    return picked


def pick_tracks(
    vlm: VisionLanguageModel,
    question: str,
    candidates: dict[int, list[tuple[int, Box]]],
    frame_of: Callable[[int], np.ndarray | None],
) -> set[int] | None:
    """The track ids the question is about. `candidates`: track id -> (keyframe, box) sightings;
    `frame_of(keyframe)` gives that keyframe's picture. None when an answer can't be used (every
    track is then shown the same)."""
    crops: list[tuple[int, list[np.ndarray]]] = []
    for track_id, sightings in candidates.items():
        views = []
        for keyframe, box in pick_views(sightings):
            frame = frame_of(keyframe)
            if frame is not None and (c := crop(frame, box)) is not None:
                views.append(c)
        if views:
            crops.append((track_id, views))
    if not crops:
        return set()
    picked: set[int] = set()
    for start in range(0, len(crops), PICK_BATCH):
        batch = crops[start : start + PICK_BATCH]
        images = [candidate_image(views, k) for k, (_, views) in enumerate(batch, 1)]
        prompt = pick_prompt(question, len(batch), any(len(v) > 1 for _, v in batch))
        try:
            reply = vlm.complete(
                images, prompt, max_tokens=PICK_MAX_TOKENS, image_max_side=_CROP_SIDE * 2
            )
        except Exception as error:  # noqa: BLE001 - any failure: show every track the same
            log.warning("picking tracks failed: %s", error)
            return None
        numbers = parse_pick(reply.text, len(batch))
        if numbers is None:
            log.warning("unreadable pick answer: %r", reply.text[:200])
            return None
        picked.update(batch[k - 1][0] for k in numbers)
    return picked
