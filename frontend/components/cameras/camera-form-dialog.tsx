"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { Camera, SourceType } from "@/lib/types";

/** Add a camera, or edit one when `camera` is given. Remount (key) to reset the fields. */
export function CameraFormDialog({
  open,
  onOpenChange,
  camera,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  camera?: Camera;
  onSaved?: (camera: Camera) => void;
}) {
  const queryClient = useQueryClient();
  const editing = camera !== undefined;

  const [name, setName] = useState(camera?.display_name ?? "");
  const [sourceType, setSourceType] = useState<SourceType>(camera?.source_type ?? "file");
  const [uri, setUri] = useState("");
  const [retention, setRetention] = useState(String(camera?.retention_days ?? 14));
  const [enabled, setEnabled] = useState(camera?.enabled ?? true);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      const retention_days = Number(retention);
      if (!Number.isInteger(retention_days) || retention_days < 1) {
        throw new Error("Retention must be a whole number of days, 1 or more");
      }
      if (editing) {
        return api.updateCamera(camera.id, {
          display_name: name,
          enabled,
          retention_days,
          // The stored URL is write-only: only send it when a new one was typed.
          ...(uri.trim() ? { source_uri: uri.trim() } : {}),
        });
      }
      return api.createCamera({
        display_name: name,
        source_type: sourceType,
        source_uri: sourceType === "rtsp" ? uri.trim() : null,
        enabled,
        retention_days,
      });
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["cameras"] });
      toast.success(editing ? "Camera updated" : "Camera added");
      onSaved?.(saved);
      onOpenChange(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  const showUri = editing ? camera.source_type === "rtsp" : sourceType === "rtsp";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setError(null);
            save.mutate();
          }}
          className="grid gap-4"
        >
          <DialogHeader>
            <DialogTitle>{editing ? "Edit camera" : "Add camera"}</DialogTitle>
            <DialogDescription>
              {editing
                ? "Change how this camera is stored and analysed."
                : "A file camera receives uploaded MP4s. An RTSP camera is a live stream."}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-1.5">
            <Label htmlFor="camera-name">Name</Label>
            <Input
              id="camera-name"
              required
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Front door"
            />
          </div>

          {!editing && (
            <div className="grid gap-1.5">
              <Label htmlFor="camera-type">Type</Label>
              <select
                id="camera-type"
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value as SourceType)}
                className="h-8 rounded-lg border border-input bg-popover px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <option value="file">File (uploaded MP4s)</option>
                <option value="rtsp">RTSP (live stream)</option>
              </select>
            </div>
          )}

          {showUri && (
            <div className="grid gap-1.5">
              <Label htmlFor="camera-uri">Stream URL</Label>
              <Input
                id="camera-uri"
                required={!editing}
                value={uri}
                onChange={(e) => setUri(e.target.value)}
                placeholder={
                  editing ? `Unchanged (${camera.source_uri_masked ?? "none"})` : "rtsp://user:password@host:554/stream"
                }
                autoComplete="off"
              />
              <p className="text-xs text-muted-foreground">
                Stored encrypted and never shown again; only a masked form is displayed.
              </p>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="camera-retention">Keep footage (days)</Label>
              <Input
                id="camera-retention"
                inputMode="numeric"
                value={retention}
                onChange={(e) => setRetention(e.target.value)}
              />
            </div>
            <label className="flex items-end gap-2 pb-1.5 text-sm">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                className="size-4 accent-[var(--green)]"
              />
              Enabled
            </label>
          </div>

          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? "Saving…" : editing ? "Save changes" : "Add camera"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
