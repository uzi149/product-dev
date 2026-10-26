import { describe, expect, it } from "vitest";
import { API_URL, api, mediaUrl } from "./api";

describe("media URLs", () => {
  it("leave out parameters that are null", () => {
    expect(mediaUrl("/x")).toBe(`${API_URL}/x`);
    expect(mediaUrl("/x", { v: null, r: 2 })).toBe(`${API_URL}/x?r=2`);
  });

  it("stay the same until a load is retried", () => {
    expect(api.playbackUrl("s1")).toBe(`${API_URL}/segments/s1/playback`);
    expect(api.playbackUrl("s1", 1)).toBe(`${API_URL}/segments/s1/playback?r=1`);
    expect(api.referenceFrameUrl("p1")).toBe(`${API_URL}/static-periods/p1/reference-frame`);
    expect(api.referenceFrameUrl("p1", 3)).toBe(`${API_URL}/static-periods/p1/reference-frame?r=3`);
  });

  it("give every snapshot its own URL", () => {
    const at = "2026-03-07T09:05:00.123+00:00";
    expect(api.snapshotUrl("c1", null)).toBe(`${API_URL}/cameras/c1/snapshot`);
    expect(api.snapshotUrl("c1", at)).toBe(
      `${API_URL}/cameras/c1/snapshot?v=${encodeURIComponent(at)}`,
    );
    expect(api.snapshotUrl("c1", at, 2)).toBe(
      `${API_URL}/cameras/c1/snapshot?v=${encodeURIComponent(at)}&r=2`,
    );
  });
});
