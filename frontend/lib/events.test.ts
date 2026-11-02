import { describe, expect, it } from "vitest";
import { delayWording, exportWording, filterLabel } from "./events";
import { reconnectDelay } from "./use-live-events";

describe("events helpers", () => {
  it("words the alert delay", () => {
    expect(delayWording(34)).toBe("34 s after the action");
    expect(delayWording(75)).toBe("1 min 15 s after the action");
    expect(delayWording(null)).toBeNull();
  });
  it("words the export state", () => {
    const state = { error: null, exported_at: null, url: null, file_name: null };
    expect(exportWording(undefined)).toBe("Not exported");
    expect(exportWording({ ...state, status: "running" })).toBe("Exporting…");
    expect(exportWording({ ...state, status: "done" })).toBe("Ready to download");
    expect(exportWording({ ...state, status: "failed", error: "no footage" })).toBe("Export failed: no footage");
  });
  it("labels the filters", () => {
    expect(filterLabel("new")).toBe("New");
    expect(filterLabel("all")).toBe("All");
  });
  it("reconnects the live socket with back-off up to 30 s", () => {
    expect([0, 1, 2, 3, 10].map(reconnectDelay)).toEqual([1_000, 2_000, 4_000, 8_000, 30_000]);
  });
});
