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

/** How many uploads Recent uploads shows. */
export const RECENT_UPLOADS = 10;

/** Where this browser keeps the uploads the API refused for a camera (ADR 0016). */
export const rejectedUploadsKey = (cameraId: string) => ["rejected-uploads", cameraId] as const;

let rejectedCount = 0;

/** A file the API refused before storing it: listed as failed, in this browser only (ADR 0016). */
export function rejectedUpload(args: {
  cameraId: string;
  filename: string;
  startTimeIso: string;
  error: string;
  now?: Date;
}): Upload {
  const now = (args.now ?? new Date()).toISOString();
  rejectedCount += 1;
  return {
    id: `rejected-${now}-${rejectedCount}`,
    camera_id: args.cameraId,
    filename: args.filename,
    start_time: args.startTimeIso,
    status: "failed",
    segments_created: null,
    static_periods_created: null,
    error: args.error,
    created_at: now,
    finished_at: now,
  };
}

/** The API's uploads and this browser's rejected ones together, newest first. */
export function recentUploads(stored: Upload[], rejected: Upload[], limit = RECENT_UPLOADS): Upload[] {
  return [...stored, ...rejected]
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    .slice(0, limit);
}

/** Uploads in `after` that have finished since `before`: they were running, or are new. */
export function newlyFinished(before: Upload[] | undefined, after: Upload[]): Upload[] {
  if (!before) return [];
  const known = new Map(before.map((u) => [u.id, u]));
  return after.filter((u) => {
    const was = known.get(u.id);
    return isFinished(u) && (!was || !isFinished(was));
  });
}

/** An upload's import state for the operator: badge text and a hover hint. */
export function uploadStatus(upload: Upload): { label: string; hint: string } {
  switch (upload.status) {
    case "pending":
      return { label: "Import queued", hint: "Waiting for a worker to read the file" };
    case "processing":
      return { label: "Importing…", hint: "Finding the active parts and storing them as segments" };
    case "done":
      return {
        label: "Imported",
        hint: "Active parts are under Stored segments, each analysed separately",
      };
    case "failed":
      return { label: "Import failed", hint: "The file could not be imported" };
  }
}

const plural =(n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

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
