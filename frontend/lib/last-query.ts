/**
 * The last query opened in this browser, so coming back to Investigate shows it again (step 4.6).
 * Storage can be missing or blocked (private windows); then nothing is remembered.
 */
const KEY = "sentinel.last-query";

export function readLastQuery(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function writeLastQuery(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(KEY, id);
    else window.localStorage.removeItem(KEY);
  } catch {
    // not remembered
  }
}
