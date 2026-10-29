"use client";

import { ImageOff } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { type BadgeTone, clock, evidenceBadge, shownMs } from "@/lib/queries";
import type { EvidenceItem } from "@/lib/types";
import { cn } from "@/lib/utils";

const TONE: Record<BadgeTone, string> = {
  verified: "border-green/45 bg-green/10 text-green",
  possible: "text-muted-foreground",
  unverifiable: "border-orange/45 bg-orange/10 text-orange",
  rejected: "border-red/45 bg-red/15 text-red",
};

function Thumbnail({ keyframeId }: { keyframeId: string | null }) {
  const [failed, setFailed] = useState(false);
  if (!keyframeId || failed) {
    return (
      <div className="grid aspect-video w-24 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
        <ImageOff className="size-4" />
      </div>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- a presigned MinIO redirect, not a static asset
    <img
      src={api.keyframeUrl(keyframeId)}
      alt=""
      onError={() => setFailed(true)}
      className="aspect-video w-24 shrink-0 rounded-md bg-muted object-cover"
    />
  );
}

/** One merged result: camera, time, how much is shown, the verification badge and summary. */
export function ResultCard({
  result,
  selected,
  onSelect,
}: {
  result: EvidenceItem;
  selected: boolean;
  onSelect: () => void;
}) {
  const badge = evidenceBadge(result);
  const gaps = result.manifest.parts.filter((p) => p.type === "gap").length;
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        className={cn(
          "flex w-full gap-3 rounded-xl border bg-card p-3 text-left transition-colors hover:bg-accent",
          selected && "border-green/50 bg-green/[0.04]",
        )}
      >
        <Thumbnail keyframeId={result.keyframe_id} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <b className="text-[13px]">{result.camera_name}</b>
            <Badge variant="outline" title={badge.hint} className={TONE[badge.tone]}>
              {badge.label}
            </Badge>
          </div>
          <div className="mt-1 font-mono text-[11px] text-muted-foreground">
            {clock(result.start_time)}–{clock(result.end_time)} · {formatDuration(shownMs(result.manifest))} shown
            {gaps > 0 && ` · ${gaps} gap${gaps === 1 ? "" : "s"}`}
          </div>
          {result.summary && <p className="mt-1 line-clamp-2 text-xs">{result.summary}</p>}
        </div>
      </button>
    </li>
  );
}
