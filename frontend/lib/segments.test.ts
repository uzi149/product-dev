import { describe, expect, it } from "vitest";
import { segmentStatus } from "./segments";

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
