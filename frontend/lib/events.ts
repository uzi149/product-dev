import type { StateTone } from "./triggers";
import { durationWording } from "./triggers";
import type { EventStatus, ExportState } from "./types";

export const STATUS_LABEL: Record<EventStatus, string> = {
  new: "New",
  confirmed: "Confirmed",
  dismissed: "Dismissed",
};

export const STATUS_TONE: Record<EventStatus, StateTone> = {
  new: "orange",
  confirmed: "green",
  dismissed: "muted",
};

/** The status filter of the Events page; "all" shows every status. */
export type StatusFilter = EventStatus | "all";
export const STATUS_FILTERS: StatusFilter[] = ["new", "confirmed", "dismissed", "all"];

export function filterLabel(filter: StatusFilter): string {
  return filter === "all" ? "All" : STATUS_LABEL[filter];
}

/** "34 s after the action" (live cameras only; null for uploads). */
export function delayWording(seconds: number | null): string | null {
  return seconds === null ? null : `${durationWording(seconds)} after the action`;
}

/** The export button's state, in words. */
export function exportWording(state: ExportState | undefined): string {
  switch (state?.status) {
    case "running":
      return "Exporting…";
    case "done":
      return "Ready to download";
    case "failed":
      return `Export failed: ${state.error ?? "unknown error"}`;
    default:
      return "Not exported";
  }
}

export const EXPORT_POLL_MS = 1_500;
