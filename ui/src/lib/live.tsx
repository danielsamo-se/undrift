import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../api/client";
import type { ActivityEvent } from "../api/types";

export const POLL_MS = 2000;
const INITIAL_TAIL = 400;
const MAX_KEPT = 2000;

export type LiveStatus = "connecting" | "live" | "paused" | "offline";

interface LiveState {
  events: ActivityEvent[];
  latestSeq: number;
  status: LiveStatus;
  lastSync: number | null;
  freshSince: number;
  error: string | null;
  paused: boolean;
  setPaused: (paused: boolean) => void;
}

const LiveContext = createContext<LiveState | null>(null);

/** Polls the persisted activity log; every view refreshes when `latestSeq` moves. */
export function LiveEventsProvider({ children }: { children: ReactNode }) {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [latestSeq, setLatestSeq] = useState(0);
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [lastSync, setLastSync] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [freshSince, setFreshSince] = useState(Number.MAX_SAFE_INTEGER);
  const cursor = useRef<number | null>(null);

  const tick = useCallback(async () => {
    try {
      const page = cursor.current === null ? await api.eventsTail(INITIAL_TAIL) : await api.eventsAfter(cursor.current);
      if (cursor.current === null) setFreshSince(page.latest_seq);
      if (page.events.length) {
        setEvents((prev) => {
          const seen = new Set(prev.map((e) => e.seq));
          const merged = [...prev, ...page.events.filter((e) => !seen.has(e.seq))];
          return merged.length > MAX_KEPT ? merged.slice(merged.length - MAX_KEPT) : merged;
        });
      }
      cursor.current = Math.max(cursor.current ?? 0, page.next_after, page.events.at(-1)?.seq ?? 0);
      setLatestSeq(page.latest_seq);
      setStatus("live");
      setError(null);
      setLastSync(Date.now());
    } catch (err) {
      setStatus("offline");
      setError(err instanceof ApiError ? err.message : "Connection lost");
    }
  }, []);

  useEffect(() => {
    if (paused) {
      setStatus("paused");
      return;
    }
    let stopped = false;
    let timer: number | undefined;
    const loop = async () => {
      if (document.visibilityState === "visible") await tick();
      if (!stopped) timer = window.setTimeout(loop, POLL_MS);
    };
    void loop();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [paused, tick]);

  const value = useMemo(
    () => ({ events, latestSeq, status, lastSync, freshSince, error, paused, setPaused }),
    [events, latestSeq, status, lastSync, freshSince, error, paused],
  );
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveState {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive outside LiveEventsProvider");
  return ctx;
}
