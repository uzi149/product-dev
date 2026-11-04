"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { DeleteTriggerDialog } from "@/components/triggers/delete-trigger-dialog";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api";
import { LIST_POLL_MS } from "@/lib/refresh";
import { STATE_TONE as TONE, percent, ruleWording, triggerState } from "@/lib/triggers";
import type { Trigger } from "@/lib/types";
import { useHydrated } from "@/lib/use-hydrated";
import { cn } from "@/lib/utils";

export default function TriggersPage() {
  const triggers = useQuery({
    queryKey: ["triggers"],
    queryFn: api.triggers,
    // Faster while a rule is being compiled, so "Compiling…" turns into the result.
    refetchInterval: (query) =>
      query.state.data?.some((t) => t.compile_status === "pending") ? 2_000 : LIST_POLL_MS,
  });
  const hydrated = useHydrated();
  const list = hydrated ? triggers.data : undefined;
  const [deleting, setDeleting] = useState<Trigger | null>(null);
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: (t: Trigger) => (t.enabled ? api.disableTrigger(t.id) : api.enableTrigger(t.id)),
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["triggers"] });
      toast.success(`${saved.name} ${saved.enabled ? "enabled" : "disabled"}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <>
      <PageHeader
        eyebrow="Custom triggers"
        title="Triggers"
        description="Describe what should be reported, choose the cameras, test it on stored footage and enable it. Each analysed clip is checked against its own camera's triggers."
        actions={
          <Link href="/triggers/new" className={buttonVariants()}>
            <Plus /> New trigger
          </Link>
        }
      />
      {triggers.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load the triggers: {triggers.error.message}
        </p>
      )}
      {!list && !triggers.isError && <p className="text-sm text-muted-foreground">Loading…</p>}
      {list && list.length === 0 && (
        <div className="rounded-2xl border border-dashed p-8 text-center text-sm text-muted-foreground">
          No triggers yet. Create one, e.g. “Report when someone places an object and leaves it behind”.
        </div>
      )}
      {list && list.length > 0 && (
        <ul className="grid gap-3" aria-label="Triggers">
          {list.map((t) => {
            const state = triggerState(t);
            return (
              <li key={t.id} className="grid gap-2 rounded-2xl border bg-card p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Link href={`/triggers/${t.id}`} className="font-semibold hover:underline">
                    {t.name}
                  </Link>
                  <Badge variant="outline" className={TONE[state.tone]} title={t.compile_error ?? undefined}>
                    {state.label}
                  </Badge>
                  <div className="ml-auto flex flex-wrap gap-1.5">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={t.compile_status !== "done" || toggle.isPending}
                      onClick={() => toggle.mutate(t)}
                    >
                      {t.enabled ? "Disable" : "Enable"}
                    </Button>
                    <Link href={`/triggers/${t.id}`} className={buttonVariants({ size: "sm", variant: "outline" })}>
                      <Pencil /> Edit
                    </Link>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Delete ${t.name}`}
                      onClick={() => setDeleting(t)}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                </div>
                <p className="text-sm text-muted-foreground">“{t.natural_language_rule}”</p>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
                  {t.compiled && (
                    <span>
                      <span className="text-muted-foreground">Understood as </span>
                      {ruleWording(t.compiled)}
                    </span>
                  )}
                  <span>
                    <span className="text-muted-foreground">Cameras </span>
                    {t.cameras.map((c) => c.display_name).join(", ") || "none"}
                  </span>
                  <span>
                    <span className="text-muted-foreground">Confidence </span>
                    {percent(t.minimum_confidence)}
                  </span>
                  {t.compiled && (
                    <span className="text-muted-foreground">
                      {t.compiled.requires_vlm_verification ? "VLM verification on" : "No VLM verification"}
                    </span>
                  )}
                  <Link
                    href={`/events?trigger=${t.id}`}
                    className={cn("hover:underline", t.events.new > 0 && "text-orange")}
                  >
                    {t.events.total === 0
                      ? "No events"
                      : `${t.events.total} event${t.events.total === 1 ? "" : "s"}${t.events.new ? `, ${t.events.new} new` : ""}`}
                  </Link>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {deleting && <DeleteTriggerDialog trigger={deleting} onOpenChange={(open) => !open && setDeleting(null)} />}
    </>
  );
}
