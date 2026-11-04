"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Download, FileVideo, Trash2, Undo2, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { EvidencePlayer } from "@/components/investigate/evidence-player";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { EXPORT_POLL_MS, STATUS_LABEL, STATUS_TONE, delayWording, exportWording } from "@/lib/events";
import { formatDateTime } from "@/lib/format";
import { clock } from "@/lib/queries";
import { STATE_TONE, actionWord, percent, verificationWording } from "@/lib/triggers";
import type { EventDetail, EventStatus, ExportState } from "@/lib/types";
import { useEventMarks } from "@/lib/use-marks";

export default function EventPage() {
  const { id } = useParams<{ id: string }>();
  const event = useQuery({ queryKey: ["events", "detail", id], queryFn: () => api.event(id) });
  return (
    <>
      <Link href="/events" className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:underline">
        <ArrowLeft className="size-3" /> Events
      </Link>
      {event.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load the event: {event.error.message}
        </p>
      )}
      {!event.data && !event.isError && <p className="text-sm text-muted-foreground">Loading…</p>}
      {event.data && <EventView event={event.data} />}
    </>
  );
}

function EventView({ event }: { event: EventDetail }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [note, setNote] = useState(event.note ?? "");
  const [deleting, setDeleting] = useState(false);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["events"] });
    queryClient.invalidateQueries({ queryKey: ["event-counts"] });
    queryClient.invalidateQueries({ queryKey: ["triggers"] });
  };
  const acknowledge = useMutation({
    mutationFn: (status: EventStatus) => api.acknowledgeEvent(event.id, status, note.trim() || null),
    onSuccess: (saved) => {
      refresh();
      toast.success(saved.status === "new" ? "Back to review" : `Event ${STATUS_LABEL[saved.status].toLowerCase()}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const marks = useEventMarks(event.id);
  const remove = useMutation({
    mutationFn: () => api.deleteEvent(event.id),
    onSuccess: () => {
      refresh();
      toast.success("Event deleted");
      router.push("/events");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const actions = (event.details.matches ?? []).flatMap((m) => m.actions ?? []);
  const delay = delayWording(event.delay_seconds);
  const marksReady = marks.data?.status === "done";

  return (
    <>
      <PageHeader
        eyebrow={event.camera_name}
        title={event.trigger_name}
        description={`${formatDateTime(event.start_time)} – ${clock(event.end_time)}`}
        actions={
          <Button type="button" variant="ghost" aria-label="Delete event" onClick={() => setDeleting(true)}>
            <Trash2 />
          </Button>
        }
      />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(300px,0.9fr)]">
        <section aria-label="Evidence" className="min-w-0 rounded-2xl border bg-card p-4">
          <EvidencePlayer result={event} marks={marks.data} />
        </section>
        <div className="grid content-start gap-4">
          <section aria-label="Event" className="grid gap-2 rounded-2xl border bg-card p-4 text-sm">
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline" className={STATE_TONE[STATUS_TONE[event.status]]}>
                {STATUS_LABEL[event.status]}
              </Badge>
              <Badge variant="outline">{verificationWording(event.verification)}</Badge>
              {event.confidence !== null && <Badge variant="outline">{percent(event.confidence)}</Badge>}
            </div>
            {event.summary && <p>{event.summary}</p>}
            {event.verification_reason && event.verification !== "verified" && (
              <p className="text-xs text-muted-foreground">{event.verification_reason}</p>
            )}
            {actions.length > 0 && (
              <div>
                <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                  Matched actions
                </div>
                <ul className="mt-1 grid gap-0.5 text-xs">
                  {actions.map((a, n) => (
                    <li key={`${a.type}-${a.start}-${n}`}>
                      <span className="font-mono text-muted-foreground">{clock(a.start)}</span> {actionWord(a.type)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="text-xs text-muted-foreground">
              Reported {formatDateTime(event.alerted_at)}
              {delay && ` · ${delay}`}
            </div>
          </section>

          <section aria-label="Review" className="grid gap-2 rounded-2xl border bg-card p-4">
            <Input
              aria-label="Note"
              value={note}
              maxLength={1000}
              placeholder="Note (optional)"
              onChange={(e) => setNote(e.target.value)}
            />
            <div className="flex flex-wrap gap-1.5">
              <Button type="button" disabled={acknowledge.isPending} onClick={() => acknowledge.mutate("confirmed")}>
                <Check /> Confirm
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={acknowledge.isPending}
                onClick={() => acknowledge.mutate("dismissed")}
              >
                <X /> Dismiss
              </Button>
              {event.status !== "new" && (
                <Button
                  type="button"
                  variant="ghost"
                  disabled={acknowledge.isPending}
                  onClick={() => acknowledge.mutate("new")}
                >
                  <Undo2 /> Back to review
                </Button>
              )}
            </div>
            {event.acknowledged_at && (
              <p className="text-xs text-muted-foreground">
                {STATUS_LABEL[event.status]} {formatDateTime(event.acknowledged_at)}
                {event.note && ` · “${event.note}”`}
              </p>
            )}
          </section>

          <section aria-label="Export" className="grid gap-2 rounded-2xl border bg-card p-4">
            <p className="text-xs text-muted-foreground">
              One video of the evidence, with a card where footage between the parts isn&apos;t shown.
            </p>
            <ExportRow eventId={event.id} label="Original" marked={false} />
            <ExportRow
              eventId={event.id}
              label="With marks"
              marked
              disabledReason={marksReady ? null : "Available once the marks are made"}
            />
          </section>
        </div>
      </div>

      {deleting && (
        <Dialog open onOpenChange={setDeleting}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete this event?</DialogTitle>
              <DialogDescription>This can&apos;t be undone.</DialogDescription>
            </DialogHeader>
            <p className="text-sm">
              The event, its evidence list and its export are deleted. The footage stays, but is no longer
              protected from deletion.
            </p>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setDeleting(false)}>
                Cancel
              </Button>
              <Button type="button" variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>
                {remove.isPending ? "Deleting…" : "Delete event"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

/** One export choice (step 5.3; "With marks" step 5.7): Export, then Download when it is ready. */
function ExportRow({
  eventId,
  label,
  marked,
  disabledReason = null,
}: {
  eventId: string;
  label: string;
  marked: boolean;
  disabledReason?: string | null;
}) {
  const queryClient = useQueryClient();
  const key = ["events", "export", eventId, marked];
  const state = useQuery({
    queryKey: key,
    queryFn: () => api.exportState(eventId, marked),
    refetchInterval: (query) => (query.state.data?.status === "running" ? EXPORT_POLL_MS : false),
  });
  const start = useMutation({
    mutationFn: () => api.exportEvent(eventId, marked),
    onSuccess: (saved: ExportState) => queryClient.setQueryData(key, saved),
    onError: (e: Error) => toast.error(e.message),
  });
  const exp = state.data;
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-2">
      <span className="w-24 text-xs font-medium">{label}</span>
      {exp?.status === "done" && exp.url ? (
        <a href={exp.url} download={exp.file_name ?? undefined} className={buttonVariants({ size: "sm" })}>
          <Download /> Download
        </a>
      ) : (
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={exp?.status === "running" || start.isPending || disabledReason !== null}
          onClick={() => start.mutate()}
        >
          <FileVideo /> {exp?.status === "failed" ? "Export again" : "Export"}
        </Button>
      )}
      <span role="status" className="text-xs text-muted-foreground">
        {disabledReason && exp?.status !== "done" ? disabledReason : exportWording(exp)}
      </span>
    </div>
  );
}
