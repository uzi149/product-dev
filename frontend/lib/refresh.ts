import type { QueryClient } from "@tanstack/react-query";

/** How often lists that can change on the server reload while the tab is visible (ADR 0017). */
export const LIST_POLL_MS = 10_000;

/** A finished upload stored new footage: reload that camera's lists and the camera list. */
export function invalidateCameraData(queryClient: QueryClient, cameraId: string): void {
  queryClient.invalidateQueries({ queryKey: ["segments", cameraId] });
  queryClient.invalidateQueries({ queryKey: ["static-periods", cameraId] });
  queryClient.invalidateQueries({ queryKey: ["uploads", cameraId] });
  queryClient.invalidateQueries({ queryKey: ["cameras"] });
}

/** After the API was unreachable: reload, at once, every query on screen that failed meanwhile. */
export function refetchFailed(queryClient: QueryClient): Promise<void> {
  return queryClient.refetchQueries({ type: "active", predicate: (q) => q.state.status === "error" });
}
