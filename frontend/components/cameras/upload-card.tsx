"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { localInputToIso, toLocalInputValue } from "@/lib/format";
import type { Camera } from "@/lib/types";
import { isFinished, pollWhileRunning, uploadSummary } from "@/lib/uploads";
import { cn } from "@/lib/utils";

export function UploadCard({ camera }: { camera: Camera }) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [start, setStart] = useState(() => toLocalInputValue(new Date()));
  const [progress, setProgress] = useState(0);
  // The upload sent from this card, followed until its background ingestion finishes.
  const [watchingId, setWatchingId] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: () => {
      const startTimeIso = localInputToIso(start);
      if (!file) throw new Error("Choose a video file first");
      if (!startTimeIso) throw new Error("Enter a valid start date and time");
      setProgress(0);
      return api.uploadVideo({ cameraId: camera.id, startTimeIso, file }, setProgress);
    },
    onSuccess: (queued) => {
      setWatchingId(queued.id);
      queryClient.invalidateQueries({ queryKey: ["uploads", camera.id] });
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const watched = useQuery({
    queryKey: ["upload", watchingId],
    queryFn: () => api.upload(watchingId!),
    enabled: watchingId !== null,
    refetchInterval: (query) => pollWhileRunning(query.state.data),
  });

  const finished = watched.data && isFinished(watched.data) ? watched.data : null;
  const announced = useRef<string | null>(null);
  useEffect(() => {
    if (!finished || announced.current === finished.id) return;
    announced.current = finished.id;
    queryClient.invalidateQueries({ queryKey: ["segments", camera.id] });
    queryClient.invalidateQueries({ queryKey: ["static-periods", camera.id] });
    queryClient.invalidateQueries({ queryKey: ["uploads", camera.id] });
    queryClient.invalidateQueries({ queryKey: ["cameras"] });
    const message = `${finished.filename}: ${uploadSummary(finished)}`;
    if (finished.status === "done") toast.success(message);
    else toast.error(message);
  }, [finished, camera.id, queryClient]);

  const busy = upload.isPending || (watched.data !== undefined && !isFinished(watched.data));

  return (
    <section className="rounded-2xl border bg-card p-4">
      <h2 className="mb-1 text-xs font-semibold">Upload a video</h2>
      <p className="mb-3 text-[11px] text-muted-foreground">
        Only the parts where something happens are stored for {camera.display_name}, with 10 s
        before and after each. Processing runs in the background.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-1.5">
          <Label htmlFor="upload-file">Video file</Label>
          <Input
            id="upload-file"
            ref={fileInput}
            type="file"
            accept="video/*,.mp4,.mov,.mkv,.avi"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="upload-start">Recording started (your local time)</Label>
          <Input
            id="upload-start"
            type="datetime-local"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </div>
      </div>

      {upload.isPending && (
        <div
          className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-valuenow={Math.round(progress * 100)}
        >
          <div className="h-full bg-green transition-all" style={{ width: `${progress * 100}%` }} />
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button onClick={() => upload.mutate()} disabled={busy || !file}>
          <Upload />
          {upload.isPending
            ? progress >= 1
              ? "Checking the file…"
              : `Uploading ${Math.round(progress * 100)}%`
            : "Upload"}
        </Button>
        {file && !upload.isPending && (
          <span className="font-mono text-[11px] text-muted-foreground">{file.name}</span>
        )}
        {watched.data && (
          <span
            role="status"
            className={cn(
              "text-[11px]",
              watched.data.status === "failed" ? "text-destructive" : "text-muted-foreground",
            )}
          >
            {watched.data.filename}: {uploadSummary(watched.data)}
          </span>
        )}
      </div>
    </section>
  );
}
