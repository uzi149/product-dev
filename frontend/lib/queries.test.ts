import { describe, expect, it } from "vitest";
import { barWidths, evidenceBadge, gapWording, playlist, shownMs, splitResults, stageLines } from "./queries";
import type { EvidenceItem, PlaybackManifest, QueryState } from "./types";

const MANIFEST: PlaybackManifest = {
  result_id: "r",
  camera_id: "c",
  start_time: "2026-03-07T14:20:51Z",
  end_time: "2026-03-07T14:27:29Z",
  parts: [
    {
      type: "video",
      camera_id: "c",
      segment_id: "s100",
      source_start_ms: 12_000,
      source_end_ms: 69_000,
      start_time: "2026-03-07T14:20:51Z",
      end_time: "2026-03-07T14:21:48Z",
    },
    { type: "gap", duration_ms: 312_000 },
    {
      type: "video",
      camera_id: "c",
      segment_id: "s108",
      source_start_ms: 0,
      source_end_ms: 29_000,
      start_time: "2026-03-07T14:27:00Z",
      end_time: "2026-03-07T14:27:29Z",
    },
  ],
};

function state(overrides: Partial<QueryState> = {}): QueryState {
  return {
    id: "q",
    query_text: "a bag",
    search_text: "a bag",
    understood: [],
    camera_ids: null,
    start: null,
    end: null,
    status: "running",
    stage: "searching",
    verification: null,
    error: null,
    created_at: "2026-03-07T14:30:00Z",
    finished_at: null,
    counts: { candidates: 0, to_verify: 0, verified: 0, rejected: 0, unverifiable: 0, possible: 0 },
    ...overrides,
  };
}

function item(rank: number, status: EvidenceItem["status"], confidence: number | null = null): EvidenceItem {
  return {
    id: `r${rank}`,
    rank,
    camera_id: "c",
    camera_name: "CAM-02",
    start_time: MANIFEST.start_time,
    end_time: MANIFEST.end_time,
    score: 1 - rank / 10,
    status,
    verification_confidence: confidence,
    verification_reason: confidence === null ? null : "A person places a bag.",
    summary: "A person places a bag.",
    keyframe_id: null,
    manifest: MANIFEST,
  };
}

describe("gapWording", () => {
  it("says how much footage is not shown", () => {
    expect(gapWording(312_000)).toBe("5 minutes 12 seconds");
    expect(gapWording(45_000)).toBe("45 seconds");
    expect(gapWording(1_000)).toBe("1 second");
    expect(gapWording(60_000)).toBe("1 minute");
    expect(gapWording(3_720_000)).toBe("1 hour 2 minutes");
  });
});

describe("stageLines", () => {
  it("shows the four stages of scope §10 with counts", () => {
    const counts = { candidates: 8, to_verify: 3, verified: 2, rejected: 1, unverifiable: 0, possible: 5 };
    expect(stageLines(state({ stage: "verifying", verification: "on", counts }))).toEqual([
      { text: "Searching indexed footage…", status: "done" },
      { text: "Found 8 possible matches", status: "done" },
      { text: "Verifying 3 sequences…", status: "active" },
      { text: "Verified results", status: "waiting" },
    ]);
    const done = stageLines(state({ status: "done", stage: "done", verification: "on", counts }));
    expect(done.map((l) => l.status)).toEqual(["done", "done", "done", "done"]);
    expect(done[3].text).toBe("2 verified results");
  });

  it("starts with only the search active", () => {
    expect(stageLines(state()).map((l) => l.status)).toEqual(["active", "waiting", "waiting", "waiting"]);
    expect(stageLines(state({ status: "pending", stage: null })).map((l) => l.status)).toEqual([
      "waiting",
      "waiting",
      "waiting",
      "waiting",
    ]);
  });

  it("says when verification is off with the local model", () => {
    const lines = stageLines(
      state({ status: "done", stage: "done", verification: "skipped", counts: { ...state().counts, candidates: 1 } }),
    );
    expect(lines.map((l) => l.text)).toEqual([
      "Searching indexed footage…",
      "Found 1 possible match",
      "Verification is off with the local model",
    ]);
  });
});

describe("evidenceBadge", () => {
  it("is a possible match until verified", () => {
    expect(evidenceBadge(item(1, "possible")).label).toBe("Possible match");
    expect(evidenceBadge(item(1, "verified", 0.87))).toMatchObject({
      label: "Verified 87%",
      tone: "verified",
      hint: "A person places a bag.",
    });
    expect(evidenceBadge(item(1, "unverifiable")).label).toBe("Couldn't be verified");
  });
});

describe("splitResults", () => {
  it("lists the results below the checked ones under Not verified", () => {
    const results = [item(1, "verified", 0.9), item(3, "unverifiable"), item(6, "possible"), item(7, "possible")];
    const { main, notVerified } = splitResults(results, state({ verification: "on", counts: { ...state().counts, to_verify: 5 } }));
    // Ranks 1-5 were checked (2, 4 and 5 were rejected and are not in the list).
    expect(main.map((r) => r.rank)).toEqual([1, 3]);
    expect(notVerified.map((r) => r.rank)).toEqual([6, 7]);
  });

  it("keeps everything together when verification is off", () => {
    const results = [item(1, "possible"), item(2, "possible")];
    expect(splitResults(results, state({ verification: "skipped" })).notVerified).toEqual([]);
  });
});

describe("playlist", () => {
  it("walks the parts in order with the gaps between them", () => {
    const entries = playlist(MANIFEST);
    expect(entries.map((e) => e.kind)).toEqual(["video", "gap", "video"]);
    expect(entries[0]).toMatchObject({ number: 1, segmentId: "s100", startMs: 12_000, endMs: 69_000 });
    expect(entries[2]).toMatchObject({ number: 2, segmentId: "s108" });
    expect(shownMs(MANIFEST)).toBe(86_000);
  });

  it("gives video parts widths by length and a gap a fixed share", () => {
    const widths = barWidths(playlist(MANIFEST));
    expect(widths.reduce((a, b) => a + b, 0)).toBeCloseTo(100);
    expect(widths[1]).toBeCloseTo(12);
    expect(widths[0] / widths[2]).toBeCloseTo(57 / 29);
  });
});
