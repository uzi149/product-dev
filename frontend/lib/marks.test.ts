import { describe, expect, it } from "vitest";
import {
  HOLD_MS,
  KIND_COLOUR,
  NOT_PICKED_COLOUR,
  boxesAt,
  legend,
  marksBusy,
  marksWording,
  pictureRect,
  trackBoxAt,
} from "./marks";
import type { MarkTrack, MarksState } from "./types";

const SEGMENT = "seg-1";

function track(id: number, label: string, kind: MarkTrack["kind"], picked: boolean, xs: number[]): MarkTrack {
  return {
    id,
    label,
    kind,
    picked,
    runs: [{ segment_id: SEGMENT, points: xs.map((x, n) => [n * 200, x, 0.2, x + 0.1, 0.6]) }],
  };
}

function marks(tracks: MarkTrack[], pick: MarksState["pick_status"] = "none"): MarksState {
  return { status: "done", reason: null, updating: false, pick_status: pick, tracks };
}

describe("marks", () => {
  it("slides a box between two points", () => {
    const points: [number, number, number, number, number][] = [
      [0, 0, 0, 0.2, 0.2],
      [200, 0.2, 0, 0.4, 0.2],
    ];
    expect(trackBoxAt(points, 100)).toEqual([0.1, 0, 0.30000000000000004, 0.2]);
    expect(trackBoxAt(points, 0)).toEqual([0, 0, 0.2, 0.2]);
  });

  it("holds the ends briefly and draws nothing outside the track", () => {
    const points: [number, number, number, number, number][] = [
      [1000, 0.1, 0.1, 0.2, 0.2],
      [1200, 0.1, 0.1, 0.2, 0.2],
    ];
    expect(trackBoxAt(points, 1000 - HOLD_MS)).not.toBeNull();
    expect(trackBoxAt(points, 1000 - HOLD_MS - 1)).toBeNull();
    expect(trackBoxAt(points, 1200 + HOLD_MS)).not.toBeNull();
    expect(trackBoxAt(points, 1200 + HOLD_MS + 1)).toBeNull();
  });

  it("draws nothing across a long break in a track", () => {
    const points: [number, number, number, number, number][] = [
      [0, 0.1, 0.1, 0.2, 0.2],
      [2000, 0.5, 0.1, 0.6, 0.2],
    ];
    expect(trackBoxAt(points, 1000)).toBeNull();
  });

  it("shows only the segment playing, picked tracks strong and on top", () => {
    const state = marks(
      [track(1, "bag", "portable_object", true, [0.5, 0.5]), track(2, "person", "person", false, [0.1, 0.2])],
      "done",
    );
    const shown = boxesAt(state, SEGMENT, 100);
    expect(shown.map((s) => [s.label, s.strong, s.colour])).toEqual([
      ["person", false, NOT_PICKED_COLOUR],
      ["bag", true, KIND_COLOUR.portable_object],
    ]);
    expect(boxesAt(state, "other-segment", 100)).toEqual([]);
  });

  it("without a pick every track is strong", () => {
    const state = marks([track(1, "person", "person", false, [0.1, 0.2])], "failed");
    expect(boxesAt(state, SEGMENT, 0)[0].strong).toBe(true);
  });

  it("finds the picture inside the black bars", () => {
    // A 4:3 video in a 16:9 box: bars left and right.
    expect(pictureRect(1600, 900, 640, 480)).toEqual({ x: 200, y: 0, width: 1200, height: 900 });
    // A 16:9 video in a taller box: bars above and below.
    expect(pictureRect(800, 600, 1920, 1080)).toEqual({ x: 0, y: 75, width: 800, height: 450 });
  });

  it("builds a legend, picked first", () => {
    const state = marks(
      [
        track(1, "person", "person", false, [0.1]),
        track(2, "person", "person", false, [0.3]),
        track(3, "bag", "portable_object", true, [0.5]),
      ],
      "done",
    );
    expect(legend(state)).toEqual([
      { label: "bag", colour: KIND_COLOUR.portable_object, strong: true },
      { label: "person", colour: NOT_PICKED_COLOUR, strong: false },
    ]);
  });

  it("words the state", () => {
    const base = marks([]);
    expect(marksWording({ ...base, status: "running" })).toBe("Marking people and objects…");
    expect(marksWording({ ...base, status: "running", updating: true })).toBe("Updating the marks…");
    expect(marksWording({ ...base, status: "skipped", reason: "Marks are off with the local model." })).toBe(
      "Marks are off with the local model.",
    );
    expect(marksWording({ ...base, status: "failed", reason: "daily limit" })).toBe(
      "Marks couldn't be made: daily limit",
    );
    expect(marksWording(base)).toBe("Nothing was found to mark.");
    expect(marksWording(marks([track(1, "person", "person", true, [0.1])], "done"))).toBe(
      "1 person or object marked; the ones it is about are highlighted, the others grey",
    );
    expect(marksBusy({ ...base, status: "pending" })).toBe(true);
    expect(marksBusy(base)).toBe(false);
  });
});
