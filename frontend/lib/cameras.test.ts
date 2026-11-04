import { describe, expect, it } from "vitest";
import { deleteSummary } from "./cameras";

describe("deleteSummary", () => {
  it("lists every kind of data that goes", () => {
    expect(
      deleteSummary({ segments: 1409, analysed_windows: 1, static_periods: 0, uploads: 3, blocker: null }),
    ).toBe(
      `${(1409).toLocaleString()} segments, 1 analysed window, 0 no-change periods and 3 uploads are deleted for good, with their files.`,
    );
  });
});
