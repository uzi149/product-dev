"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCw, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { DeleteTriggerDialog } from "@/components/triggers/delete-trigger-dialog";
import { TestPanel } from "@/components/triggers/test-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import {
  STATE_TONE,
  durationWording,
  entitiesWording,
  percent,
  ruleWording,
  triggerState,
} from "@/lib/triggers";
import type { Trigger, TriggerInput } from "@/lib/types";
import { useHydrated } from "@/lib/use-hydrated";
import { cn } from "@/lib/utils";

interface Form {
  name: string;
  rule: string;
  cameraIds: string[];
  confidence: number; // percent
  cooldownMinutes: number;
  preRoll: number;
  postRoll: number;
  verify: boolean | null; // null: as compiled
}

const DEFAULT_FORM: Form = {
  name: "",
  rule: "",
  cameraIds: [],
  confidence: 80,
  cooldownMinutes: 2,
  preRoll: 10,
  postRoll: 20,
  verify: null,
};

function fromTrigger(t: Trigger): Form {
  return {
    name: t.name,
    rule: t.natural_language_rule,
    cameraIds: t.cameras.map((c) => c.id),
    confidence: Math.round(t.minimum_confidence * 100),
    cooldownMinutes: t.cooldown_seconds / 60,
    preRoll: t.pre_roll_seconds,
    postRoll: t.post_roll_seconds,
    verify: null,
  };
}

function Section({ step, title, children }: { step: number; title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="grid gap-3 rounded-2xl border bg-card p-4">
      <h2 className="text-sm font-semibold">
        <span className="mr-2 font-mono text-[11px] text-green">{step}</span>
        {title}
      </h2>
      {children}
    </section>
  );
}

/**
 * The trigger builder (scope §12.1): the rule and name, the cameras, the confidence, cooldown,
 * evidence and verification, then a test on stored footage and Enable. New triggers start
 * disabled and are compiled by the VLM after saving (ADR 0032).
 */
export function TriggerBuilder({ triggerId }: { triggerId: string | null }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const hydrated = useHydrated();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const trigger = useQuery({
    queryKey: ["trigger", triggerId],
    queryFn: () => api.trigger(triggerId!),
    enabled: triggerId !== null,
    refetchInterval: (query) => (query.state.data?.compile_status === "pending" ? 1_500 : false),
  });
  const saved = trigger.data ?? null;

  // The form starts from the saved trigger once it has loaded.
  const [form, setForm] = useState<Form>(DEFAULT_FORM);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  if (saved && loadedFor !== saved.id) {
    setLoadedFor(saved.id);
    setForm(fromTrigger(saved));
  }
  const [deleting, setDeleting] = useState(false);
  const set = (change: Partial<Form>) => setForm((f) => ({ ...f, ...change }));

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["triggers"] });
    queryClient.invalidateQueries({ queryKey: ["trigger", triggerId] });
  };
  const input: TriggerInput = {
    name: form.name.trim(),
    natural_language_rule: form.rule.trim(),
    camera_ids: form.cameraIds,
    minimum_confidence: form.confidence / 100,
    cooldown_seconds: Math.round(form.cooldownMinutes * 60),
    pre_roll_seconds: form.preRoll,
    post_roll_seconds: form.postRoll,
  };
  const save = useMutation({
    mutationFn: async () => {
      if (!saved) return api.createTrigger(input);
      const verify =
        form.verify !== null && saved.compiled && form.verify !== saved.compiled.requires_vlm_verification
          ? { requires_vlm_verification: form.verify }
          : {};
      return api.updateTrigger(saved.id, { ...input, ...verify });
    },
    onSuccess: (result) => {
      refresh();
      set({ verify: null });
      queryClient.setQueryData(["trigger", result.id], result);
      if (!saved) {
        toast.success(`${result.name} saved: the VLM is reading the rule`);
        router.replace(`/triggers/${result.id}`);
      } else {
        toast.success("Changes saved");
      }
    },
  });
  const compile = useMutation({ mutationFn: () => api.compileTrigger(saved!.id), onSuccess: refresh });
  const toggle = useMutation({
    mutationFn: () => (saved!.enabled ? api.disableTrigger(saved!.id) : api.enableTrigger(saved!.id)),
    onSuccess: (t) => {
      queryClient.setQueryData(["trigger", t.id], t);
      refresh();
      toast.success(`${t.name} ${t.enabled ? "enabled: it now runs on its cameras" : "disabled"}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (triggerId && trigger.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load the trigger: {trigger.error.message}
      </p>
    );
  }
  if (triggerId && !saved) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const compiled = saved?.compiled ?? null;
  const verifyShown = form.verify ?? compiled?.requires_vlm_verification ?? true;
  const valid = input.name !== "" && input.natural_language_rule !== "" && input.camera_ids.length > 0;
  const state = saved ? triggerState(saved) : null;

  return (
    <div className="grid gap-4">
      {saved && state && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className={STATE_TONE[state.tone]}>
            {state.label}
          </Badge>
          <div className="ml-auto flex gap-1.5">
            <Button
              type="button"
              variant={saved.enabled ? "outline" : "default"}
              disabled={saved.compile_status !== "done" || toggle.isPending}
              onClick={() => toggle.mutate()}
            >
              {saved.enabled ? "Disable" : "Enable"}
            </Button>
            <Button type="button" variant="ghost" aria-label="Delete trigger" onClick={() => setDeleting(true)}>
              <Trash2 />
            </Button>
          </div>
        </div>
      )}

      <Section step={1} title="Rule and name">
        <div className="grid gap-1">
          <Label htmlFor="trigger-name" className="text-[10px]">Name</Label>
          <Input
            id="trigger-name"
            value={form.name}
            maxLength={200}
            placeholder="Object left behind"
            onChange={(e) => set({ name: e.target.value })}
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="trigger-rule" className="text-[10px]">Report when…</Label>
          <textarea
            id="trigger-rule"
            value={form.rule}
            maxLength={1000}
            rows={2}
            placeholder="Report when someone places an object and leaves it behind"
            onChange={(e) => set({ rule: e.target.value })}
            className="rounded-lg border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          />
        </div>
        <div aria-label="Understood as" className="rounded-xl border border-dashed p-3 text-sm">
          {!saved && (
            <span className="text-muted-foreground">
              After saving, the VLM reads the rule (text only) and says how it will be matched.
            </span>
          )}
          {saved?.compile_status === "pending" && (
            <span className="text-orange">Compiling… the VLM is reading the rule.</span>
          )}
          {saved?.compile_status === "failed" && (
            <div className="flex flex-wrap items-center gap-2">
              <span role="alert" className="text-destructive">
                Couldn&apos;t compile: {saved.compile_error}
              </span>
              <Button type="button" size="sm" variant="outline" disabled={compile.isPending} onClick={() => compile.mutate()}>
                <RotateCw /> Compile again
              </Button>
            </div>
          )}
          {saved?.compile_status === "done" && compiled && (
            <div className="grid gap-1">
              <div>
                <span className="text-muted-foreground">Understood as </span>
                <b>{ruleWording(compiled)}</b>
              </div>
              {compiled.required_entities.length > 0 && (
                <div className="text-xs text-muted-foreground">Needs: {entitiesWording(compiled)}</div>
              )}
              <div className="text-xs text-muted-foreground">
                {compiled.mode === "structured"
                  ? "Matched on the actions the analysis reports, in order."
                  : "Matched by similarity to the descriptions of each clip."}
              </div>
            </div>
          )}
        </div>
      </Section>

      <Section step={2} title="Cameras">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Trigger cameras">
          {hydrated &&
            cameras.data?.map((c) => {
              const on = form.cameraIds.includes(c.id);
              return (
                <Button
                  key={c.id}
                  type="button"
                  size="sm"
                  variant={on ? "secondary" : "outline"}
                  aria-pressed={on}
                  onClick={() =>
                    set({ cameraIds: on ? form.cameraIds.filter((x) => x !== c.id) : [...form.cameraIds, c.id] })
                  }
                >
                  {c.display_name}
                </Button>
              );
            })}
        </div>
        {cameras.data?.length === 0 && (
          <p className="text-xs text-muted-foreground">No cameras yet: add one on the Cameras page.</p>
        )}
        <p className="text-xs text-muted-foreground">The trigger only runs on these cameras.</p>
      </Section>

      <Section step={3} title="Confidence, cooldown and evidence">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="grid gap-1">
            <Label htmlFor="trigger-confidence" className="text-[10px]">
              Minimum confidence: {form.confidence} %
            </Label>
            <input
              id="trigger-confidence"
              type="range"
              min={0}
              max={100}
              step={5}
              value={form.confidence}
              onChange={(e) => set({ confidence: Number(e.target.value) })}
              className="accent-[var(--green)]"
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="trigger-cooldown" className="text-[10px]">
              Cooldown (minutes): a new match within it extends the event
            </Label>
            <Input
              id="trigger-cooldown"
              type="number"
              min={0}
              max={60}
              step={0.5}
              value={form.cooldownMinutes}
              onChange={(e) => set({ cooldownMinutes: Number(e.target.value) })}
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="trigger-pre" className="text-[10px]">Evidence before (seconds)</Label>
            <Input
              id="trigger-pre"
              type="number"
              min={0}
              max={60}
              value={form.preRoll}
              onChange={(e) => set({ preRoll: Number(e.target.value) })}
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="trigger-post" className="text-[10px]">Evidence after (seconds)</Label>
            <Input
              id="trigger-post"
              type="number"
              min={0}
              max={120}
              value={form.postRoll}
              onChange={(e) => set({ postRoll: Number(e.target.value) })}
            />
          </div>
        </div>
        <label className={cn("flex items-center gap-2 text-sm", !compiled && "opacity-60")}>
          <input
            type="checkbox"
            checked={verifyShown}
            disabled={!compiled}
            onChange={(e) => set({ verify: e.target.checked })}
          />
          The VLM verifies a match before it is reported
          {!compiled && <span className="text-xs text-muted-foreground">(after the rule is compiled)</span>}
        </label>
        <p className="text-xs text-muted-foreground">
          Reported {percent(input.minimum_confidence)} or more; the alert waits for the{" "}
          {durationWording(form.postRoll)} after the action to be stored.
        </p>
        <div className="flex items-center gap-3">
          <Button type="button" disabled={!valid || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : saved ? "Save changes" : "Create trigger"}
          </Button>
          {!valid && (
            <span className="text-xs text-muted-foreground">A name, a rule and at least one camera are needed.</span>
          )}
        </div>
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            {save.error.message}
          </p>
        )}
      </Section>

      <Section step={4} title="Test and enable">
        {!saved && <p className="text-sm text-muted-foreground">Create the trigger first.</p>}
        {saved && saved.compile_status !== "done" && (
          <p className="text-sm text-muted-foreground">The rule must be compiled before it can be tested.</p>
        )}
        {saved && saved.compile_status === "done" && <TestPanel trigger={saved} />}
      </Section>

      {deleting && saved && (
        <DeleteTriggerDialog
          trigger={saved}
          onOpenChange={setDeleting}
          onDeleted={() => router.push("/triggers")}
        />
      )}
    </div>
  );
}
