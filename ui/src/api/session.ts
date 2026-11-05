import { useSyncExternalStore } from "react";

/**
 * Reviewer credentials live in this module only: never in bundled source, localStorage,
 * sessionStorage, cookies or the URL. Reloading the page locks the session again.
 */
interface Session {
  token: string;
  reviewer: string;
}

let current: Session | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export const reviewerSession = {
  get: (): Session | null => current,
  token: (): string | null => current?.token ?? null,
  set(token: string, reviewer: string): void {
    current = { token, reviewer };
    emit();
  },
  clear(): void {
    current = null;
    emit();
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export function useReviewerSession(): Session | null {
  return useSyncExternalStore(reviewerSession.subscribe, reviewerSession.get, reviewerSession.get);
}
