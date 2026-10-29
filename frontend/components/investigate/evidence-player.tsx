"use client";

import { RotateCcw, SkipForward } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { barWidths, clock, gapWording, playlist } from "@/lib/queries";
import type { EvidenceItem } from "@/lib/types";
import { cn } from "@/lib/utils";

/** How long the "… not shown" card stays before the next part plays. */
export const GAP_CARD_MS = 2_000;

/**
 * Plays a result's parts in order (scope §11). A part that follows a gap is announced with a
 * "5 minutes 12 seconds not shown" card, so separated footage never looks continuous.
 */
export function EvidencePlayer({ result }: { result: EvidenceItem }) {
  const entries = useMemo(() => playlist(result.manifest), [result.manifest]);
  const widths = useMemo(() => barWidths(entries), [entries]);
  const videoCount = entries.filter((e) => e.kind === "video").length;
  const [index, setIndex] = useState(0);
  const [ended, setEnded] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  const entry = entries[index];
  // The video part on screen: the current one, or (during a gap card) the one before it.
  const shown = entries
    .slice(0, index + 1)
    .reverse()
    .find((e) => e.kind === "video");

  function next() {
    if (index + 1 < entries.length) setIndex(index + 1);
    else setEnded(true);
  }

  function jump(to: number) {
    setEnded(false);
    setIndex(to);
  }

  // A gap card moves on by itself.
  useEffect(() => {
    if (ended || entry?.kind !== "gap") return;
    videoRef.current?.pause();
    const timer = setTimeout(() => setIndex((i) => Math.min(i + 1, entries.length - 1)), GAP_CARD_MS);
    return () => clearTimeout(timer);
  }, [index, ended, entry?.kind, entries.length]);

  // A new video part in the same segment: seek; a new segment loads through `src` below.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || ended || entry?.kind !== "video") return;
    if (video.readyState >= 1 && video.dataset.segment === entry.segmentId) {
      video.currentTime = entry.startMs / 1000;
      void video.play().catch(() => {});
    }
  }, [index, ended, entry]);

  if (!entry || !shown || shown.kind !== "video") {
    return <p className="p-4 text-sm text-muted-foreground">This result has nothing to play.</p>;
  }

  return (
    <div className="grid gap-3">
      <div className="relative overflow-hidden rounded-xl bg-black">
        <video
          ref={videoRef}
          data-segment={shown.segmentId}
          src={api.playbackUrl(shown.segmentId)}
          muted
          playsInline
          controls
          onLoadedMetadata={(e) => {
            if (entry.kind !== "video") return;
            e.currentTarget.currentTime = entry.startMs / 1000;
            void e.currentTarget.play().catch(() => {});
          }}
          onTimeUpdate={(e) => {
            if (entry.kind === "video" && !ended && e.currentTarget.currentTime >= entry.endMs / 1000 - 0.05) {
              e.currentTarget.pause();
              next();
            }
          }}
          className="mx-auto aspect-video max-h-[420px] w-full"
        />
        {entry.kind === "gap" && !ended && (
          <div
            role="status"
            className="absolute inset-0 grid place-items-center bg-black/85 text-center text-white"
          >
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-orange">Gap</div>
              <p className="mt-2 text-lg font-semibold">{gapWording(entry.durationMs)} not shown</p>
              <Button size="sm" variant="outline" className="mt-3" onClick={next}>
                <SkipForward /> Skip
              </Button>
            </div>
          </div>
        )}
        {ended && (
          <div className="absolute inset-0 grid place-items-center bg-black/70 text-center text-white">
            <div>
              <p className="text-sm">End of the evidence</p>
              <Button size="sm" variant="outline" className="mt-3" onClick={() => jump(0)}>
                <RotateCcw /> Play again
              </Button>
            </div>
          </div>
        )}
      </div>

      <div className="flex h-7 w-full overflow-hidden rounded-md border" aria-label="Parts">
        {entries.map((e, i) => (
          <button
            key={i}
            type="button"
            title={
              e.kind === "gap"
                ? `${gapWording(e.durationMs)} not shown`
                : `Part ${e.number}: ${clock(e.startTime)}–${clock(e.endTime)}`
            }
            onClick={() => jump(e.kind === "gap" ? Math.min(i + 1, entries.length - 1) : i)}
            style={{ width: `${widths[i]}%` }}
            data-kind={e.kind}
            aria-current={i === index ? "true" : undefined}
            className={cn(
              "h-full border-r last:border-r-0 text-[10px] font-mono transition-colors",
              e.kind === "gap"
                ? "bg-[repeating-linear-gradient(45deg,transparent,transparent_4px,var(--orange)_4px,var(--orange)_5px)] text-orange"
                : "bg-green/15 hover:bg-green/25",
              i === index && e.kind === "video" && "bg-green/45",
            )}
          >
            {e.kind === "gap" ? "gap" : e.number}
          </button>
        ))}
      </div>
      <p className="font-mono text-[11px] text-muted-foreground">
        {entry.kind === "gap"
          ? `Gap · ${gapWording(entry.durationMs)} not shown`
          : `Part ${entry.number} of ${videoCount} · ${clock(entry.startTime)}–${clock(entry.endTime)}`}
      </p>
    </div>
  );
}
