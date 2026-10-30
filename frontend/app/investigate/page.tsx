"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, RotateCw } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { EvidencePlayer } from "@/components/investigate/evidence-player";
import { ProgressStages } from "@/components/investigate/progress-stages";
import { QueryForm, UnderstoodChips } from "@/components/investigate/query-form";
import { QueryHistory } from "@/components/investigate/query-history";
import { ResultCard } from "@/components/investigate/result-card";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { readLastQuery, writeLastQuery } from "@/lib/last-query";
import { splitResults } from "@/lib/queries";
import type { QueryInput, QueryState, Understood } from "@/lib/types";
import { QUERY_GONE, useQueryProgress } from "@/lib/use-query-progress";

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
  // The open query is kept in the URL (?q=…), so a reload or a shared link shows the same results.
  // Without it, the last query opened in this browser comes back (step 4.6).
  const requested = useSearchParams().get("q");
  const [queryId, setQueryId] = useState<string | null>(requested);
  const [seenRequested, setSeenRequested] = useState(requested);
  if (requested !== seenRequested) {
    setSeenRequested(requested);
    if (requested) setQueryId(requested);
  }
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const queryClient = useQueryClient();

  // The browser's storage is read after the first render, so the server's HTML still matches.
  const restored = useRef(false);
  useEffect(() => {
    if (!restored.current) {
      restored.current = true;
      const last = readLastQuery();
      if (!requested && last) {
        // Putting it in the URL opens it (above), like a link with ?q.
        window.history.replaceState(null, "", `?${new URLSearchParams({ q: last })}`);
        return;
      }
    }
    // Also when a link opened the page without ?q (the sidebar) while a query is open.
    if (requested !== queryId) {
      const url = queryId ? `?${new URLSearchParams({ q: queryId })}` : window.location.pathname;
      window.history.replaceState(null, "", url);
    }
    writeLastQuery(queryId);
  }, [queryId, requested]);

  function open(id: string | null) {
    setQueryId(id);
    setSelectedId(null);
  }

  const opened = {
    onSuccess: (state: QueryState) => {
      open(state.id);
      queryClient.invalidateQueries({ queryKey: ["queries"] });
    },
  };
  const create = useMutation({ mutationFn: (input: QueryInput) => api.createQuery(input), ...opened });
  const rerun = useMutation({ mutationFn: (id: string) => api.rerunQuery(id), ...opened });
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

  // The history shows each query's status: refresh it when the open one finishes.
  const status = state?.status;
  useEffect(() => {
    if (status === "done" || status === "failed") queryClient.invalidateQueries({ queryKey: ["queries"] });
  }, [status, queryClient]);

  function removeChip(kind: Understood["kind"]) {
    if (!state) return;
    create.mutate({
      ...state.request,
      ...(kind === "camera" ? { parse_cameras: false } : { parse_times: false }),
    });
  }
  const failure = create.error ?? rerun.error;

  return (
    <>
      <PageHeader
        eyebrow="Investigate"
        title="Ask any camera feed"
        description="Search the analysed footage in plain English. Results start as possible matches and are verified by the VLM; each plays as merged evidence, with gaps shown where footage is left out."
      />
      <div className="grid gap-4">
        <QueryForm busy={create.isPending} onSubmit={(input) => create.mutate(input)} />
        {failure && (
          <p role="alert" className="text-sm text-destructive">
            {failure.message}
          </p>
        )}
        <QueryHistory
          openId={queryId}
          onOpen={open}
          onRerun={(id) => rerun.mutate(id)}
          rerunning={rerun.isPending}
          onDeleted={(id) => id === queryId && open(null)}
        />

        {queryId && !state && !error && <p className="text-sm text-muted-foreground">Loading the query…</p>}
        {error && (
          <p
            role={error === QUERY_GONE ? undefined : "alert"}
            className={error === QUERY_GONE ? "text-sm text-muted-foreground" : "text-sm text-destructive"}
          >
            {error}{" "}
            <button type="button" className="underline" onClick={() => open(null)}>
              New search
            </button>
          </p>
        )}

        {state && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <UnderstoodChips
                understood={state.understood}
                searchText={state.search_text}
                onRemove={removeChip}
              />
              <div className="ml-auto flex gap-1.5">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={rerun.isPending}
                  onClick={() => rerun.mutate(state.id)}
                >
                  <RotateCw /> {rerun.isPending ? "Asking again…" : "Run again"}
                </Button>
                <Button type="button" size="sm" variant="outline" onClick={() => open(null)}>
                  <Plus /> New search
                </Button>
              </div>
            </div>
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
