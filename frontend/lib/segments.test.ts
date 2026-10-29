import { describe, expect, it } from "vitest";
import { isStuck, retryFailedText, segmentStatus } from "./segments";

describe("segmentStatus", () => {
  it("names each analysis state in plain words", () => {
    expect(segmentStatus("pending").label).toBe("Analysis queued");
    expect(segmentStatus("processing").label).toBe("Analysing…");
    expect(segmentStatus("done").label).toBe("Analysed");
    expect(segmentStatus("failed").label).toBe("Analysis failed");
  });

  it("explains whether the segment can be searched", () => {
    expect(segmentStatus("done").hint).toBe("Searchable");
    expect(segmentStatus("failed").hint).toMatch(/^Not searchable/);
  });
});

describe("stuck segments", () => {
  it("is stuck only while processing and retryable", () => {
    expect(isStuck({ status: "processing", retryable: true })).toBe(true);
    expect(isStuck({ status: "processing", retryable: false })).toBe(false);
    expect(isStuck({ status: "failed", retryable: true })).toBe(false);
  });

  it("says so on the badge", () => {
    expect(segmentStatus("processing", true).label).toBe("Analysis stuck");
    expect(segmentStatus("processing", true).hint).toMatch(/worker may have stopped/);
  });
});

describe("retryFailedText", () => {
  it("names the count and OpenRouter", () => {
    expect(retryFailedText(16, { kind: "api", host: "openrouter.ai" })).toEqual({
      title: "Analyse 16 failed segments again?",
      body: "Their frames are sent to OpenRouter.",
    });
  });

  it("names another API host, such as a local vLLM", () => {
    expect(retryFailedText(1, { kind: "api", host: "127.0.0.1:8001" })).toEqual({
      title: "Analyse 1 failed segment again?",
      body: "Its frames are sent to the VLM at 127.0.0.1:8001.",
    });
  });

  it("says when the local model does the work", () => {
    expect(retryFailedText(3, { kind: "local", host: null }).body).toBe(
      "They are analysed by the local model on this machine.",
    );
  });

  it("stays truthful when the backend is unknown", () => {
    expect(retryFailedText(2, undefined).body).toBe("Their frames are sent to the configured VLM.");
  });
});
