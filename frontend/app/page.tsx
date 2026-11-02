"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Thumbnail } from "@/components/investigate/result-card";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { STATUS_LABEL, STATUS_TONE } from "@/lib/events";
import { formatDateTime } from "@/lib/format";
import { onlineCount } from "@/lib/live";
import { LIST_POLL_MS } from "@/lib/refresh";
import { STATE_TONE } from "@/lib/triggers";
import { useHydrated } from "@/lib/use-hydrated";
import { cn } from "@/lib/utils";

function Stat({ label, value, detail, href }: { label: string; value: string; detail?: string; href: string }) {
  return (
    <Link href={href} className="grid gap-1 rounded-2xl border bg-card p-4 transition-colors hover:bg-accent">
      <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">{label}</span>
      <b className="text-3xl font-semibold tracking-tight">{value}</b>
      {detail && <span className="text-xs text-muted-foreground">{detail}</span>}
    </Link>
  );
}

function Check({ ok, label }: { ok: boolean | null; label: string }) {
  return (
    <li className="flex items-center gap-2 text-sm">
      <i
        className={cn(
          "size-2 rounded-full",
          ok === null ? "bg-muted-foreground" : ok ? "bg-green" : "bg-red",
        )}
      />
      {label}
    </li>
  );
}

/** Cameras online, active triggers, events to review and the latest events (MVP plan §2). */
export default function OverviewPage() {
  const hydrated = useHydrated();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15_000 });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras, refetchInterval: LIST_POLL_MS });
  const triggers = useQuery({ queryKey: ["triggers"], queryFn: api.triggers, refetchInterval: LIST_POLL_MS });
  const counts = useQuery({ queryKey: ["event-counts"], queryFn: api.eventCounts, refetchInterval: LIST_POLL_MS });
  const latest = useQuery({
    queryKey: ["events", "latest"],
    queryFn: () => api.events({ limit: 5 }),
    refetchInterval: LIST_POLL_MS,
  });

  const cameraList = hydrated ? cameras.data : undefined;
  const live = cameraList ? onlineCount(cameraList) : null;
  const files = cameraList?.filter((c) => c.source_type === "file").length ?? 0;
  const triggerList = hydrated ? triggers.data : undefined;
  const active = triggerList?.filter((t) => t.enabled).length ?? 0;
  const rtsp = cameraList?.filter((c) => c.source_type === "rtsp" && c.enabled) ?? [];
  const ingestion =
    rtsp.length === 0 ? null : !rtsp.some((c) => c.live?.state === "not_running" || c.live?.state === "unknown");
  const h = hydrated ? health.data : undefined;

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title="Overview"
        description="Cameras online, active triggers and the events waiting for review."
      />
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat
          label="Cameras online"
          href="/live"
          value={live ? `${live.online}/${live.total}` : cameraList ? "—" : "…"}
          detail={cameraList ? `${live?.total ?? 0} live, ${files} for uploads` : undefined}
        />
        <Stat
          label="Active triggers"
          href="/triggers"
          value={triggerList ? String(active) : "…"}
          detail={triggerList ? `of ${triggerList.length}` : undefined}
        />
        <Stat
          label="Events to review"
          href="/events"
          value={hydrated && counts.data ? String(counts.data.new) : "…"}
          detail={hydrated && counts.data ? `${counts.data.total} reported` : undefined}
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(260px,1fr)]">
        <section aria-label="Latest events" className="grid content-start gap-2 rounded-2xl border bg-card p-4">
          <h2 className="text-sm font-semibold">Latest events</h2>
          {hydrated && latest.data?.items.length === 0 && (
            <p className="text-sm text-muted-foreground">No events yet.</p>
          )}
          <ul className="grid gap-2">
            {hydrated &&
              latest.data?.items.map((e) => (
                <li key={e.id}>
                  <Link href={`/events/${e.id}`} className="flex items-center gap-3 rounded-xl p-1.5 hover:bg-accent">
                    <Thumbnail keyframeId={e.keyframe_id} />
                    <div className="min-w-0 flex-1">
                      <b className="text-[13px]">{e.trigger_name}</b>
                      <div className="text-xs text-muted-foreground">
                        {e.camera_name} · {formatDateTime(e.start_time)}
                      </div>
                    </div>
                    <Badge variant="outline" className={STATE_TONE[STATUS_TONE[e.status]]}>
                      {STATUS_LABEL[e.status]}
                    </Badge>
                  </Link>
                </li>
              ))}
          </ul>
        </section>
        <section aria-label="System" className="grid content-start gap-2 rounded-2xl border bg-card p-4">
          <h2 className="text-sm font-semibold">System</h2>
          <ul className="grid gap-1.5">
            <Check ok={h ? true : health.isError ? false : null} label={health.isError ? "API not reachable" : "API"} />
            <Check ok={h ? h.database : null} label="Database" />
            <Check ok={h ? h.storage : null} label="Video storage" />
            <Check
              ok={h ? true : null}
              label={
                h
                  ? h.vlm_backend.kind === "api"
                    ? `VLM: ${h.vlm_backend.host}`
                    : "VLM: local model (verification off)"
                  : "VLM"
              }
            />
            <Check
              ok={ingestion}
              label={
                ingestion === null
                  ? "Live ingestion: no live cameras"
                  : ingestion
                    ? "Live ingestion running"
                    : "Live ingestion isn't running"
              }
            />
          </ul>
          <p className="text-xs text-muted-foreground">
            The workers don&apos;t report here: a query that stays waiting, or a trigger that stays
            “Compiling…”, means the query worker isn&apos;t running.
          </p>
        </section>
      </div>
    </>
  );
}
