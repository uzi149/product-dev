"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { LIVE_POLL_MS, liveBadge, liveDetail, type Tone } from "@/lib/live";
import type { Camera } from "@/lib/types";
import { cn } from "@/lib/utils";

const DOT: Record<Tone, string> = {
  green: "bg-green shadow-[0_0_8px_var(--green)]",
  orange: "bg-orange",
  red: "bg-red",
  muted: "bg-muted-foreground",
};

/** One camera on the wall: its latest snapshot, status and name. Opens the camera's timeline. */
export function CameraTile({ camera, now }: { camera: Camera; now: Date }) {
  const live = camera.live;
  const badge = liveBadge(live);
  const snapshotAt = live?.last_snapshot_at ?? null;
  const dimmed = live?.state !== "online";

  // A picture that failed to load (MinIO down) is tried again: an offline camera sends no newer
  // snapshot, whose new URL would otherwise be the next try.
  const [attempt, setAttempt] = useState(0);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const src = snapshotAt ? api.snapshotUrl(camera.id, snapshotAt, attempt) : null;
  const failed = src !== null && src === failedSrc;
  useEffect(() => {
    if (!failed) return;
    const id = setTimeout(() => setAttempt((a) => a + 1), LIVE_POLL_MS);
    return () => clearTimeout(id);
  }, [failed]);

  return (
    <Link
      href={`/cameras?camera=${camera.id}`}
      aria-label={`${camera.display_name}: ${badge.label}`}
      className="group relative block min-h-[200px] overflow-hidden rounded-xl border bg-[#111b1c] focus-visible:outline-2 focus-visible:outline-green"
    >
      {src && !failed ? (
        // The snapshot is a redirect to a presigned MinIO URL, not a static asset: a plain <img>.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={`Latest picture from ${camera.display_name}`}
          onError={() => setFailedSrc(src)}
          className={cn(
            "absolute inset-0 size-full object-cover opacity-80 transition duration-300 group-hover:scale-[1.02] group-hover:opacity-95",
            dimmed && "opacity-35 grayscale",
          )}
        />
      ) : (
        <div className="absolute inset-0 grid place-items-center font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          {failed ? "Picture not available" : "No picture yet"}
        </div>
      )}
      {/* Scan lines, as in the prototype. */}
      <div className="pointer-events-none absolute inset-0 bg-[repeating-linear-gradient(0deg,rgba(255,255,255,.018)_0_1px,transparent_1px_4px)]" />

      <div className="absolute inset-0 flex flex-col justify-between p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 rounded-[5px] bg-[#060d0d]/75 px-1.5 py-1 font-mono text-[9px] uppercase tracking-[0.1em]">
            <i className={cn("size-[5px] rounded-full", DOT[badge.tone])} />
            {badge.label}
          </span>
          {live?.activity && live.state === "online" && (
            <span className="rounded-[5px] bg-orange/90 px-1.5 py-1 font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-[#1a0d04]">
              Activity
            </span>
          )}
        </div>
        <div className="min-w-0 [text-shadow:0_1px_8px_#000]">
          <div className="truncate text-[13px] font-semibold">{camera.display_name}</div>
          <div className="mt-0.5 truncate font-mono text-[10px] text-foreground/65">
            {liveDetail(live, now)}
          </div>
        </div>
      </div>
    </Link>
  );
}
