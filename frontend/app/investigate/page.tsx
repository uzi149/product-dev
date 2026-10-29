"use client";

import { useMutation } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { EvidencePlayer } from "@/components/investigate/evidence-player";
import { ProgressStages } from "@/components/investigate/progress-stages";
import { QueryForm, UnderstoodChips } from "@/components/investigate/query-form";
import { ResultCard } from "@/components/investigate/result-card";
import { PageHeader } from "@/components/page-header";
import { api } from "@/lib/api";
import { splitResults } from "@/lib/queries";
import type { QueryInput, Understood } from "@/lib/types";
import { useQueryProgress } from "@/lib/use-query-progress";

/** A query still pending after this long means no query worker is taking it. */
const NO_WORKER_MS = 10_000;

export default function InvestigatePage() {
  // useSearchParams needs a Suspense boundary when the page is pre-rendered.
  return (
    <Suspense>
      <InvestigateView />
    </Suspense>
  );
}

function InvestigateView() {
  // The query id is kept in the URL (?q=…), so a reload or a shared link shows the same results.
  const requested = useSearchParams().get("q");
  const [created, setCreated] = useState<string | null>(null);
  const queryId = created ?? requested;
  const [lastInput, setLastInput] = useState<QueryInput | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: (input: QueryInput) => api.createQuery(input),
    onSuccess: (state, input) => {
      setLastInput(input);
      setCreated(state.id);
      setSelectedId(null);
      window.history.replaceState(null, "", `?${new URLSearchParams({ q: state.id })}`);
    },
  });
  const { snapshot, error } = useQueryProgress(queryId);
  const state = snapshot?.state;
  const results = snapshot?.results ?? [];
  const { main, notVerified } = state ? splitResults(results, state) : { main: [], notVerified: [] };
  const selected = results.find((r) => r.id === selectedId) ?? main[0] ?? notVerified[0] ?? null;

  // Ticks while the query waits, to say when no worker seems to be taking it.
  const [now, setNow] = useState(() => Date.now());
  const pending = state?.status === "pending";
  useEffect(() => {
    if (!pending) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [pending]);
  const noWorker = pending && now - new Date(state.created_at).getTime() > NO_WORKER_MS;

  function removeChip(kind: Understood["kind"]) {
    if (!lastInput) return;
    create.mutate({
      ...lastInput,
      ...(kind === "camera" ? { parse_cameras: false } : { parse_times: false }),
    });
  }

  return (
    <>
      <PageHeader
        eyebrow="Investigate"
        title="Ask any camera feed"
        description="Search the analysed footage in plain English. Results start as possible matches and are verified by the VLM; each plays as merged evidence, with gaps shown where footage is left out."
      />
      <div className="grid gap-4">
        <QueryForm busy={create.isPending} onSubmit={(input) => create.mutate(input)} />
        {create.isError && (
          <p role="alert" className="text-sm text-destructive">
            {create.error.message}
          </p>
        )}

        {queryId && !state && !error && <p className="text-sm text-muted-foreground">Loading the query…</p>}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}

        {state && (
          <>
            <UnderstoodChips
              understood={state.understood}
              searchText={state.search_text}
              onRemove={lastInput ? removeChip : undefined}
            />
            <ProgressStages state={state} />
            {noWorker && (
              <p role="alert" className="text-sm text-orange">
                The query is waiting: the query worker isn&apos;t running. Start it with{" "}
                <code className="font-mono">celery -A shared.jobs.celery_app worker --pool=solo -Q query</code>.
              </p>
            )}
            {state.status === "failed" && (
              <p role="alert" className="text-sm text-destructive">
                The query failed: {state.error}
              </p>
            )}
            {state.status === "done" && results.length === 0 && (
              <p className="rounded-2xl border border-dashed p-6 text-center text-sm text-muted-foreground">
                No matches. Try other words, or a wider time range.
              </p>
            )}

            {results.length > 0 && (
              <div className="grid gap-4 xl:grid-cols-[minmax(300px,0.9fr)_minmax(0,1.5fr)]">
                <div className="grid content-start gap-3">
                  <ul className="grid gap-2" aria-label="Results">
                    {main.map((r) => (
                      <ResultCard
                        key={r.id}
                        result={r}
                        selected={selected?.id === r.id}
                        onSelect={() => setSelectedId(r.id)}
                      />
                    ))}
                  </ul>
                  {notVerified.length > 0 && (
                    <section aria-label="Not verified" className="grid gap-2">
                      <h2 className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                        Not verified
                      </h2>
                      <ul className="grid gap-2">
                        {notVerified.map((r) => (
                          <ResultCard
                            key={r.id}
                            result={r}
                            selected={selected?.id === r.id}
                            onSelect={() => setSelectedId(r.id)}
                          />
                        ))}
                      </ul>
                    </section>
                  )}
                </div>
                {selected && (
                  <section aria-label="Evidence" className="min-w-0 rounded-2xl border bg-card p-4">
                    <h2 className="mb-3 text-xs font-semibold">
                      Evidence · {selected.camera_name}
                    </h2>
                    <EvidencePlayer key={selected.id} result={selected} />
                  </section>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
