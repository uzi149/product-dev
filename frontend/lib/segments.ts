import type { ProcessingStatus, Segment, VlmBackend } from "./types";

/** `processing` with no progress for a while: its analysis worker most likely stopped. */
export function isStuck(segment: Pick<Segment, "status" | "retryable">): boolean {
  return segment.status === "processing" && segment.retryable;
}

/** A segment's analysis state for the operator: badge text and a hover hint. */
export function segmentStatus(status: ProcessingStatus, stuck = false): { label: string; hint: string } {
  if (stuck) {
    return { label: "Analysis stuck", hint: "No analysis progress for a while; the worker may have stopped" };
  }
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

export const STUCK_TONE = "border-orange/45 bg-orange/10 text-orange";

/** The "Retry failed" confirmation: how many segments, and where their frames go (ADR 0025). */
export function retryFailedText(count: number, backend: VlmBackend | undefined): { title: string; body: string } {
  const title = `Analyse ${count} failed segment${count === 1 ? "" : "s"} again?`;
  const their = count === 1 ? "Its" : "Their";
  if (backend?.kind === "local") {
    return { title, body: `${count === 1 ? "It is" : "They are"} analysed by the local model on this machine.` };
  }
  if (backend?.kind === "api" && backend.host) {
    const where = backend.host === "openrouter.ai" ? "OpenRouter" : `the VLM at ${backend.host}`;
    return { title, body: `${their} frames are sent to ${where}.` };
  }
  return { title, body: `${their} frames are sent to the configured VLM.` };
}
