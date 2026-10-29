"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, RotateCcw, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { SegmentVideo } from "@/components/cameras/stored-media";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { formatDateTime, formatDuration, localInputToIso } from "@/lib/format";
import { LIST_POLL_MS } from "@/lib/refresh";
import { isStuck, retryFailedText, segmentStatus, STUCK_TONE } from "@/lib/segments";
import { STATUS_TONE } from "@/lib/status";
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
    refetchInterval: LIST_POLL_MS,
    // A new From/To keeps the current list on screen until the new one arrives.
    placeholderData: keepPreviousData,
  });
  // The count on "Retry failed": every failed segment in From/To, not only the listed ones.
  const counts = useQuery({
    queryKey: ["segment-counts", camera.id, start, end],
    queryFn: () => api.segmentStatusCounts({ cameraId: camera.id, start, end }),
    refetchInterval: LIST_POLL_MS,
    placeholderData: keepPreviousData,
  });
  // Shared with the app shell: names the VLM backend in the confirmation (ADR 0025).
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15_000 });

  const queryClient = useQueryClient();
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["segments", camera.id] });
    queryClient.invalidateQueries({ queryKey: ["segment-counts", camera.id] });
  };
  const retryOne = useMutation({
    mutationFn: (segmentId: string) => api.reanalyseSegment(segmentId),
    onSuccess: () => toast.success("Segment queued for analysis"),
    onError: (e: Error) => toast.error(e.message),
    onSettled: refresh,
  });
  const [confirming, setConfirming] = useState(false);
  const retryFailed = useMutation({
    mutationFn: () => api.reanalyseFailed({ cameraId: camera.id, start, end }),
    onSuccess: ({ queued }) => {
      toast.success(`${queued} segment${queued === 1 ? "" : "s"} queued for analysis`);
      setConfirming(false);
    },
    onError: (e: Error) => toast.error(e.message),
    onSettled: refresh,
  });
  const failed = counts.data?.failed ?? 0;
  const confirmText = retryFailedText(failed, health.data?.vlm_backend);

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
          {failed > 0 && (
            <Button variant="outline" onClick={() => setConfirming(true)}>
              <RotateCcw /> Retry failed ({failed})
            </Button>
          )}
        </div>
      </div>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirmText.title}</DialogTitle>
            <DialogDescription>{confirmText.body}</DialogDescription>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {from || to ? "Only the failed segments within From/To." : "Every failed segment of this camera."} A
            retry helps once the cause is fixed (for example the VLM credit or connection).
          </p>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button type="button" disabled={retryFailed.isPending} onClick={() => retryFailed.mutate()}>
              {retryFailed.isPending ? "Queueing…" : "Analyse again"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
          const stuck = isStuck(segment);
          const status = segmentStatus(segment.status, stuck);
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
                  <Badge
                    variant="outline"
                    title={status.hint}
                    className={stuck ? STUCK_TONE : STATUS_TONE[segment.status]}
                  >
                    {status.label}
                  </Badge>
                  {segment.retryable && (
                    <Button
                      size="sm"
                      variant="outline"
                      title="Analyse this segment again"
                      disabled={retryOne.isPending && retryOne.variables === segment.id}
                      onClick={() => retryOne.mutate(segment.id)}
                    >
                      <RotateCcw /> Retry
                    </Button>
                  )}
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
                  <SegmentVideo segmentId={segment.id} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
