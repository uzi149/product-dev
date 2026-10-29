import type { EvidenceItem, PlaybackManifest, QueryState } from "./types";

const plural = (n: number, word: string, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

/** 312000 -> "5 minutes 12 seconds"; footage not shown between two parts (scope §11). */
export function gapWording(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const words = [];
  if (hours) words.push(plural(hours, "hour"));
  if (minutes) words.push(plural(minutes, "minute"));
  if (seconds || words.length === 0) words.push(plural(seconds, "second"));
  return words.join(" ");
}

export type StageStatus = "done" | "active" | "waiting";

/** The four lines of scope §10 ("Searching indexed footage…" … "2 verified results"). */
export function stageLines(state: QueryState): { text: string; status: StageStatus }[] {
  const { stage, status, counts, verification } = state;
  const order = ["searching", "candidates", "verifying", "done"];
  const at = status === "pending" ? -1 : order.indexOf(stage ?? "searching");
  const finished = status === "done" || status === "failed";
  const mark = (index: number): StageStatus =>
    finished || at > index ? "done" : at === index ? "active" : "waiting";

  const lines: { text: string; status: StageStatus }[] = [
    { text: "Searching indexed footage…", status: mark(0) },
    {
      text: at >= 1 ? `Found ${plural(counts.candidates, "possible match", "possible matches")}` : "Finding possible matches…",
      status: mark(1),
    },
  ];
  if (verification === "skipped") {
    lines.push({ text: "Verification is off with the local model", status: finished ? "done" : "waiting" });
    return lines;
  }
  lines.push({
    text:
      at >= 2 || (finished && counts.to_verify > 0)
        ? `Verifying ${plural(counts.to_verify, "sequence")}…`
        : "Verifying the best matches…",
    status: counts.to_verify === 0 && finished ? "done" : mark(2),
  });
  lines.push({
    text: finished ? `${plural(counts.verified, "verified result")}` : "Verified results",
    status: finished ? "done" : "waiting",
  });
  return lines;
}

export type BadgeTone = "verified" | "possible" | "unverifiable" | "rejected";

/** A result's badge: "possible match" until verified (scope §10). */
export function evidenceBadge(item: EvidenceItem): { label: string; tone: BadgeTone; hint: string } {
  switch (item.status) {
    case "verified":
      return {
        label: `Verified ${Math.round((item.verification_confidence ?? 0) * 100)}%`,
        tone: "verified",
        hint: item.verification_reason ?? "The VLM confirmed this result",
      };
    case "unverifiable":
      return {
        label: "Couldn't be verified",
        tone: "unverifiable",
        hint: item.verification_reason ?? "The VLM could not check this result",
      };
    case "rejected":
      return { label: "Rejected", tone: "rejected", hint: item.verification_reason ?? "" };
    case "possible":
      return { label: "Possible match", tone: "possible", hint: "Not checked by the VLM" };
  }
}

/**
 * The results to show first, and those under "Not verified" (ADR 0029): results below the ones
 * the VLM checks stay possible matches. With verification off, all are in the main list.
 */
export function splitResults(
  results: EvidenceItem[],
  state: QueryState,
): { main: EvidenceItem[]; notVerified: EvidenceItem[] } {
  if (state.verification !== "on") return { main: results, notVerified: [] };
  // By rank, not position: rejected results are already left out of the list.
  const unchecked = (r: EvidenceItem) => r.rank > state.counts.to_verify && r.status === "possible";
  return { main: results.filter((r) => !unchecked(r)), notVerified: results.filter(unchecked) };
}

export type PlaylistEntry =
  | {
      kind: "video";
      /** 1-based number among the video parts. */
      number: number;
      segmentId: string;
      startMs: number;
      endMs: number;
      startTime: string;
      endTime: string;
    }
  | { kind: "gap"; durationMs: number };

/** The manifest as the player walks it: video parts in order, gaps between them. */
export function playlist(manifest: PlaybackManifest): PlaylistEntry[] {
  let number = 0;
  return manifest.parts.map((part) =>
    part.type === "gap"
      ? { kind: "gap", durationMs: part.duration_ms }
      : {
          kind: "video",
          number: ++number,
          segmentId: part.segment_id,
          startMs: part.source_start_ms,
          endMs: part.source_end_ms,
          startTime: part.start_time,
          endTime: part.end_time,
        },
  );
}

/** How long the result plays: the video parts only. */
export function shownMs(manifest: PlaybackManifest): number {
  return manifest.parts.reduce(
    (sum, p) => sum + (p.type === "video" ? p.source_end_ms - p.source_start_ms : 0),
    0,
  );
}

/** A gap's share of the bar: fixed, so a long gap stays visible without hiding the video parts. */
export const GAP_SHARE = 0.12;

/** Each entry's width on the part bar, in %: video parts by length, gaps a fixed share. */
export function barWidths(entries: PlaylistEntry[]): number[] {
  const gaps = entries.filter((e) => e.kind === "gap").length;
  const videoMs = entries.reduce((sum, e) => sum + (e.kind === "video" ? e.endMs - e.startMs : 0), 0);
  const videoShare = 100 * (1 - Math.min(gaps * GAP_SHARE, 0.5));
  const gapWidth = gaps ? (100 - videoShare) / gaps : 0;
  return entries.map((e) =>
    e.kind === "gap" ? gapWidth : videoMs ? (videoShare * (e.endMs - e.startMs)) / videoMs : 0,
  );
}

/** "14:20:51" in local time. */
export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** The browser's IANA time zone, sent with each query (step 4.1). */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}
