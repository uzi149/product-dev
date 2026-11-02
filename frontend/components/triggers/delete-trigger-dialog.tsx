"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import type { Trigger } from "@/lib/types";

/** Delete a trigger with its events and tests, after saying how many events go (ADR 0034). */
export function DeleteTriggerDialog({
  trigger,
  onOpenChange,
  onDeleted,
}: {
  trigger: Trigger;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.deleteTrigger(trigger.id),
    onSuccess: () => {
      for (const key of ["triggers", "events", "event-counts"]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
      toast.success(`${trigger.name} deleted`);
      onOpenChange(false);
      onDeleted?.();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const events = trigger.events.total;
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {trigger.name}?</DialogTitle>
          <DialogDescription>This can&apos;t be undone.</DialogDescription>
        </DialogHeader>
        <p className="text-sm">
          {events > 0
            ? `Its ${events === 1 ? "event" : `${events} events`} and their exports will be deleted too. The footage stays.`
            : "It has no events. Its tests will be deleted too."}
        </p>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>
            {remove.isPending ? "Deleting…" : "Delete trigger"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
