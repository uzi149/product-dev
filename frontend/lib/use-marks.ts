"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "./api";
import { MARKS_POLL_MS, marksBusy } from "./marks";

/** An event's marks (step 5.7), checked again while they are being made. */
export function useEventMarks(eventId: string) {
  return useQuery({
    queryKey: ["events", "marks", eventId],
    queryFn: () => api.eventMarks(eventId),
    refetchInterval: (query) => (marksBusy(query.state.data) ? MARKS_POLL_MS : false),
  });
}

/** A query result's marks: opening the result asks for them (once; the API ignores repeats). */
export function useResultMarks(queryId: string | null, resultId: string) {
  const queryClient = useQueryClient();
  const key = ["queries", "marks", queryId, resultId];
  useEffect(() => {
    if (!queryId) return;
    api
      .markResult(queryId, resultId)
      .then((state) => queryClient.setQueryData(["queries", "marks", queryId, resultId], state))
      .catch(() => {
        // The GET below shows what the API has; a queue that is down shows as failed.
      });
  }, [queryClient, queryId, resultId]);
  return useQuery({
    queryKey: key,
    queryFn: () => api.resultMarks(queryId as string, resultId),
    enabled: queryId !== null,
    refetchInterval: (query) => (marksBusy(query.state.data) ? MARKS_POLL_MS : false),
  });
}
