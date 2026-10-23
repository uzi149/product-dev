import { describe, expect, it } from "vitest";
import { formatDuration, localInputToIso, toLocalInputValue } from "./format";

describe("formatDuration", () => {
  it("formats minutes and seconds", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(4000)).toBe("0:04");
    expect(formatDuration(65_000)).toBe("1:05");
  });

  it("adds hours when needed", () => {
    expect(formatDuration(3_723_000)).toBe("1:02:03");
  });

  it("rounds to the nearest second and never goes negative", () => {
    expect(formatDuration(2_600)).toBe("0:03");
    expect(formatDuration(-5)).toBe("0:00");
  });
});

describe("datetime-local helpers", () => {
  it("round trips local time through an ISO string with a timezone", () => {
    const value = "2026-03-07T09:05";
    const iso = localInputToIso(value);
    expect(iso).toMatch(/Z$/);
    expect(toLocalInputValue(new Date(iso!))).toBe(value);
  });

  it("returns null for empty or invalid input", () => {
    expect(localInputToIso("")).toBeNull();
    expect(localInputToIso("not a date")).toBeNull();
  });
});
