import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

/**
 * False while React hydrates the server HTML, true after. A component behind a Suspense boundary
 * hydrates after the sidebar, which may have filled the query cache meanwhile: rendering cached
 * data before hydration would not match the server HTML.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}
