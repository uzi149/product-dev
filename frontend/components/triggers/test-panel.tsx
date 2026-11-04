"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { EvidencePlayer } from "@/components/investigate/evidence-player";
import { Thumbnail } from "@/components/investigate/result-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { formatDateTime, localInputToIso } from "@/lib/format";
import { clock } from "@/lib/queries";
import {
  TEST_POLL_MS,
  VERDICT_LABEL,
  finishedTest,
  percent,
  sequenceWording,
  sourceWording,
  testWording,
  verificationWording,
} from "@/lib/triggers";
import type { TestMatch, TestSource, Trigger, Verdict } from "@/lib/types";
import { cn } from "@/lib/utils";

const SOURCES: { value: TestSource; label: string }[] = [
  { value: "video", label: "An uploaded video" },
  { value: "last_hour", label: "Last hour" },
  { value: "last_24h", label: "Last 24 hours" },
  { value: "range", label: "Custom range" },
];

const VERDICT_TONE: Record<Verdict, string> = {
  would_report: "border-green/45 bg-green/10 text-green",
  rejected: "border-red/45 bg-red/15 text-red",
  below_confidence: "text-muted-foreground",
};

/**
 * Step 4 of the builder (scope §12.4, ADR 0035): run the trigger's current rule on stored footage
 * and see what it would have reported: would report, rejected by the VLM, below the confidence.
 */
export function TestPanel({ trigger }: { trigger: Trigger }) {
  const queryClient = useQueryClient();
  const [source, setSource] = useState<TestSource>("video");
  const [uploadId, setUploadId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [testId, setTestId] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  const uploads = useQuery({ queryKey: ["uploads", "all"], queryFn: api.allUploads });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const usable = (uploads.data ?? []).filter((u) => u.status === "done" && (u.segments_created ?? 0) > 0);
  const cameraName = (id: string) => cameras.data?.find((c) => c.id === id)?.display_name ?? "?";

  // The latest test of this trigger, unless one was just started here.
  const latest = useQuery({ queryKey: ["trigger-tests", trigger.id], queryFn: () => api.triggerTests(trigger.id) });
  const shownId = testId ?? latest.data?.[0]?.id ?? null;
  const test = useQuery({
    queryKey: ["trigger-test", trigger.id, shownId],
    queryFn: () => api.triggerTest(trigger.id, shownId!),
    enabled: shownId !== null,
    refetchInterval: (query) => (finishedTest(query.state.data) ? false : TEST_POLL_MS),
  });

  const run = useMutation({
    mutationFn: () =>
      api.testTrigger(trigger.id, {
        source,
        upload_id: source === "video" ? uploadId || null : null,
        start: source === "range" ? localInputToIso(from) : null,
        end: source === "range" ? localInputToIso(to) : null,
      }),
    onSuccess: (started) => {
      setTestId(started.id);
      setSelected(null);
      queryClient.invalidateQueries({ queryKey: ["trigger-tests", trigger.id] });
    },
  });

  const ready = source !== "video" || uploadId !== "";
  const result = test.data;
  const matches = result?.matches ?? [];
  const playing: TestMatch | null = selected !== null ? (matches[selected] ?? null) : null;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Test on">
        {SOURCES.map((s) => (
          <Button
            key={s.value}
            type="button"
            size="sm"
            variant={source === s.value ? "secondary" : "outline"}
            aria-pressed={source === s.value}
            onClick={() => setSource(s.value)}
          >
            {s.label}
          </Button>
        ))}
      </div>
      {source === "video" && (
        <div className="grid gap-1">
          <Label htmlFor="test-upload" className="text-[10px]">
            Uploaded video (any camera)
          </Label>
          <select
            id="test-upload"
            value={uploadId}
            onChange={(e) => setUploadId(e.target.value)}
            className="h-8 rounded-lg border bg-background px-2 text-sm"
          >
            <option value="">Choose a video…</option>
            {usable.map((u) => (
              <option key={u.id} value={u.id}>
                {u.filename} · {cameraName(u.camera_id)} · {formatDateTime(u.start_time)}
              </option>
            ))}
          </select>
          {uploads.data && usable.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No uploaded video with stored footage yet: upload one on the Cameras page.
            </p>
          )}
        </div>
      )}
      {source === "range" && (
        <div className="flex flex-wrap gap-3">
          <div className="grid gap-1">
            <Label htmlFor="test-from" className="text-[10px]">From</Label>
            <Input id="test-from" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="test-to" className="text-[10px]">To</Label>
            <Input id="test-to" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
        </div>
      )}
      <div>
        <Button type="button" disabled={!ready || run.isPending} onClick={() => run.mutate()}>
          <FlaskConical /> {run.isPending ? "Starting…" : "Run test"}
        </Button>
        {run.isError && (
          <p role="alert" className="mt-2 text-sm text-destructive">
            {run.error.message}
          </p>
        )}
      </div>

      {result && (
        <section aria-label="Test result" className="grid gap-3 rounded-xl border p-3">
          <div className="text-xs text-muted-foreground">
            {sourceWording(result)} · {formatDateTime(result.start)} – {formatDateTime(result.end)} ·
            confidence {percent(result.minimum_confidence)}
          </div>
          <p role="status" className={cn("text-sm", result.status === "failed" && "text-destructive")}>
            {testWording(result)}
          </p>
          {result.status === "done" && matches.length > 0 && (
            <div className="grid gap-3 xl:grid-cols-[minmax(280px,0.9fr)_minmax(0,1.4fr)]">
              <ul className="grid content-start gap-2" aria-label="Would-be events">
                {matches.map((m, n) => (
                  <li key={`${n}-${m.camera_id}-${m.start_time}`}>
                    <button
                      type="button"
                      aria-pressed={selected === n}
                      onClick={() => setSelected(n)}
                      className={cn(
                        "flex w-full gap-3 rounded-xl border bg-card p-2.5 text-left hover:bg-accent",
                        m.verdict !== "would_report" && "opacity-70",
                        selected === n && "border-green/50",
                      )}
                    >
                      <Thumbnail keyframeId={m.keyframe_id} />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <b className="text-[13px]">{m.camera_name}</b>
                          <Badge variant="outline" className={VERDICT_TONE[m.verdict]}>
                            {VERDICT_LABEL[m.verdict]}
                          </Badge>
                        </div>
                        <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                          {clock(m.start_time)}–{clock(m.end_time)} · {percent(m.confidence)}
                          {m.actions.length > 0 && ` · ${sequenceWording(m.actions.map((a) => a.type))}`}
                        </div>
                        <p className="mt-1 line-clamp-2 text-xs">
                          {verificationWording(m.verification.status)}
                          {m.verification.reason ? `: ${m.verification.reason}` : ""}
                        </p>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
              {playing ? (
                <div className="min-w-0 rounded-xl border p-3">
                  {playing.summary && <p className="mb-2 text-sm">{playing.summary}</p>}
                  <EvidencePlayer key={`${selected}-${playing.start_time}`} result={playing} />
                </div>
              ) : (
                <p className="self-center text-sm text-muted-foreground">Pick a match to play its evidence.</p>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
