"use client";

import { RotateCw } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

function Failed({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <div role="alert" className="grid place-items-center gap-2 py-6 text-center text-xs text-muted-foreground">
      Could not load the {what}.
      <Button size="sm" variant="outline" onClick={onRetry}>
        <RotateCw /> Retry
      </Button>
    </div>
  );
}

/** A link younger than this has not expired: its failure is shown, not retried on its own. */
const RELOAD_AFTER_MS = 60_000;

/**
 * Plays a stored segment. Its MinIO link expires after an hour, so a video that fails long after
 * it had loaded (paused for long, then seeked) is loaded again at the same position. Any other
 * failure gets a Retry button.
 */
export function SegmentVideo({ segmentId }: { segmentId: string }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const loadedAt = useRef<number | null>(null);
  const resumeAt = useRef(0);

  function reload() {
    loadedAt.current = null;
    setFailed(false);
    setAttempt((a) => a + 1);
  }

  if (failed) return <Failed what="video" onRetry={reload} />;
  return (
    <video
      key={attempt}
      src={api.playbackUrl(segmentId, attempt)}
      controls
      autoPlay
      onLoadedMetadata={(e) => {
        loadedAt.current = Date.now();
        if (resumeAt.current > 0) e.currentTarget.currentTime = resumeAt.current;
        resumeAt.current = 0;
      }}
      onError={(e) => {
        resumeAt.current = e.currentTarget.currentTime;
        const at = loadedAt.current;
        if (at !== null && Date.now() - at > RELOAD_AFTER_MS) reload();
        else setFailed(true);
      }}
      className="mx-auto max-h-[420px] w-full rounded-lg"
    />
  );
}

/** A static period's reference picture, with a Retry button when it fails to load. */
export function ReferenceFrame({ periodId }: { periodId: string }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <Failed
        what="picture"
        onRetry={() => {
          setFailed(false);
          setAttempt((a) => a + 1);
        }}
      />
    );
  }
  return (
    // A redirect to a presigned MinIO URL, not a static asset: a plain <img>.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={api.referenceFrameUrl(periodId, attempt)}
      alt="Reference picture of the unchanged scene"
      onError={() => setFailed(true)}
      className="mx-auto max-h-[360px] rounded-lg"
    />
  );
}
