import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

export type View = "overview" | "impact" | "review" | "activity";
export interface Route {
  view: View;
  param: string | null;
}

const VIEWS: View[] = ["overview", "impact", "review", "activity"];

export function parseHash(hash: string): Route {
  const [view, ...rest] = hash.replace(/^#\/?/, "").split("/");
  return {
    view: VIEWS.includes(view as View) ? (view as View) : "overview",
    param: rest.length ? decodeURIComponent(rest.join("/")) || null : null,
  };
}

function subscribeHash(cb: () => void): () => void {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribeHash, () => window.location.hash, () => "");
  return parseHash(hash);
}

export function navigate(view: View, param?: string | null): void {
  window.location.hash = `/${view}${param ? `/${encodeURIComponent(param)}` : ""}`;
}

export interface Resource<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Load data and reload whenever `key` changes (e.g. the latest event seq). Previous data stays
 * visible during reloads, so live updates do not flash loading states.
 */
export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, key: unknown): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    loadRef
      .current(ctrl.signal)
      .then((value) => {
        if (ctrl.signal.aborted) return;
        setData(value);
        setError(null);
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}

export function useNow(intervalMs = 15_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
