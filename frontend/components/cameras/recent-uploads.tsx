"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { invalidateCameraData, LIST_POLL_MS } from "@/lib/refresh";
import { STATUS_TONE } from "@/lib/status";
import type { Camera, Upload } from "@/lib/types";
import {
  newlyFinished,
  pollWhileRunning,
  RECENT_UPLOADS,
  recentUploads,
  rejectedUploadsKey,
  uploadStatus,
  uploadSummary,
} from "@/lib/uploads";

export function RecentUploads({ camera }: { camera: Camera }) {
  const queryClient = useQueryClient();
  const uploads = useQuery({
    queryKey: ["uploads", camera.id],
    queryFn: () => api.uploads(camera.id),
    refetchInterval: (query) => pollWhileRunning(query.state.data) || LIST_POLL_MS,
  });
  // Files the API refused, added by the upload card. Kept in this browser until a reload.
  const rejected = useQuery({
    queryKey: rejectedUploadsKey(camera.id),
    // Nothing to fetch: a refetch (an invalidation) keeps what is there instead of emptying it.
    queryFn: () => queryClient.getQueryData<Upload[]>(rejectedUploadsKey(camera.id)) ?? [],
    initialData: [] as Upload[],
    staleTime: Infinity,
    gcTime: Infinity,
  });

  // An upload that finished, from this tab or elsewhere, has stored new footage.
  const previous = useRef<Upload[] | undefined>(undefined);
  useEffect(() => {
    if (!uploads.data) return;
    const finished = newlyFinished(previous.current, uploads.data);
    previous.current = uploads.data;
    if (finished.length > 0) invalidateCameraData(queryClient, camera.id);
  }, [uploads.data, camera.id, queryClient]);

  const list = recentUploads(uploads.data ?? [], rejected.data, RECENT_UPLOADS);
  if (!list.length) return null;

  return (
    <section className="overflow-hidden rounded-2xl border bg-card">
      <h2 className="border-b px-4 py-3 text-xs font-semibold">Recent uploads</h2>
      <ul>
        {list.map((upload) => (
          <li
            key={upload.id}
            className="grid grid-cols-[1fr_auto] items-center gap-3 border-b px-4 py-2.5 last:border-0"
          >
            <div className="min-w-0">
              <div className="truncate font-mono text-xs">{upload.filename}</div>
              <div className="mt-1 text-[11px] text-muted-foreground">
                Recorded {formatDateTime(upload.start_time)} · {uploadSummary(upload)}
              </div>
            </div>
            <Badge
              variant="outline"
              title={uploadStatus(upload).hint}
              className={STATUS_TONE[upload.status]}
            >
              {uploadStatus(upload).label}
            </Badge>
          </li>
        ))}
      </ul>
    </section>
  );
}
