import { Pause, Play } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { LogRow, RunGroup } from "../components/ActivityLog";
import { EmptyState, ErrorState, LoadingBlock } from "../components/ui";
import { useRuns } from "../lib/data";
import { GROUP_LABEL, buildTimeline, groupOf, itemSeq, type EventGroup, type TimelineItem } from "../lib/events";
import { dayKey, formatCost, formatTime, plural, relativeTime, shortId } from "../lib/format";
import { useNow } from "../lib/hooks";
import { POLL_MS, useLive } from "../lib/live";

const GROUPS: EventGroup[] = ["agent", "mcp", "policy", "review", "security", "system"];

function matches(item: TimelineItem, filter: EventGroup | null): boolean {
  if (!filter) return true;
  if (item.kind === "run") return filter === "agent" || (filter === "mcp" && item.children.some((e) => e.type === "mcp.tool_call"));
  return groupOf(item.event.type) === filter;
}

export function ActivityView({ focusRun }: { focusRun: string | null }) {
  const live = useLive();
  const now = useNow(5000);
  const runs = useRuns();
  const [filter, setFilter] = useState<EventGroup | null>(null);
  const items = useMemo(() => buildTimeline(live.events), [live.events]);
  const shown = useMemo(() => items.filter((i) => matches(i, filter)).sort((a, b) => itemSeq(b) - itemSeq(a)), [items, filter]);
  const counts = useMemo(() => {
    const m = new Map<EventGroup, number>();
    live.events.forEach((e) => m.set(groupOf(e.type), (m.get(groupOf(e.type)) ?? 0) + 1));
    return m;
  }, [live.events]);
  const firstRun = shown.find((i) => i.kind === "run");

  useEffect(() => {
    if (focusRun) document.getElementById(`run-${focusRun}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focusRun, items.length]);

  const rows: ReactNode[] = [];
  let lastDay = "";
  for (const item of shown) {
    const ts = item.kind === "run" ? item.start.ts : item.event.ts;
    const day = dayKey(ts);
    if (day !== lastDay) rows.push(<li key={`d-${day}`} className="log-day">{day}</li>);
    lastDay = day;
    rows.push(
      item.kind === "run" ? (
        <RunGroup
          key={item.id}
          anchorId={`run-${item.id}`}
          run={item}
          freshSince={live.freshSince}
          defaultOpen={focusRun ? focusRun === item.id : item === firstRun}
        />
      ) : (
        <LogRow key={item.event.seq} event={item.event} fresh={item.event.seq > live.freshSince} />
      ),
    );
  }

  return (
    <div className="page">
      <header className="ph">
        <div>
          <h1>Activity</h1>
          <p className="ph-sub">Persisted log: MCP tool calls, agent runs and findings, policy changes, review decisions.</p>
        </div>
        <div className="ph-actions">
          <span className={`livestate livestate-${live.status}`} role="status">
            <span className="livestate-dot" aria-hidden />
            {live.status === "live" && `Live · ${POLL_MS / 1000}s`}
            {live.status === "paused" && "Paused"}
            {live.status === "offline" && "Offline · retrying"}
            {live.status === "connecting" && "Connecting"}
          </span>
          <button className="btn btn-sm" onClick={() => live.setPaused(!live.paused)}>
            {live.paused ? <Play size={12} aria-hidden /> : <Pause size={12} aria-hidden />} {live.paused ? "Resume" : "Pause"}
          </button>
        </div>
      </header>

      <div className="toolbar">
        <div className="seg seg-wrap" role="toolbar" aria-label="Filter events">
          <button className={!filter ? "is-active" : ""} aria-pressed={!filter} onClick={() => setFilter(null)}>
            All <span className="count">{live.events.length}</span>
          </button>
          {GROUPS.filter((g) => counts.get(g)).map((g) => (
            <button key={g} className={filter === g ? "is-active" : ""} aria-pressed={filter === g} onClick={() => setFilter(filter === g ? null : g)}>
              {GROUP_LABEL[g]} <span className="count">{counts.get(g)}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="activity">
        <div className="log-panel">
          {live.status === "connecting" && !live.events.length ? (
            <LoadingBlock rows={10} />
          ) : live.status === "offline" && !live.events.length ? (
            <ErrorState error={new Error(live.error ?? "API not reachable")} />
          ) : shown.length === 0 ? (
            <EmptyState title="No events match this filter" />
          ) : (
            <ul className="log">{rows}</ul>
          )}
          {live.events.length > 0 && (
            <p className="log-foot">
              {plural(live.events.length, "event")} loaded · seq #{live.events[0]?.seq}–#{live.events.at(-1)?.seq}
              {live.lastSync && ` · synced ${relativeTime(new Date(live.lastSync).toISOString(), now)}`}
            </p>
          )}
        </div>

        <aside className="runs-index" aria-label="Agent runs">
          <h3>Agent runs</h3>
          {runs.error && !runs.data ? (
            <ErrorState error={runs.error} onRetry={runs.reload} />
          ) : !runs.data ? (
            <LoadingBlock rows={3} />
          ) : runs.data.runs.length === 0 ? (
            <p className="muted">No agent runs yet.</p>
          ) : (
            <ul className="plain-list">
              {runs.data.runs.map((r) => {
                const state = r.status === "running" ? "running" : r.errors.length ? "error" : "ok";
                return (
                  <li key={r.id}>
                    <a href={`#/activity/${encodeURIComponent(r.id)}`} className={`run-index-item${focusRun === r.id ? " is-active" : ""}`}>
                      <span className={`run-dot run-dot-${state}`} aria-hidden />
                      <span className="run-index-main">
                        <span>
                          <code>{shortId(r.id)}</code> · {formatTime(r.started_at)}
                        </span>
                        <span className="muted small">
                          {state === "running"
                            ? "in progress"
                            : state === "error"
                              ? "no usable verdict"
                              : `${Object.keys(r.classifications).length} findings · ${plural(r.proposals.length, "proposal")}`}
                          {r.cost?.total && ` · ${formatCost(r.cost.total)}`}
                        </span>
                      </span>
                    </a>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>
      </div>
    </div>
  );
}
