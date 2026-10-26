"use client";

import { useQuery } from "@tanstack/react-query";
import { Play, X } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { formatDateTime, formatDuration, localInputToIso } from "@/lib/format";
import type { Camera } from "@/lib/types";
import { cn } from "@/lib/utils";

export function SegmentsPanel({ camera }: { camera: Camera }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  // Only one segment plays at a time, in a player under its own row.
  const [playingId, setPlayingId] = useState<string | null>(null);

  const start = localInputToIso(from) ?? undefined;
  const end = localInputToIso(to) ?? undefined;
  const segments = useQuery({
    queryKey: ["segments", camera.id, start, end],
    queryFn: () => api.segments({ cameraId: camera.id, start, end }),
  });

  return (
    <section className="overflow-hidden rounded-2xl border bg-card">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b px-4 py-3">
        <div>
          <h2 className="text-xs font-semibold">Stored segments</h2>
          <small className="font-mono text-[9px] uppercase tracking-[0.1em] text-muted-foreground">
            {camera.display_name}
          </small>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="grid gap-1">
            <Label htmlFor="seg-from" className="text-[10px]">From</Label>
            <Input id="seg-from" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="seg-to" className="text-[10px]">To</Label>
            <Input id="seg-to" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          {(from || to) && (
            <Button
              variant="ghost"
              onClick={() => {
                setFrom("");
                setTo("");
              }}
            >
              Clear
            </Button>
          )}
        </div>
      </div>

      {segments.isPending && <p className="p-4 text-sm text-muted-foreground">Loading…</p>}
      {segments.isError && (
        <p role="alert" className="p-4 text-sm text-destructive">
          Could not load segments: {segments.error.message}
        </p>
      )}
      {segments.data?.length === 0 && (
        <p className="p-4 text-sm text-muted-foreground">
          {from || to ? "No segments in this time range." : "No footage stored for this camera yet."}
        </p>
      )}

      <ul>
        {segments.data?.map((segment) => {
          const isPlaying = playingId === segment.id;
          return (
            <li key={segment.id} className={cn("border-b last:border-0", isPlaying && "bg-accent")}>
              <div className="grid grid-cols-[1fr_auto] items-center gap-3 px-4 py-3">
                <div>
                  <div className="font-mono text-xs">{formatDateTime(segment.start_time)}</div>
                  <div className="mt-1 text-[11px] text-muted-foreground">
                    {formatDuration(segment.duration_ms)}
                    {segment.width && segment.height ? ` · ${segment.width}×${segment.height}` : ""}
                    {segment.codec ? ` · ${segment.codec}` : ""}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline">{segment.status}</Badge>
                  {isPlaying ? (
                    <Button size="sm" variant="outline" onClick={() => setPlayingId(null)}>
                      <X /> Close
                    </Button>
                  ) : (
                    <Button size="sm" onClick={() => setPlayingId(segment.id)}>
                      <Play /> Play
                    </Button>
                  )}
                </div>
              </div>
              {isPlaying && (
                <div className="bg-black p-3">
                  <video
                    src={api.playbackUrl(segment.id)}
                    controls
                    autoPlay
                    className="mx-auto max-h-[420px] w-full rounded-lg"
                  />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
