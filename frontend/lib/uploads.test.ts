import { describe, expect, it } from "vitest";
import type { Upload } from "./types";
import { isFinished, pollWhileRunning, UPLOAD_POLL_MS, uploadSummary } from "./uploads";

const upload = (overrides: Partial<Upload> = {}): Upload => ({
  id: "u1",
  camera_id: "c1",
  filename: "clip.mp4",
  start_time: "2026-03-07T09:05:00Z",
  status: "pending",
  segments_created: null,
  static_periods_created: null,
  error: null,
  created_at: "2026-03-07T09:06:00Z",
  finished_at: null,
  ...overrides,
});

describe("uploadSummary", () => {
  it("describes running uploads", () => {
    expect(uploadSummary(upload())).toBe("Waiting for a worker…");
    expect(uploadSummary(upload({ status: "processing" }))).toBe("Processing…");
  });

  it("counts the stored parts", () => {
    expect(uploadSummary(upload({ status: "done", segments_created: 1 }))).toBe(
      "1 active part stored",
    );
    expect(uploadSummary(upload({ status: "done", segments_created: 3 }))).toBe(
      "3 active parts stored",
    );
  });

  it("says when nothing happened in the video", () => {
    expect(uploadSummary(upload({ status: "done", segments_created: 0 }))).toBe(
      "No activity found in this video",
    );
  });

  it("shows the error of a failed upload", () => {
    expect(uploadSummary(upload({ status: "failed", error: "not a video" }))).toBe(
      "Failed: not a video",
    );
    expect(uploadSummary(upload({ status: "failed" }))).toBe("Failed");
  });
});

describe("polling", () => {
  it("knows which uploads are finished", () => {
    expect(isFinished(upload({ status: "done" }))).toBe(true);
    expect(isFinished(upload({ status: "failed" }))).toBe(true);
    expect(isFinished(upload({ status: "processing" }))).toBe(false);
  });

  it("polls only while something is still running", () => {
    expect(pollWhileRunning(undefined)).toBe(false);
    expect(pollWhileRunning(upload())).toBe(UPLOAD_POLL_MS);
    expect(pollWhileRunning(upload({ status: "done" }))).toBe(false);
    expect(pollWhileRunning([upload({ status: "done" }), upload({ status: "processing" })])).toBe(
      UPLOAD_POLL_MS,
    );
    expect(pollWhileRunning([])).toBe(false);
  });
});
