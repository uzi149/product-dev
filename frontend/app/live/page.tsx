"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { CameraTile } from "@/components/live/camera-tile";
import { PageHeader } from "@/components/page-header";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api";
import { ingestionStopped, LIVE_POLL_MS, liveCameras, onlineCount } from "@/lib/live";
import { cn } from "@/lib/utils";

export default function LivePage() {
  const cameras = useQuery({
    queryKey: ["cameras"],
    queryFn: api.cameras,
    refetchInterval: LIVE_POLL_MS,
  });
  // Ticks every second so "updated 3 s ago" stays true between status reloads.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const all = cameras.data ?? [];
  const live = liveCameras(all);
  const files = all.length - live.length;
  const { online, total } = onlineCount(all);

  return (
    <>
      <PageHeader
        eyebrow="Live operations"
        title="Camera wall"
        description="The latest picture from every live camera, refreshed every 5 seconds. Only the footage where something happens is stored. Open a camera to see its timeline."
        actions={
          total > 0 && (
            <div className="rounded-full border px-3 py-1.5 font-mono text-[11px] text-muted-foreground">
              {online} / {total} online
            </div>
          )
        }
      />

      {cameras.isPending && <p className="text-sm text-muted-foreground">Loading cameras…</p>}
      {cameras.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load cameras: {cameras.error.message}
        </p>
      )}

      {ingestionStopped(all) && (
        <div
          role="alert"
          className="mb-4 rounded-xl border border-orange/30 bg-orange/10 px-4 py-3 text-xs text-[#ffb27f]"
        >
          Live ingestion is not running, so no live camera is watched and nothing is stored. Start it
          with <code className="font-mono">python -m services.ingestion</code>.
        </div>
      )}

      {cameras.data && live.length === 0 && (
        <div className="grid min-h-[220px] place-items-center rounded-2xl border border-dashed text-center">
          <div>
            <p className="text-sm text-muted-foreground">No live cameras yet.</p>
            <Link href="/cameras" className={cn(buttonVariants(), "mt-3")}>
              <Plus /> Add an RTSP camera
            </Link>
          </div>
        </div>
      )}

      {live.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {live.map((camera) => (
            <CameraTile key={camera.id} camera={camera} now={now} />
          ))}
        </div>
      )}

      {files > 0 && (
        <p className="mt-4 text-[11px] text-muted-foreground">
          {files} file camera{files === 1 ? "" : "s"} not shown: {files === 1 ? "it receives" : "they receive"}{" "}
          uploaded videos. See{" "}
          <Link href="/cameras" className="underline underline-offset-2 hover:text-foreground">
            Cameras
          </Link>
          .
        </p>
      )}
    </>
  );
}
