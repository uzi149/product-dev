"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Camera } from "@/lib/types";
import { pollWhileRunning, uploadSummary } from "@/lib/uploads";

export function RecentUploads({ camera }: { camera: Camera }) {
  const uploads = useQuery({
    queryKey: ["uploads", camera.id],
    queryFn: () => api.uploads(camera.id),
    refetchInterval: (query) => pollWhileRunning(query.state.data),
  });

  if (!uploads.data?.length) return null;

  return (
    <section className="overflow-hidden rounded-2xl border bg-card">
      <h2 className="border-b px-4 py-3 text-xs font-semibold">Recent uploads</h2>
      <ul>
        {uploads.data.map((upload) => (
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
            <Badge variant={upload.status === "failed" ? "destructive" : "outline"}>
              {upload.status}
            </Badge>
          </li>
        ))}
      </ul>
    </section>
  );
}
