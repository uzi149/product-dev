"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { localInputToIso, toLocalInputValue } from "@/lib/format";
import type { Camera } from "@/lib/types";

export function UploadCard({ camera }: { camera: Camera }) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [start, setStart] = useState(() => toLocalInputValue(new Date()));
  const [progress, setProgress] = useState(0);

  const upload = useMutation({
    mutationFn: () => {
      const startTimeIso = localInputToIso(start);
      if (!file) throw new Error("Choose an MP4 file first");
      if (!startTimeIso) throw new Error("Enter a valid start date and time");
      setProgress(0);
      return api.uploadVideo({ cameraId: camera.id, startTimeIso, file }, setProgress);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["segments", camera.id] });
      queryClient.invalidateQueries({ queryKey: ["cameras"] });
      toast.success("Video uploaded");
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <section className="rounded-2xl border bg-card p-4">
      <h2 className="mb-1 text-xs font-semibold">Upload an MP4</h2>
      <p className="mb-3 text-[11px] text-muted-foreground">
        The whole file is stored as one segment for {camera.display_name}. Non-H.264 videos are
        converted, and audio is dropped.
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

      <div className="mt-3 flex items-center gap-3">
        <Button onClick={() => upload.mutate()} disabled={upload.isPending || !file}>
          <Upload />
          {upload.isPending
            ? progress >= 1
              ? "Processing…"
              : `Uploading ${Math.round(progress * 100)}%`
            : "Upload"}
        </Button>
        {file && !upload.isPending && (
          <span className="font-mono text-[11px] text-muted-foreground">{file.name}</span>
        )}
      </div>
    </section>
  );
}
