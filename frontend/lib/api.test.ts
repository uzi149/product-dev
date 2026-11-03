import { afterEach, describe, expect, it, vi } from "vitest";
import { API_URL, ApiError, api, mediaUrl, OverlapError } from "./api";

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

describe("uploadVideo errors", () => {
  /** An XMLHttpRequest that answers every send with this status and JSON body. */
  function answer(status: number, body: unknown) {
    class FakeXhr {
      status = status;
      responseText = JSON.stringify(body);
      upload = { onprogress: null };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      open() {}
      send() {
        this.onload?.();
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
  }
  const send = () =>
    api.uploadVideo(
      { cameraId: "c1", startTimeIso: "2026-11-02T04:00:00Z", file: new File(["x"], "clip.mpg") },
      () => {},
    );

  afterEach(() => vi.unstubAllGlobals());

  it("turns a 409 with conflicts into an OverlapError", async () => {
    const conflicts = [{ start: "2026-11-02T04:00:00Z", end: "2026-11-02T04:00:54Z", filename: "video9.avi" }];
    answer(409, { detail: { message: "Lobby already has footage at this time.", conflicts } });
    const error = await send().catch((e) => e);
    expect(error).toBeInstanceOf(OverlapError);
    expect(error.status).toBe(409);
    expect(error.conflicts).toEqual(conflicts);
  });

  it("keeps a plain detail as the message", async () => {
    answer(422, { detail: "not a readable video: no stream" });
    const error = await send().catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).not.toBeInstanceOf(OverlapError);
    expect(error.message).toBe("not a readable video: no stream");
  });
});
