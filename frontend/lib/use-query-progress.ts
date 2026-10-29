"use client";

import { useEffect, useState } from "react";
import { api } from "./api";
import type { QuerySnapshot } from "./types";

/** How often to ask for the state when the WebSocket can't be used. */
export const POLL_MS = 2_000;

const finished = (snapshot: QuerySnapshot | null) =>
  snapshot !== null && (snapshot.state.status === "done" || snapshot.state.status === "failed");

/**
 * Follows a query: the progress WebSocket sends the state and results after every change (step
 * 4.4). If the socket can't be opened or drops before the query is finished, it polls instead.
 */
export function useQueryProgress(queryId: string | null): {
  snapshot: QuerySnapshot | null;
  error: string | null;
} {
  // Tagged with its query, so the previous query's data never shows for a new one.
  const [data, setData] = useState<{ id: string; snapshot: QuerySnapshot | null; error: string | null }>({
    id: "",
    snapshot: null,
    error: null,
  });

  useEffect(() => {
    if (!queryId) return;
    const setSnapshot = (snapshot: QuerySnapshot) =>
      setData({ id: queryId, snapshot, error: null });
    const setError = (error: string) =>
      setData((d) => ({ id: queryId, snapshot: d.id === queryId ? d.snapshot : null, error }));
    let stopped = false;
    let latest: QuerySnapshot | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const show = (next: QuerySnapshot) => {
      latest = next;
      setSnapshot(next);
    };

    async function poll() {
      if (stopped) return;
      try {
        const [state, results] = await Promise.all([api.query(queryId!), api.queryResults(queryId!)]);
        if (stopped) return;
        show({ state, results });
      } catch (e) {
        if (stopped) return;
        setError((e as Error).message);
      }
      if (!finished(latest)) timer = setTimeout(poll, POLL_MS);
    }

    let socket: WebSocket | null = null;
    try {
      socket = new WebSocket(api.queryProgressUrl(queryId));
    } catch {
      void poll();
    }
    if (socket) {
      socket.onmessage = (event) => {
        if (!stopped) show(JSON.parse(event.data) as QuerySnapshot);
      };
      socket.onclose = (event) => {
        if (stopped) return;
        if (event.code === 4404) setError("This query doesn't exist (any more).");
        else if (!finished(latest)) void poll();
      };
    }

    return () => {
      stopped = true;
      clearTimeout(timer);
      if (socket && socket.readyState <= WebSocket.OPEN) socket.close();
    };
  }, [queryId]);

  return data.id === queryId ? { snapshot: data.snapshot, error: data.error } : { snapshot: null, error: null };
}
