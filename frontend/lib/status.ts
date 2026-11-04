import type { ProcessingStatus } from "./types";

/** Badge colours shared by every processing state: uploads and segment analysis. */
export const STATUS_TONE: Record<ProcessingStatus, string> = {
  pending: "text-muted-foreground",
  processing: "border-blue/45 bg-blue/10 text-blue",
  done: "border-green/45 bg-green/10 text-green",
  failed: "border-red/45 bg-red/15 text-red",
};
