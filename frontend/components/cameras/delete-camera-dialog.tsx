"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
import { api } from "@/lib/api";
import { deleteSummary } from "@/lib/cameras";
import type { Camera } from "@/lib/types";

/** Delete a camera with all its data, after showing what goes (ADR 0026). */
export function DeleteCameraDialog({
  camera,
  onOpenChange,
  onDeleted,
}: {
  camera: Camera;
  onOpenChange: (open: boolean) => void;
  onDeleted: (camera: Camera) => void;
}) {
  const queryClient = useQueryClient();
  // Always fresh: whether it can be deleted changes as recording stops or an upload finishes.
  const preview = useQuery({
    queryKey: ["delete-preview", camera.id],
    queryFn: () => api.deletePreview(camera.id),
    gcTime: 0,
    refetchInterval: 5_000,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteCamera(camera.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cameras"] });
      toast.success(`${camera.display_name} deleted`);
      onDeleted(camera);
      onOpenChange(false);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const blocker = preview.data?.blocker ?? null;
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {camera.display_name}?</DialogTitle>
          <DialogDescription>This can&apos;t be undone.</DialogDescription>
        </DialogHeader>

        {preview.isPending && <p className="text-sm text-muted-foreground">Checking what would be deleted…</p>}
        {preview.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not check: {preview.error.message}
          </p>
        )}
        {preview.data && <p className="text-sm">{deleteSummary(preview.data)}</p>}
        {blocker && (
          <p role="alert" className="rounded-lg border border-orange/45 bg-orange/10 p-3 text-sm text-orange">
            Can&apos;t be deleted now: {blocker}.
          </p>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={!preview.data || blocker !== null || remove.isPending}
            onClick={() => remove.mutate()}
          >
            {remove.isPending ? "Deleting…" : "Delete camera"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
