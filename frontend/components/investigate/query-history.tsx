"use client";

import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, RotateCw, Search, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { historyStatus } from "@/lib/queries";
import type { QueryState } from "@/lib/types";
import { cn } from "@/lib/utils";

/** How many searches a page of the history shows; Show more adds as many again. */
export const HISTORY_PAGE = 20;
/** While a listed query is still waiting or running, the list refreshes this often. */
const REFRESH_MS = 3_000;
/** Typing in the search box waits this long before asking the API. */
const TYPING_MS = 300;

const unfinished = (q: QueryState) => q.status === "pending" || q.status === "running";

/**
 * Recent searches (step 4.6, ADR 0031): newest first, shared by everyone using the UI. Opening one
 * shows its stored results; Run again asks it again; Delete removes it with its results. The
 * search box keeps the questions with every typed word (filtered by the API, over the whole
 * history). Collapsed while a query is open, open when none is.
 */
export function QueryHistory({
  openId,
  onOpen,
  onRerun,
  rerunning,
  onDeleted,
}: {
  openId: string | null;
  onOpen: (id: string) => void;
  onRerun: (id: string) => void;
  rerunning: boolean;
  onDeleted: (id: string) => void;
}) {
  const [typed, setTyped] = useState("");
  const [search, setSearch] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setSearch(typed.trim()), TYPING_MS);
    return () => clearTimeout(timer);
  }, [typed]);
  const [expanded, setExpanded] = useState(!openId);
  const [shownFor, setShownFor] = useState(openId);
  if (shownFor !== openId) {
    setShownFor(openId);
    setExpanded(!openId);
  }
  const [deleting, setDeleting] = useState<QueryState | null>(null);
  // Show more loads the next page (offset), so no request asks for more than one page.
  const history = useInfiniteQuery({
    queryKey: ["queries", search],
    queryFn: ({ pageParam }) => api.queries({ limit: HISTORY_PAGE, offset: pageParam, q: search }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, page) => n + page.items.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
    placeholderData: (previous) => previous,
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) => page.items.some(unfinished)) ? REFRESH_MS : false,
  });
  // A query asked while paging can appear on two pages for a moment: list it once.
  const items = [...new Map((history.data?.pages ?? []).flatMap((p) => p.items).map((q) => [q.id, q])).values()];
  const total = history.data?.pages[0]?.total ?? 0;
  const count = search ? `${total} matching` : String(total);

  return (
    <section aria-label="Recent searches" className="rounded-2xl border bg-card">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-xs font-semibold"
      >
        <ChevronDown className={cn("size-3.5 transition-transform", !expanded && "-rotate-90")} />
        Recent searches{history.data ? ` (${count})` : ""}
      </button>
      {expanded && (
        <div className="grid gap-1 border-t px-2 py-2">
          <div className="relative px-1 pb-1">
            <Search className="pointer-events-none absolute top-1/2 left-3.5 size-3.5 -translate-y-[calc(50%+2px)] text-muted-foreground" />
            <Input
              type="search"
              aria-label="Search the recent searches"
              placeholder="Search the recent searches"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              maxLength={200}
              className="h-8 pl-8 text-sm"
            />
          </div>
          {history.isPending && <p className="px-2 text-sm text-muted-foreground">Loading…</p>}
          {history.isError && (
            <p role="alert" className="px-2 text-sm text-destructive">
              Could not load the searches: {history.error.message}
            </p>
          )}
          {history.data && items.length === 0 && (
            <p className="px-2 py-1 text-sm text-muted-foreground">
              {search ? (
                <>
                  No searches with <q>{search}</q>.
                </>
              ) : (
                "No searches yet."
              )}
            </p>
          )}
          {items.length > 0 && (
            <ul className="grid gap-0.5">
              {items.map((q) => (
                <li
                  key={q.id}
                  className={cn(
                    "flex items-center gap-1 rounded-lg pr-1 hover:bg-accent",
                    q.id === openId && "bg-green/[0.06]",
                  )}
                >
                  <button
                    type="button"
                    aria-current={q.id === openId ? "true" : undefined}
                    onClick={() => onOpen(q.id)}
                    className="grid min-w-0 flex-1 gap-0.5 px-2 py-1.5 text-left sm:flex sm:items-baseline sm:gap-3"
                  >
                    <span className="truncate text-sm sm:flex-1">{q.query_text}</span>
                    <span className="shrink-0 text-[11px] text-muted-foreground">
                      {formatDateTime(q.created_at)} · {historyStatus(q)}
                    </span>
                  </button>
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    aria-label={`Run again: ${q.query_text}`}
                    title="Run again"
                    disabled={rerunning}
                    onClick={() => onRerun(q.id)}
                  >
                    <RotateCw />
                  </Button>
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    aria-label={`Delete: ${q.query_text}`}
                    title="Delete"
                    onClick={() => setDeleting(q)}
                  >
                    <Trash2 />
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {history.hasNextPage && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="justify-self-start"
              disabled={history.isFetchingNextPage}
              onClick={() => history.fetchNextPage()}
            >
              {history.isFetchingNextPage ? "Loading…" : "Show more"}
            </Button>
          )}
        </div>
      )}
      {deleting && (
        <DeleteQueryDialog
          query={deleting}
          onOpenChange={(open) => !open && setDeleting(null)}
          onDeleted={onDeleted}
        />
      )}
    </section>
  );
}

/** Delete one search with everything it found (ADR 0031). */
function DeleteQueryDialog({
  query,
  onOpenChange,
  onDeleted,
}: {
  query: QueryState;
  onOpenChange: (open: boolean) => void;
  onDeleted: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.deleteQuery(query.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["queries"] });
      toast.success("Search deleted");
      onDeleted(query.id);
      onOpenChange(false);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const results = query.counts.candidates;
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete this search?</DialogTitle>
          <DialogDescription>This can&apos;t be undone.</DialogDescription>
        </DialogHeader>
        <p className="text-sm">
          <q>{query.query_text}</q>
          {results > 0 ? ` and its ${results === 1 ? "result" : `${results} results`}` : ""} will be
          deleted. The footage and its analysis stay.
        </p>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>
            {remove.isPending ? "Deleting…" : "Delete search"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
