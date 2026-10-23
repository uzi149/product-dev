import type { Upload } from "./types";

/** How often an unfinished upload is checked again. */
export const UPLOAD_POLL_MS = 2000;

export function isFinished(upload: Upload): boolean {
  return upload.status === "done" || upload.status === "failed";
}

/** Poll interval for react-query: keep polling while any upload is still running. */
export function pollWhileRunning(uploads: Upload | Upload[] | undefined): number | false {
  if (!uploads) return false;
  const list = Array.isArray(uploads) ? uploads : [uploads];
  return list.some((u) => !isFinished(u)) ? UPLOAD_POLL_MS : false;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** One line for the operator: "2 active parts stored", "Processing…", the error, … */
export function uploadSummary(upload: Upload): string {
  switch (upload.status) {
    case "pending":
      return "Waiting for a worker…";
    case "processing":
      return "Processing…";
    case "failed":
      return upload.error ? `Failed: ${upload.error}` : "Failed";
    case "done": {
      const parts = upload.segments_created ?? 0;
      return parts === 0
        ? "No activity found in this video"
        : `${plural(parts, "active part")} stored`;
    }
  }
}
