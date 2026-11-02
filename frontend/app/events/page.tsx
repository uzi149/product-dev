"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { Thumbnail } from "@/components/investigate/result-card";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { STATUS_FILTERS, STATUS_LABEL, STATUS_TONE, type StatusFilter, filterLabel } from "@/lib/events";
import { formatDateTime } from "@/lib/format";
import { LIST_POLL_MS } from "@/lib/refresh";
import { STATE_TONE, percent, verificationWording } from "@/lib/triggers";
import { useHydrated } from "@/lib/use-hydrated";

const PAGE = 50;

export default function EventsPage() {
  // useSearchParams needs a Suspense boundary when the page is pre-rendered.
  return (
    <Suspense>
      <EventsView />
    </Suspense>
  );
}

function EventsView() {
  const router = useRouter();
  const params = useSearchParams();
  // The filters live in the URL (?status=, ?camera=, ?trigger=), so a trigger's "events" link
  // and a reload show the same list. New events are shown first: they are the ones to review.
  const status = (params.get("status") as StatusFilter | null) ?? "new";
  const cameraId = params.get("camera");
  const triggerId = params.get("trigger");
  function setFilter(key: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    router.replace(`/events${next.size ? `?${next}` : ""}`);
  }

  const hydrated = useHydrated();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const triggers = useQuery({ queryKey: ["triggers"], queryFn: api.triggers });
  const events = useInfiniteQuery({
    queryKey: ["events", status, cameraId, triggerId],
    queryFn: ({ pageParam }) =>
      api.events({
        status: status === "all" ? null : status,
        cameraId,
        triggerId,
        limit: PAGE,
        offset: pageParam,
      }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
    refetchInterval: LIST_POLL_MS,
  });
  const items = hydrated ? [...new Map((events.data?.pages ?? []).flatMap((p) => p.items).map((e) => [e.id, e])).values()] : [];
  const total = events.data?.pages[0]?.total ?? 0;

  return (
    <>
      <PageHeader
        eyebrow="Reported events"
        title="Events"
        description="What the triggers reported, with their evidence. Confirm or dismiss each one; export the evidence as one video."
      />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="flex gap-1.5" role="group" aria-label="Status">
          {STATUS_FILTERS.map((f) => (
            <Button
              key={f}
              type="button"
              size="sm"
              variant={status === f ? "secondary" : "outline"}
              aria-pressed={status === f}
              onClick={() => setFilter("status", f === "new" ? null : f)}
            >
              {filterLabel(f)}
            </Button>
          ))}
        </div>
        <select
          aria-label="Camera"
          value={cameraId ?? ""}
          onChange={(e) => setFilter("camera", e.target.value || null)}
          className="h-7 rounded-lg border bg-background px-2 text-xs"
        >
          <option value="">All cameras</option>
          {hydrated &&
            cameras.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.display_name}
              </option>
            ))}
        </select>
        <select
          aria-label="Trigger"
          value={triggerId ?? ""}
          onChange={(e) => setFilter("trigger", e.target.value || null)}
          className="h-7 rounded-lg border bg-background px-2 text-xs"
        >
          <option value="">All triggers</option>
          {hydrated &&
            triggers.data?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
        </select>
        {hydrated && events.data && <span className="text-xs text-muted-foreground">{total} events</span>}
      </div>

      {events.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load the events: {events.error.message}
        </p>
      )}
      {hydrated && events.data && items.length === 0 && (
        <div className="rounded-2xl border border-dashed p-8 text-center text-sm text-muted-foreground">
          {status === "new" ? "Nothing to review." : "No events."}
        </div>
      )}
      {items.length > 0 && (
        <ul className="grid gap-2" aria-label="Events">
          {items.map((e) => (
            <li key={e.id}>
              <Link
                href={`/events/${e.id}`}
                className="flex gap-3 rounded-xl border bg-card p-3 transition-colors hover:bg-accent"
              >
                <Thumbnail keyframeId={e.keyframe_id} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <b className="text-[13px]">{e.trigger_name}</b>
                    <span className="text-xs text-muted-foreground">{e.camera_name}</span>
                    <Badge variant="outline" className={`ml-auto ${STATE_TONE[STATUS_TONE[e.status]]}`}>
                      {STATUS_LABEL[e.status]}
                    </Badge>
                  </div>
                  <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                    {formatDateTime(e.start_time)}
                    {e.confidence !== null && ` · ${percent(e.confidence)}`} · {verificationWording(e.verification)}
                  </div>
                  {e.summary && <p className="mt-1 line-clamp-2 text-xs">{e.summary}</p>}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {events.hasNextPage && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="mt-3"
          disabled={events.isFetchingNextPage}
          onClick={() => events.fetchNextPage()}
        >
          {events.isFetchingNextPage ? "Loading…" : "Show more"}
        </Button>
      )}
    </>
  );
}
