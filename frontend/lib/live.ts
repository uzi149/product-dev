import type { Camera, LiveStatus } from "./types";

/** How often the Live cameras page reloads camera status (snapshots are taken every 5 s). */
export const LIVE_POLL_MS = 5000;

export type Tone = "green" | "orange" | "red" | "muted";

/** The badge on a camera tile. */
export function liveBadge(status: LiveStatus | null): { label: string; tone: Tone } {
  switch (status?.state) {
    case "online":
      return { label: "Live", tone: "green" };
    case "connecting":
      return { label: "Connecting", tone: "orange" };
    case "offline":
      return { label: "Offline", tone: "red" };
    case "disabled":
      return { label: "Disabled", tone: "muted" };
    case "not_running":
      return { label: "Not watched", tone: "muted" };
    case "unknown":
      return { label: "Unknown", tone: "muted" };
    default:
      return { label: "File", tone: "muted" };
  }
}

/** "4 s ago", "3 min ago", "2 h ago". */
export function ago(iso: string, now: Date): string {
  const seconds = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return `${Math.floor(seconds / 3600)} h ago`;
}

/** "14:02" today, otherwise "Mar 07, 14:02" (local time). */
export function clockTime(iso: string, now: Date): string {
  const date = new Date(iso);
  const time = date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (date.toDateString() === now.toDateString()) return time;
  return `${date.toLocaleDateString(undefined, { month: "short", day: "2-digit" })}, ${time}`;
}

/** The line under a camera's name on its tile. */
export function liveDetail(status: LiveStatus | null, now: Date): string {
  switch (status?.state) {
    case "online":
      return status.last_snapshot_at
        ? `Updated ${ago(status.last_snapshot_at, now)}`
        : "Waiting for the first picture";
    case "connecting":
      return "Connecting to the stream…";
    case "offline": {
      const since = status.since ? `Offline since ${clockTime(status.since, now)}` : "Offline";
      return status.error ? `${since}: ${status.error}` : since;
    }
    case "disabled":
      return "Disabled in its settings";
    case "not_running":
      return "Live ingestion is not running";
    case "unknown":
      return status.error ?? "Status unknown";
    default:
      return "Receives uploaded files";
  }
}

export function liveCameras(cameras: Camera[]): Camera[] {
  return cameras.filter((c) => c.source_type === "rtsp");
}

/** For the sidebar: live cameras that are online, of all live cameras. */
export function onlineCount(cameras: Camera[]): { online: number; total: number } {
  const live = liveCameras(cameras);
  return { online: live.filter((c) => c.live?.state === "online").length, total: live.length };
}

/** True when there are enabled live cameras but nothing is reading them. */
export function ingestionStopped(cameras: Camera[]): boolean {
  const enabled = liveCameras(cameras).filter((c) => c.enabled);
  return enabled.length > 0 && enabled.every((c) => c.live?.state === "not_running");
}
