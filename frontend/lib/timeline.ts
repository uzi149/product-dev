import type { Segment, StaticPeriod } from "./types";

/** Epoch milliseconds. */
export interface TimeRange {
  start: number;
  end: number;
}

export type BlockKind = "segment" | "static" | "offline";

export interface TimelineBlock {
  id: string;
  kind: BlockKind;
  start: number;
  end: number;
  /** Position and width in % of the range. */
  left: number;
  width: number;
}

/** A block this narrow (in % of the bar) is still visible and clickable. */
export const MIN_WIDTH_PERCENT = 0.4;

/** "fit" zooms to the footage of the day `day`, which suits short uploaded clips. */
export type Preset = "1h" | "6h" | "24h" | "day" | "fit";

const HOUR = 3_600_000;
const SPAN: Record<"1h" | "6h" | "24h", number> = { "1h": HOUR, "6h": 6 * HOUR, "24h": 24 * HOUR };

/**
 * The range to load. Rolling presets end `back` spans before `now`; "day" and "fit" load the local
 * calendar day `day` (YYYY-MM-DD).
 */
export function rangeFor(preset: Preset, now: Date, back: number, day: string): TimeRange {
  if (preset === "day" || preset === "fit") {
    const [y, m, d] = day.split("-").map(Number);
    const start = new Date(y, m - 1, d).getTime();
    return { start, end: new Date(y, m - 1, d + 1).getTime() };
  }
  const end = now.getTime() - back * SPAN[preset];
  return { start: end - SPAN[preset], end };
}

/** YYYY-MM-DD of a date in local time (for <input type="date">). */
export function localDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function shiftDay(day: string, days: number): string {
  const [y, m, d] = day.split("-").map(Number);
  return localDay(new Date(y, m - 1, d + days));
}

/** The range from the first to the last item, with a little room on both sides (at least 1 min). */
export function fitRange(segments: Segment[], periods: StaticPeriod[]): TimeRange | null {
  const times = [...segments, ...periods].flatMap((i) => [
    Date.parse(i.start_time),
    Date.parse(i.end_time),
  ]);
  if (times.length === 0) return null;
  const start = Math.min(...times);
  const end = Math.max(...times);
  const pad = Math.max((end - start) * 0.04, (60_000 - (end - start)) / 2);
  return { start: start - pad, end: end + pad };
}

/** Blocks for the bar: static and offline periods underneath, stored segments on top. */
export function layoutBlocks(
  range: TimeRange,
  segments: Segment[],
  periods: StaticPeriod[],
): TimelineBlock[] {
  const span = range.end - range.start;
  const items: Omit<TimelineBlock, "left" | "width">[] = [
    ...periods.map((p) => ({
      id: p.id,
      kind: (p.health_status === "offline" ? "offline" : "static") as BlockKind,
      start: Date.parse(p.start_time),
      end: Date.parse(p.end_time),
    })),
    ...segments.map((s) => ({
      id: s.id,
      kind: "segment" as BlockKind,
      start: Date.parse(s.start_time),
      end: Date.parse(s.end_time),
    })),
  ];
  const order: Record<BlockKind, number> = { static: 0, offline: 1, segment: 2 };
  return items
    .filter((i) => i.end > range.start && i.start < range.end && span > 0)
    .map((i) => {
      const from = Math.max(i.start, range.start);
      const to = Math.min(i.end, range.end);
      const width = Math.max(((to - from) / span) * 100, MIN_WIDTH_PERCENT);
      const left = Math.min(((from - range.start) / span) * 100, 100 - width);
      return { ...i, left, width };
    })
    .sort((a, b) => order[a.kind] - order[b.kind] || a.start - b.start);
}

const TICK_STEPS_S = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600];

/** Tick marks at round local times ("14:00", "14:30", "09:05:10"), at most `max` of them. */
export function ticks(range: TimeRange, max = 7): { at: number; left: number; label: string }[] {
  const span = range.end - range.start;
  const stepS = TICK_STEPS_S.find((sec) => span / (sec * 1000) <= max) ?? 43200;
  const step = stepS * 1000;
  const format: Intl.DateTimeFormatOptions =
    stepS < 60
      ? { hour: "2-digit", minute: "2-digit", second: "2-digit" }
      : { hour: "2-digit", minute: "2-digit" };
  // Count from local midnight, so ticks fall on round local times in any timezone.
  const midnight = new Date(range.start);
  midnight.setHours(0, 0, 0, 0);
  const first = midnight.getTime() + Math.ceil((range.start - midnight.getTime()) / step) * step;
  const result = [];
  for (let at = first; at <= range.end; at += step) {
    const label = new Date(at).toLocaleTimeString(undefined, format);
    result.push({ at, left: ((at - range.start) / span) * 100, label });
  }
  return result;
}

/** Seconds of stored footage and of offline time inside the range. */
export function totals(range: TimeRange, blocks: TimelineBlock[]): { stored: number; offline: number } {
  const within = (b: TimelineBlock) =>
    (Math.min(b.end, range.end) - Math.max(b.start, range.start)) / 1000;
  return {
    stored: Math.round(blocks.filter((b) => b.kind === "segment").reduce((t, b) => t + within(b), 0)),
    offline: Math.round(blocks.filter((b) => b.kind === "offline").reduce((t, b) => t + within(b), 0)),
  };
}
