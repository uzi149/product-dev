import type { ProcessingStatus } from "./types";

/** A segment's analysis state for the operator: badge text and a hover hint. */
export function segmentStatus(status: ProcessingStatus): { label: string; hint: string } {
  switch (status) {
    case "pending":
      return { label: "Analysis queued", hint: "Waiting for the analysis worker" };
    case "processing":
      return { label: "Analysing…", hint: "The VLM is describing this segment" };
    case "done":
      return { label: "Analysed", hint: "Searchable" };
    case "failed":
      return { label: "Analysis failed", hint: "Not searchable; see the analysis worker log" };
  }
}
