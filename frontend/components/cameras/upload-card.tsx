"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import { localInputToIso, toLocalInputValue } from "@/lib/format";
import { invalidateCameraData } from "@/lib/refresh";
import type { Camera, Upload as UploadRow } from "@/lib/types";
import {
  isFinished,
  pollWhileRunning,
  rejectedUpload,
  rejectedUploadsKey,
  UPLOAD_POLL_MS,
  uploadSummary,
} from "@/lib/uploads";
import { cn } from "@/lib/utils";

export function UploadCard({ camera }: { camera: Camera }) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [start, setStart] = useState(() => toLocalInputValue(new Date()));
  const [progress, setProgress] = useState(0);
  // The last upload sent from this card, followed until its background ingestion finishes. The
  // button only waits for the file to be sent (ADR 0018): earlier uploads go on in Recent uploads.
  const [watchingId, setWatchingId] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: (input: { file: File; startTimeIso: string }) => {
      setProgress(0);
      return api.uploadVideo({ cameraId: camera.id, ...input }, setProgress);
    },
    onSuccess: (queued) => {
      setWatchingId(queued.id);
      queryClient.invalidateQueries({ queryKey: ["uploads", camera.id] });
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
    },
    onError: (e: Error, input) => {
      toast.error(e.message);
      queryClient.invalidateQueries({ queryKey: ["uploads", camera.id] });
      // A 503 (job queue unreachable) already has its failed row in the API's list.
      if (e instanceof ApiError && e.status === 503) return;
      const rejected = rejectedUpload({
        cameraId: camera.id,
        filename: input.file.name,
        startTimeIso: input.startTimeIso,
        error: e.message,
      });
      queryClient.setQueryData<UploadRow[]>(rejectedUploadsKey(camera.id), (old = []) => [rejected, ...old]);
    },
  });

  function send() {
    const startTimeIso = localInputToIso(start);
    if (!file) toast.error("Choose a video file first");
    else if (!startTimeIso) toast.error("Enter a valid start date and time");
    else upload.mutate({ file, startTimeIso });
  }

  const watched = useQuery({
    queryKey: ["upload", watchingId],
    queryFn: () => api.upload(watchingId!),
    enabled: watchingId !== null,
    // Without an answer yet (the first request failed), keep asking until the status is known.
    refetchInterval: (query) => (query.state.data ? pollWhileRunning(query.state.data) : UPLOAD_POLL_MS),
  });

  const finished = watched.data && isFinished(watched.data) ? watched.data : null;
  const announced = useRef<string | null>(null);
  useEffect(() => {
    if (!finished || announced.current === finished.id) return;
    announced.current = finished.id;
    invalidateCameraData(queryClient, camera.id);
    const message = `${finished.filename}: ${uploadSummary(finished)}`;
    if (finished.status === "done") toast.success(message);
    else toast.error(message);
  }, [finished, camera.id, queryClient]);

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
        <Button onClick={send} disabled={upload.isPending || !file}>
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
