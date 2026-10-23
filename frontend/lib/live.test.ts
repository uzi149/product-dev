import { describe, expect, it } from "vitest";
import { ago, clockTime, ingestionStopped, liveBadge, liveDetail, onlineCount } from "./live";
import type { Camera, LiveStatus } from "./types";

const NOW = new Date(2026, 2, 7, 14, 30, 0);

function status(overrides: Partial<LiveStatus>): LiveStatus {
  return {
    state: "online",
    since: null,
    activity: false,
    last_frame_at: null,
    last_snapshot_at: null,
    error: null,
    ...overrides,
  };
}

function camera(live: LiveStatus | null, overrides: Partial<Camera> = {}): Camera {
  return {
    id: Math.random().toString(36),
    display_name: "CAM",
    source_type: live ? "rtsp" : "file",
    enabled: true,
    retention_days: 14,
    analysis_fps: 1,
    detection_config: {},
    created_at: NOW.toISOString(),
    has_source_uri: Boolean(live),
    source_uri_masked: null,
    segment_count: 0,
    live,
    ...overrides,
  };
}

describe("liveBadge", () => {
  it("names every state", () => {
    expect(liveBadge(status({ state: "online" }))).toEqual({ label: "Live", tone: "green" });
    expect(liveBadge(status({ state: "offline" }))).toEqual({ label: "Offline", tone: "red" });
    expect(liveBadge(status({ state: "connecting" })).tone).toBe("orange");
    expect(liveBadge(status({ state: "not_running" })).label).toBe("Not watched");
    expect(liveBadge(null).label).toBe("File");
  });
});

describe("liveDetail", () => {
  it("says how fresh the picture is", () => {
    const at = new Date(NOW.getTime() - 4000).toISOString();
    expect(liveDetail(status({ last_snapshot_at: at }), NOW)).toBe("Updated 4 s ago");
    expect(liveDetail(status({}), NOW)).toBe("Waiting for the first picture");
  });

  it("says since when a camera is offline, and why", () => {
    const since = new Date(2026, 2, 7, 14, 2).toISOString();
    const line = liveDetail(status({ state: "offline", since, error: "the stream ended" }), NOW);
    expect(line).toBe(`Offline since ${clockTime(since, NOW)}: the stream ended`);
    expect(clockTime(since, NOW)).not.toContain("Mar"); // today: the time only
  });

  it("explains a camera nobody is reading", () => {
    expect(liveDetail(status({ state: "not_running" }), NOW)).toBe("Live ingestion is not running");
  });
});

describe("ago", () => {
  it("uses seconds, then minutes, then hours", () => {
    const before = (ms: number) => new Date(NOW.getTime() - ms).toISOString();
    expect(ago(before(59_000), NOW)).toBe("59 s ago");
    expect(ago(before(180_000), NOW)).toBe("3 min ago");
    expect(ago(before(7_300_000), NOW)).toBe("2 h ago");
    expect(ago(before(-5000), NOW)).toBe("0 s ago");
  });
});

describe("counts", () => {
  it("counts online live cameras and ignores file cameras", () => {
    const cameras = [camera(status({})), camera(status({ state: "offline" })), camera(null)];
    expect(onlineCount(cameras)).toEqual({ online: 1, total: 2 });
  });

  it("knows when nothing reads the live cameras", () => {
    const stopped = status({ state: "not_running" });
    expect(ingestionStopped([camera(stopped), camera(null)])).toBe(true);
    expect(ingestionStopped([camera(stopped), camera(status({}))])).toBe(false);
    expect(ingestionStopped([camera(null)])).toBe(false);
    expect(ingestionStopped([camera(status({ state: "disabled" }), { enabled: false })])).toBe(false);
  });
});
