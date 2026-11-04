/**
 * Evidence marks (step 5.7, ADR 0038): where each track's box is at a moment, and how it is drawn.
 *
 * `trackBoxAt` follows the export's rule (backend shared/video/draw.py): between two points the box
 * slides linearly when they are at most MAX_STEP_MS apart (further apart, the object wasn't
 * followed in between, so nothing is drawn); just before the first point and after the last one
 * it is held for HOLD_MS. Colours match the export's.
 */
import type { EntityKind, MarkTrack, MarksState } from "./types";

export const MAX_STEP_MS = 600;
export const HOLD_MS = 150;
export const MARKS_POLL_MS = 3_000;

export const KIND_COLOUR: Record<EntityKind, string> = {
  person: "#ff8c00",
  vehicle: "#1eaaff",
  portable_object: "#ffd700",
  animal: "#ff6ec8",
};
export const OTHER_COLOUR = "#e6e6e6";
export const NOT_PICKED_COLOUR = "#969696";

export type Box = [number, number, number, number];

export interface ShownMark {
  id: number;
  label: string;
  colour: string;
  /** Picked, or no pick was made: thick with a label. Otherwise a thin grey box. */
  strong: boolean;
  box: Box;
}

/** The box of one run (points `[ms, x1, y1, x2, y2]` in time order) at `ms`, or null. */
export function trackBoxAt(points: [number, number, number, number, number][], ms: number): Box | null {
  if (points.length === 0) return null;
  let i = 0;
  while (i < points.length && points[i][0] <= ms) i++;
  if (i === 0) return points[0][0] - ms <= HOLD_MS ? (points[0].slice(1) as Box) : null;
  const last = points[points.length - 1];
  if (i === points.length) return ms - last[0] <= HOLD_MS ? (last.slice(1) as Box) : null;
  const a = points[i - 1];
  const b = points[i];
  if (b[0] - a[0] > MAX_STEP_MS) return null;
  const t = b[0] > a[0] ? (ms - a[0]) / (b[0] - a[0]) : 0;
  return [1, 2, 3, 4].map((k) => a[k] + (b[k] - a[k]) * t) as Box;
}

export function markColour(track: MarkTrack, strong: boolean): string {
  if (!strong) return NOT_PICKED_COLOUR;
  return track.kind ? KIND_COLOUR[track.kind] : OTHER_COLOUR;
}

/** Every track's box in `segmentId` at `ms` (milliseconds in the segment); strong ones last. */
export function boxesAt(marks: MarksState, segmentId: string, ms: number): ShownMark[] {
  const picking = marks.pick_status === "done";
  const shown: ShownMark[] = [];
  for (const track of marks.tracks) {
    for (const run of track.runs) {
      if (run.segment_id !== segmentId) continue;
      const box = trackBoxAt(run.points, ms);
      if (!box) continue;
      const strong = track.picked || !picking;
      shown.push({ id: track.id, label: track.label, colour: markColour(track, strong), strong, box });
    }
  }
  return shown.sort((a, b) => Number(a.strong) - Number(b.strong));
}

/** Where the picture is inside a box of `boxW` x `boxH` showing a `videoW` x `videoH` video with
 * `object-fit: contain` (black bars on two sides). */
export function pictureRect(
  boxW: number,
  boxH: number,
  videoW: number,
  videoH: number,
): { x: number; y: number; width: number; height: number } {
  if (!videoW || !videoH || !boxW || !boxH) return { x: 0, y: 0, width: boxW, height: boxH };
  const scale = Math.min(boxW / videoW, boxH / videoH);
  const width = videoW * scale;
  const height = videoH * scale;
  return { x: (boxW - width) / 2, y: (boxH - height) / 2, width, height };
}

/** One entry per label and colour, for the legend: the picked ones first. */
export function legend(marks: MarksState): { label: string; colour: string; strong: boolean }[] {
  const picking = marks.pick_status === "done";
  const seen = new Map<string, { label: string; colour: string; strong: boolean }>();
  for (const track of marks.tracks) {
    const strong = track.picked || !picking;
    const colour = markColour(track, strong);
    const key = `${track.label}|${colour}`;
    if (!seen.has(key)) seen.set(key, { label: track.label, colour, strong });
  }
  return [...seen.values()].sort((a, b) => Number(b.strong) - Number(a.strong));
}

export function marksBusy(marks: MarksState | undefined): boolean {
  return marks?.status === "pending" || marks?.status === "running";
}

/** The line under the player. */
export function marksWording(marks: MarksState | undefined): string {
  if (!marks) return "";
  if (marks.updating) return "Updating the marks…";
  switch (marks.status) {
    case "pending":
    case "running":
      return "Marking people and objects…";
    case "skipped":
      return marks.reason ?? "Marks are off.";
    case "failed":
      return `Marks couldn't be made: ${marks.reason ?? "unknown error"}`;
    case "done": {
      if (marks.tracks.length === 0) return "Nothing was found to mark.";
      const n = marks.tracks.length;
      const marked = `${n} ${n === 1 ? "person or object" : "people and objects"} marked`;
      if (marks.pick_status === "done") {
        const picked = marks.tracks.filter((t) => t.picked).length;
        return picked > 0
          ? `${marked}; the ones it is about are highlighted, the others grey`
          : `${marked}; none looked like what it is about, all grey`;
      }
      return marked;
    }
    default:
      return "";
  }
}

const VISIBLE_KEY = "sentinel.marks-visible";
const listeners = new Set<() => void>();

/** The Marks switch, remembered in this browser; on when storage is missing or blocked. */
export function readMarksVisible(): boolean {
  try {
    return window.localStorage.getItem(VISIBLE_KEY) !== "off";
  } catch {
    return true;
  }
}

export function writeMarksVisible(visible: boolean): void {
  try {
    window.localStorage.setItem(VISIBLE_KEY, visible ? "on" : "off");
  } catch {
    // not remembered
  }
  listeners.forEach((listener) => listener());
}

/** For useSyncExternalStore: every player follows the switch. */
export function subscribeMarksVisible(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
