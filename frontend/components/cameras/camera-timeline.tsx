"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { formatDateTime, formatDuration } from "@/lib/format";
import {
  fitRange,
  layoutBlocks,
  localDay,
  type Preset,
  rangeFor,
  shiftDay,
  type TimelineBlock,
  ticks,
  totals,
} from "@/lib/timeline";
import type { Camera } from "@/lib/types";
import { cn } from "@/lib/utils";

const PRESETS: { value: Preset; label: string }[] = [
  { value: "1h", label: "1 h" },
  { value: "6h", label: "6 h" },
  { value: "24h", label: "24 h" },
  { value: "day", label: "Day" },
  { value: "fit", label: "Fit" },
];

const BLOCK_STYLE: Record<TimelineBlock["kind"], string> = {
  segment: "z-10 bg-green/85 hover:bg-green",
  static: "bg-white/[0.15] hover:bg-white/[0.22]",
  offline: "bg-red/55 hover:bg-red/75",
};

const KIND_LABEL: Record<TimelineBlock["kind"], string> = {
  segment: "Stored footage",
  static: "No change",
  offline: "Camera offline",
};

function blockTitle(block: TimelineBlock): string {
  const from = formatDateTime(new Date(block.start).toISOString());
  return `${KIND_LABEL[block.kind]}: ${from}, ${formatDuration(block.end - block.start)}`;
}

/**
 * What was stored for a camera over time: stored segments, still periods and offline periods.
 * Live cameras start on the last hour; file cameras zoomed to the footage of their newest day.
 */
export function CameraTimeline({ camera }: { camera: Camera }) {
  const isLive = camera.source_type === "rtsp";
  const [preset, setPreset] = useState<Preset>(isLive ? "1h" : "fit");
  const [back, setBack] = useState(0);
  const [chosenDay, setChosenDay] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [selected, setSelected] = useState<TimelineBlock | null>(null);

  // File cameras open on the day of their newest stored segment (uploads have their own dates).
  const newest = useQuery({
    queryKey: ["segments", camera.id, "newest"],
    queryFn: () => api.segments({ cameraId: camera.id, limit: 1 }),
    enabled: !isLive,
  });
  const newestStart = newest.data?.[0]?.start_time;
  const today = localDay(new Date(now));
  const day = chosenDay ?? (newestStart ? localDay(new Date(newestStart)) : today);

  // While showing the latest range, move it along so new footage appears.
  const daily = preset === "day" || preset === "fit";
  const following = !daily && back === 0;
  useEffect(() => {
    if (!following) return;
    const id = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(id);
  }, [following]);

  const loaded = rangeFor(preset, new Date(now), back, day);
  const start = new Date(loaded.start).toISOString();
  const end = new Date(loaded.end).toISOString();
  const segments = useQuery({
    queryKey: ["segments", camera.id, "timeline", start, end],
    queryFn: () => api.segments({ cameraId: camera.id, start, end, limit: 500 }),
  });
  const periods = useQuery({
    queryKey: ["static-periods", camera.id, start, end],
    queryFn: () => api.staticPeriods({ cameraId: camera.id, start, end }),
  });

  const fitted = preset === "fit" ? fitRange(segments.data ?? [], periods.data ?? []) : null;
  const range = fitted ?? loaded;
  const blocks = layoutBlocks(range, segments.data ?? [], periods.data ?? []);
  const sum = totals(range, blocks);
  const error = segments.error ?? periods.error;

  function choose(next: Preset) {
    setPreset(next);
    setBack(0);
    setSelected(null);
  }

  function step(direction: -1 | 1) {
    setSelected(null);
    if (daily) setChosenDay(shiftDay(day, direction));
    else setBack((b) => Math.max(0, b - direction));
  }

  const atLatest = daily ? day >= today : back === 0;

  return (
    <section className="overflow-hidden rounded-2xl border bg-card" aria-label="Timeline">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div>
          <h2 className="text-xs font-semibold">Timeline</h2>
          <small className="font-mono text-[9px] uppercase tracking-[0.1em] text-muted-foreground">
            {sum.stored > 0 ? `${formatDuration(sum.stored * 1000)} stored` : "Nothing stored"}
            {sum.offline > 0 && ` · offline ${formatDuration(sum.offline * 1000)}`}
          </small>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <div role="group" aria-label="Range" className="flex rounded-lg border p-0.5">
            {PRESETS.map((p) => (
              <button
                key={p.value}
                type="button"
                aria-pressed={preset === p.value}
                onClick={() => choose(p.value)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground",
                  preset === p.value && "bg-accent text-green hover:text-green",
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          {daily && (
            <Input
              type="date"
              aria-label="Day"
              value={day}
              onChange={(e) => e.target.value && setChosenDay(e.target.value)}
              className="h-8 w-[150px]"
            />
          )}
          <Button size="icon-sm" variant="outline" aria-label="Earlier" onClick={() => step(-1)}>
            <ChevronLeft />
          </Button>
          <Button
            size="icon-sm"
            variant="outline"
            aria-label="Later"
            disabled={atLatest}
            onClick={() => step(1)}
          >
            <ChevronRight />
          </Button>
        </div>
      </div>

      <div className="px-4 pb-3 pt-4">
        <div className="relative h-9 overflow-hidden rounded-md border border-dashed bg-[repeating-linear-gradient(135deg,transparent_0_6px,rgba(255,255,255,.025)_6px_7px)]">
          {blocks.map((block) => (
            <button
              key={block.id}
              type="button"
              title={blockTitle(block)}
              aria-label={blockTitle(block)}
              data-kind={block.kind}
              onClick={() => setSelected(block)}
              style={{ left: `${block.left}%`, width: `${block.width}%` }}
              className={cn(
                "absolute inset-y-0 min-w-[3px] transition-colors",
                BLOCK_STYLE[block.kind],
                selected?.id === block.id && "outline-2 outline-foreground",
              )}
            />
          ))}
        </div>
        <div className="relative mt-1 h-4">
          {/* Labels too close to the ends would be cut off. */}
          {ticks(range)
            .filter((tick) => tick.left >= 5 && tick.left <= 95)
            .map((tick) => (
              <span
                key={tick.at}
                style={{ left: `${tick.left}%` }}
                className="absolute -translate-x-1/2 whitespace-nowrap font-mono text-[9px] text-muted-foreground"
              >
                {tick.label}
              </span>
            ))}
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
          <Legend className="bg-green/85" label="Stored footage" />
          <Legend className="bg-white/[0.15]" label="No change (not stored)" />
          <Legend className="bg-red/55" label="Camera offline" />
          <Legend className="border border-dashed" label="No record" />
        </div>
        {error && (
          <p role="alert" className="mt-2 text-xs text-destructive">
            Could not load the timeline: {error.message}
          </p>
        )}
      </div>

      {selected && <Selection block={selected} periods={periods.data ?? []} />}
    </section>
  );
}

function Legend({ className, label }: { className: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <i className={cn("inline-block h-2 w-3 rounded-[2px]", className)} />
      {label}
    </span>
  );
}

function Selection({
  block,
  periods,
}: {
  block: TimelineBlock;
  periods: { id: string; has_reference_frame: boolean }[];
}) {
  const caption = (
    <p className="mt-2 text-center font-mono text-[11px] text-muted-foreground">
      {blockTitle(block)}
    </p>
  );
  if (block.kind === "segment") {
    return (
      <div className="border-t bg-black p-3">
        <video
          key={block.id}
          src={api.playbackUrl(block.id)}
          controls
          autoPlay
          className="mx-auto max-h-[420px] w-full rounded-lg"
        />
        {caption}
      </div>
    );
  }
  const hasFrame = periods.find((p) => p.id === block.id)?.has_reference_frame;
  return (
    <div className="border-t bg-black/40 p-3">
      {hasFrame ? (
        // A redirect to a presigned MinIO URL, not a static asset: a plain <img>.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={api.referenceFrameUrl(block.id)}
          alt="Reference picture of the unchanged scene"
          className="mx-auto max-h-[360px] rounded-lg"
        />
      ) : (
        <p className="py-6 text-center text-xs text-muted-foreground">
          {block.kind === "offline"
            ? "The camera could not be reached; nothing was recorded."
            : "No reference picture for this period."}
        </p>
      )}
      {caption}
    </div>
  );
}
