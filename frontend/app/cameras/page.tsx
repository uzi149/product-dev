"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Pencil, Plug, Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { CameraFormDialog } from "@/components/cameras/camera-form-dialog";
import { SegmentsPanel } from "@/components/cameras/segments-panel";
import { UploadCard } from "@/components/cameras/upload-card";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Camera } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function CamerasPage() {
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Camera | null>(null);

  const test = useMutation({
    mutationFn: (camera: Camera) => api.testConnection(camera.id),
    onSuccess: (result, camera) => {
      if (result.ok) {
        toast.success(`${camera.display_name}: connected (${result.width}×${result.height})`);
      } else {
        toast.error(`${camera.display_name}: ${result.error ?? "connection failed"}`);
      }
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const selected = cameras.data?.find((c) => c.id === selectedId) ?? cameras.data?.[0] ?? null;

  return (
    <>
      <PageHeader
        eyebrow="System"
        title="Cameras"
        description="Add live streams or file cameras, upload recordings with their start time, and play back what was stored."
        actions={
          <Button size="lg" onClick={() => setAdding(true)}>
            <Plus /> Add camera
          </Button>
        }
      />

      {cameras.isPending && <p className="text-sm text-muted-foreground">Loading cameras…</p>}
      {cameras.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load cameras: {cameras.error.message}
        </p>
      )}
      {cameras.data?.length === 0 && (
        <div className="grid min-h-[200px] place-items-center rounded-2xl border border-dashed text-center">
          <div>
            <p className="text-sm text-muted-foreground">No cameras yet.</p>
            <Button className="mt-3" onClick={() => setAdding(true)}>
              <Plus /> Add your first camera
            </Button>
          </div>
        </div>
      )}

      {cameras.data && cameras.data.length > 0 && (
        <div className="grid gap-4 xl:grid-cols-[minmax(300px,0.8fr)_minmax(0,1.6fr)]">
          <ul className="grid content-start gap-3">
            {cameras.data.map((camera) => (
              <li
                key={camera.id}
                className={cn(
                  "rounded-2xl border bg-card p-4 transition-colors",
                  selected?.id === camera.id && "border-green/50 bg-green/[0.04]",
                )}
              >
                <button
                  type="button"
                  className="block w-full text-left"
                  onClick={() => setSelectedId(camera.id)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <b className="text-[13px]">{camera.display_name}</b>
                    <div className="flex gap-1.5">
                      <Badge variant="outline">{camera.source_type}</Badge>
                      <Badge variant={camera.enabled ? "secondary" : "outline"}>
                        {camera.enabled ? "enabled" : "disabled"}
                      </Badge>
                    </div>
                  </div>
                  <p className="mt-2 font-mono text-[11px] text-muted-foreground">
                    {camera.source_uri_masked ?? "Receives uploaded files"}
                  </p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {camera.segment_count} segment{camera.segment_count === 1 ? "" : "s"} · kept{" "}
                    {camera.retention_days} days
                  </p>
                </button>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" variant="outline" onClick={() => setEditing(camera)}>
                    <Pencil /> Edit
                  </Button>
                  {camera.source_type === "rtsp" && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={test.isPending && test.variables?.id === camera.id}
                      onClick={() => test.mutate(camera)}
                    >
                      <Plug />
                      {test.isPending && test.variables?.id === camera.id
                        ? "Testing…"
                        : "Test connection"}
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>

          {selected && (
            <div className="grid min-w-0 content-start gap-4">
              <UploadCard key={`upload-${selected.id}`} camera={selected} />
              <SegmentsPanel key={`segments-${selected.id}`} camera={selected} />
            </div>
          )}
        </div>
      )}

      {adding && (
        <CameraFormDialog
          open
          onOpenChange={setAdding}
          onSaved={(camera) => setSelectedId(camera.id)}
        />
      )}
      {editing && (
        <CameraFormDialog
          key={editing.id}
          open
          camera={editing}
          onOpenChange={(open) => !open && setEditing(null)}
        />
      )}
    </>
  );
}
