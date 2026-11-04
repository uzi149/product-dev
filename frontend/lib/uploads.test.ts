import { describe, expect, it } from "vitest";
import { formatDateTime, formatTime } from "./format";
import type { Upload } from "./types";
import {
  isFinished,
  newlyFinished,
  overlapMessage,
  pollWhileRunning,
  recentUploads,
  rejectedUpload,
  spanEndIso,
  UPLOAD_POLL_MS,
  uploadStatus,
  uploadSummary,
} from "./uploads";

const upload = (overrides: Partial<Upload> = {}): Upload => ({
  id: "u1",
  camera_id: "c1",
  filename: "clip.mp4",
  start_time: "2026-03-07T09:05:00Z",
  duration_ms: 40_000,
  status: "pending",
  segments_created: null,
  static_periods_created: null,
  error: null,
  created_at: "2026-03-07T09:06:00Z",
  finished_at: null,
  ...overrides,
});

describe("uploadStatus", () => {
  it("names each upload state, apart from the analysis of its segments", () => {
    expect(uploadStatus(upload()).label).toBe("Upload queued");
    expect(uploadStatus(upload({ status: "processing" })).label).toBe("Processing upload…");
    expect(uploadStatus(upload({ status: "done" })).label).toBe("Upload processed");
    expect(uploadStatus(upload({ status: "failed" })).label).toBe("Upload failed");
    expect(uploadStatus(upload({ status: "done" })).hint).toMatch(/each analysed separately/);
  });
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

describe("rejected uploads", () => {
  it("are listed as failed with the API's reason", () => {
    const rejected = rejectedUpload({
      cameraId: "c1",
      filename: "notes.txt",
      startTimeIso: "2026-03-07T09:05:00.000Z",
      error: "not a readable video",
      now: new Date("2026-03-07T10:00:00Z"),
    });
    expect(rejected).toMatchObject({
      camera_id: "c1",
      filename: "notes.txt",
      start_time: "2026-03-07T09:05:00.000Z",
      status: "failed",
      created_at: "2026-03-07T10:00:00.000Z",
    });
    expect(uploadSummary(rejected)).toBe("Failed: not a readable video");
  });

  it("get their own ids", () => {
    const args = { cameraId: "c1", filename: "a", startTimeIso: "x", error: "e", now: new Date(0) };
    expect(rejectedUpload(args).id).not.toBe(rejectedUpload(args).id);
  });
});

describe("recentUploads", () => {
  it("mixes stored and rejected uploads, newest first, up to the limit", () => {
    const stored = [
      upload({ id: "s2", created_at: "2026-03-07T09:30:00Z" }),
      upload({ id: "s1", created_at: "2026-03-07T09:00:00.123456+00:00" }),
    ];
    const rejected = [upload({ id: "r1", created_at: "2026-03-07T09:15:00.000Z" })];
    expect(recentUploads(stored, rejected).map((u) => u.id)).toEqual(["s2", "r1", "s1"]);
    expect(recentUploads(stored, rejected, 2).map((u) => u.id)).toEqual(["s2", "r1"]);
  });
});

describe("newlyFinished", () => {
  it("finds uploads that were running and have finished", () => {
    const before = [upload({ id: "a", status: "processing" }), upload({ id: "b", status: "done" })];
    const after = [upload({ id: "a", status: "failed" }), upload({ id: "b", status: "done" })];
    expect(newlyFinished(before, after).map((u) => u.id)).toEqual(["a"]);
  });

  it("counts a new upload that is already finished", () => {
    expect(newlyFinished([], [upload({ id: "n", status: "done" })]).map((u) => u.id)).toEqual(["n"]);
    expect(newlyFinished([], [upload({ id: "n", status: "pending" })])).toEqual([]);
  });

  it("finds nothing on the first load", () => {
    expect(newlyFinished(undefined, [upload({ status: "done" })])).toEqual([]);
  });
});

describe("overlapMessage", () => {
  const video9 = { start: "2026-11-02T04:00:00Z", end: "2026-11-02T04:00:54Z", filename: "video9.avi" };

  it("names the footage in local time and the free times around it", () => {
    expect(overlapMessage("Lobby", [video9])).toBe(
      `Lobby already has footage ${formatDateTime(video9.start)} – ${formatTime(video9.end)} (video9.avi). ` +
        `Choose a start time from ${formatTime(video9.end)}, or one where the video ends by ` +
        `${formatTime(video9.start)}.`,
    );
  });

  it("lists every conflict and frees the time after the last one", () => {
    const live = { start: "2026-11-02T04:01:00Z", end: "2026-11-02T04:02:00Z", filename: null };
    const message = overlapMessage("Lobby", [video9, live]);
    expect(message).toContain("(video9.avi), ");
    expect(message).not.toContain("(null)");
    expect(message).toContain(`from ${formatTime(live.end)},`);
    expect(message).toContain(`ends by ${formatTime(video9.start)}.`);
  });
});

describe("spanEndIso", () => {
  it("adds the file's length to its start, when known", () => {
    expect(spanEndIso("2026-11-02T04:00:00.000Z", 54_520)).toBe("2026-11-02T04:00:54.520Z");
    expect(spanEndIso("2026-11-02T04:00:00.000Z", null)).toBeNull();
  });
});
