import { describe, expect, it } from "vitest";
import {
  fitRange,
  isValidDay,
  layoutBlocks,
  localDay,
  MIN_WIDTH_PERCENT,
  rangeFor,
  shiftDay,
  ticks,
  totals,
} from "./timeline";
import type { Segment, StaticPeriod } from "./types";

const T0 = new Date(2026, 2, 7, 9, 0, 0).getTime(); // 09:00 local
const minutes = (n: number) => n * 60_000;
const iso = (ms: number) => new Date(ms).toISOString();

function segment(id: string, from: number, to: number): Segment {
  return {
    id,
    camera_id: "c",
    start_time: iso(from),
    end_time: iso(to),
    duration_ms: to - from,
    activity_score: 0.01,
    status: "pending",
    codec: "h264",
    width: 640,
    height: 360,
    created_at: iso(to),
  };
}

function period(id: string, from: number, to: number, health: "ok" | "offline" = "ok"): StaticPeriod {
  return {
    id,
    camera_id: "c",
    start_time: iso(from),
    end_time: iso(to),
    health_status: health,
    has_reference_frame: health === "ok",
  };
}

const HOUR_RANGE = { start: T0, end: T0 + minutes(60) };

describe("layoutBlocks", () => {
  it("places blocks in % of the range, segments on top", () => {
    const blocks = layoutBlocks(
      HOUR_RANGE,
      [segment("s", T0 + minutes(15), T0 + minutes(30))],
      [period("p", T0, T0 + minutes(15)), period("o", T0 + minutes(30), T0 + minutes(45), "offline")],
    );
    expect(blocks.map((b) => [b.id, b.kind, b.left, b.width])).toEqual([
      ["p", "static", 0, 25],
      ["o", "offline", 50, 25],
      ["s", "segment", 25, 25],
    ]);
  });

  it("clamps blocks to the range and drops the ones outside it", () => {
    const blocks = layoutBlocks(
      HOUR_RANGE,
      [segment("before", T0 - minutes(30), T0 - minutes(10)), segment("edge", T0 - minutes(5), T0 + minutes(6))],
      [period("after", T0 + minutes(50), T0 + minutes(90))],
    );
    expect(blocks.map((b) => b.id)).toEqual(["after", "edge"]);
    const edge = blocks.find((b) => b.id === "edge")!;
    expect(edge.left).toBe(0);
    expect(edge.width).toBeCloseTo(10);
    const after = blocks.find((b) => b.id === "after")!;
    expect(after.left + after.width).toBeCloseTo(100);
  });

  it("keeps very short blocks visible and inside the bar", () => {
    const day = { start: T0, end: T0 + minutes(24 * 60) };
    const [tiny] = layoutBlocks(day, [segment("s", day.end - 20_000, day.end)], []);
    expect(tiny.width).toBe(MIN_WIDTH_PERCENT);
    expect(tiny.left + tiny.width).toBeCloseTo(100);
  });
});

describe("ranges", () => {
  const now = new Date(T0 + minutes(60));

  it("rolling presets end now, or whole spans earlier", () => {
    expect(rangeFor("1h", now, 0, "")).toEqual(HOUR_RANGE);
    expect(rangeFor("6h", now, 1, "")).toEqual({ start: T0 - minutes(660), end: T0 - minutes(300) });
  });

  it("a day is local midnight to midnight", () => {
    const range = rangeFor("day", now, 0, "2026-03-07");
    expect(new Date(range.start).getHours()).toBe(0);
    expect(localDay(new Date(range.start))).toBe("2026-03-07");
    expect(localDay(new Date(range.end))).toBe("2026-03-08");
    expect(shiftDay("2026-03-01", -1)).toBe("2026-02-28");
  });
});

describe("ticks", () => {
  it("falls on round local times", () => {
    const marks = ticks({ start: T0 + minutes(7), end: T0 + minutes(67) });
    expect(marks.length).toBeLessThanOrEqual(7);
    expect(marks.every((m) => new Date(m.at).getMinutes() % 10 === 0)).toBe(true);
    expect(marks[0].left).toBeGreaterThan(0);
  });

  it("uses wider steps for longer ranges", () => {
    const marks = ticks({ start: T0, end: T0 + minutes(24 * 60) });
    expect(marks.length).toBeLessThanOrEqual(7);
    expect(marks[1].at - marks[0].at).toBe(minutes(360));
  });
});

describe("totals", () => {
  it("adds up stored and offline seconds inside the range", () => {
    const blocks = layoutBlocks(
      HOUR_RANGE,
      [segment("a", T0 - minutes(1), T0 + minutes(1)), segment("b", T0 + minutes(10), T0 + minutes(11))],
      [period("o", T0 + minutes(20), T0 + minutes(25), "offline")],
    );
    expect(totals(HOUR_RANGE, blocks)).toEqual({ stored: 120, offline: 300 });
  });
});

describe("fitRange", () => {
  it("zooms to short footage with at least a minute shown", () => {
    const items = {
      segments: [segment("s", T0 + 5_000, T0 + 31_000)],
      periods: [period("a", T0, T0 + 5_000), period("b", T0 + 31_000, T0 + 40_000)],
    };
    const range = fitRange(items.segments, items.periods)!;
    expect(range.start).toBeLessThan(T0);
    expect(range.end).toBeGreaterThan(T0 + 40_000);
    expect(range.end - range.start).toBeCloseTo(60_000);
    const blocks = layoutBlocks(range, items.segments, items.periods);
    expect(blocks.find((b) => b.id === "a")!.width).toBeGreaterThan(5); // wide enough to click
  });

  it("is null without footage", () => {
    expect(fitRange([], [])).toBeNull();
  });

  it("gives long footage 4 % room on each side", () => {
    const range = fitRange([segment("s", T0, T0 + minutes(100))], [])!;
    expect(range.start).toBe(T0 - minutes(4));
  });
});

describe("short ranges", () => {
  it("tick every few seconds, with seconds in the label", () => {
    const marks = ticks({ start: T0, end: T0 + 60_000 });
    expect(marks[1].at - marks[0].at).toBe(10_000);
    expect(marks[0].label).toMatch(/\d{2}\D\d{2}\D\d{2}/);
  });
});

describe("isValidDay", () => {
  it("accepts a calendar day", () => {
    expect(isValidDay("2026-03-07")).toBe(true);
    expect(isValidDay("2028-02-29")).toBe(true);
  });

  it("rejects what a date input can hold but the timeline can't show", () => {
    expect(isValidDay("")).toBe(false);
    expect(isValidDay("275760-09-13")).toBe(false);
    expect(isValidDay("0050-01-01")).toBe(false);
    expect(isValidDay("2026-02-30")).toBe(false);
  });
});
