import { ChevronRight, CircleCheck, TriangleExclamationPoint } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { ActivityEvent } from "../api/types";
import { describeEvent, type RunItem } from "../lib/events";
import { formatCost, formatDateTime, formatTime, plural, shortId } from "../lib/format";
import { useResource } from "../lib/hooks";
import { AgentProse, CopyText, EVENT_ICONS, LoadingBlock } from "./ui";

export function LogRow({ event, fresh, nested = false }: { event: ActivityEvent; fresh: boolean; nested?: boolean }) {
  const [open, setOpen] = useState(false);
  const v = describeEvent(event);
  const Icon = EVENT_ICONS[v.icon];
  return (
    <li className={`log-row tone-${v.tone}${fresh ? " is-new" : ""}${nested ? " is-nested" : ""}${open ? " is-open" : ""}`}>
      <time className="log-time" dateTime={event.ts} title={formatDateTime(event.ts)}>
        {formatTime(event.ts)}
      </time>
      <span className="log-icon" aria-hidden>
        <Icon size={13} />
      </span>
      <div className="log-main">
        <button className="log-title" onClick={() => setOpen(!open)} aria-expanded={open}>
          {v.title}
        </button>
        {v.detail && <span className="log-detail">{v.detail}</span>}
        {open && (
          <div className="log-meta">
            <p className="muted small">
              <code>{event.type}</code> · seq #{event.seq} · actor <code>{event.actor}</code>
              {event.subject_type && (
                <>
                  {" "}
                  · subject <code>{event.subject_type}:{event.subject_id}</code>
                </>
              )}
            </p>
            <pre className="json">{JSON.stringify(event.metadata, null, 2)}</pre>
          </div>
        )}
      </div>
      <span className="log-end">
        {event.status !== "ok" && <span className={`flag flag-${event.status}`}>{event.status}</span>}
        {v.link && (
          <a className="log-link" href={v.link}>
            Review
          </a>
        )}
      </span>
    </li>
  );
}

function RunRecord({ id }: { id: string }) {
  const { data, error } = useResource((signal) => api.run(id, signal), id);
  if (error) return <p className="muted small">Run record unavailable: {error.message}</p>;
  if (!data) return <LoadingBlock rows={3} />;
  const run = data.run;
  return (
    <div className="run-record">
      <dl className="facts facts-inline">
        <dt>Thread</dt>
        <dd>{run.thread_id ? <CopyText value={run.thread_id} label="thread id" /> : "—"}</dd>
        <dt>Message</dt>
        <dd>{run.message_id ? <CopyText value={run.message_id} label="message id" /> : "—"}</dd>
        <dt>Record</dt>
        <dd>
          <code>{run.record_file ? `runs/${run.record_file}` : "not found"}</code>
        </dd>
      </dl>
      {run.trace.length > 0 && (
        <>
          <h4>Cominty trace (as reported by Cominty)</h4>
          <ol className="trace">
            {run.trace.map((s, i) => (
              <li key={i}>
                <code className="trace-kind">{s.kind === "tool_call" ? `tool ${s.tool}` : `llm ${s.model ?? ""}`}</code>
                <span>{s.description}</span>
              </li>
            ))}
          </ol>
        </>
      )}
      {run.explanation && (
        <>
          <h4>Agent explanation</h4>
          <AgentProse text={run.explanation} />
        </>
      )}
    </div>
  );
}

export function RunGroup({ run, freshSince, defaultOpen, anchorId }: {
  run: RunItem;
  freshSince: number;
  defaultOpen: boolean;
  anchorId?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [record, setRecord] = useState(false);
  const calls = run.children.filter((e) => e.type === "mcp.tool_call");
  const results = run.children.filter((e) => e.type !== "mcp.tool_call");
  const meta = run.finish?.metadata ?? {};
  const cost = (meta.cost as { total?: string } | undefined)?.total;
  const retrieval = meta.retrieval as { complete?: boolean; missing?: string[] } | undefined;
  const findings = results.filter((e) => e.type === "finding.recorded" && e.status === "ok").length;
  const proposals = results.filter((e) => e.type === "proposal.created").length;
  const verdictFailed = results.some((e) => e.type === "agent.verdict_received" && e.status !== "ok");
  const state = run.status === "running" ? "running" : run.status === "error" || verdictFailed ? "error" : "ok";
  const fresh = run.start.seq > freshSince || (run.finish?.seq ?? 0) > freshSince;

  return (
    <li id={anchorId} className={`run run-${state}${fresh ? " is-new" : ""}`}>
      <button className="run-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <ChevronRight size={14} aria-hidden className="run-chev" />
        <time className="log-time" dateTime={run.start.ts} title={formatDateTime(run.start.ts)}>
          {formatTime(run.start.ts)}
        </time>
        <span className="run-state" aria-hidden />
        <span className="run-name">
          {state === "running" ? "Agent run in progress" : state === "error" ? `Agent run ${run.outcome ?? "failed"}` : "Agent run completed"}
          <code className="muted">{shortId(run.id)}</code>
        </span>
        <span className="run-facts">
          <span>{plural(calls.length, "MCP call")}</span>
          {findings > 0 && <span>{plural(findings, "finding")}</span>}
          {proposals > 0 && <span className="is-accent">{plural(proposals, "proposal")}</span>}
          {retrieval && (
            <span className={retrieval.complete ? "is-ok" : "is-warn"}>
              {retrieval.complete ? <CircleCheck size={12} aria-hidden /> : <TriangleExclamationPoint size={12} aria-hidden />}
              {retrieval.complete ? "retrieval verified" : `missing ${retrieval.missing?.join(", ")}`}
            </span>
          )}
          {meta.duration_s !== undefined && <span>{String(meta.duration_s)} s</span>}
          {cost && <span>cost {formatCost(cost)}</span>}
        </span>
      </button>
      {open && (
        <div className="run-body">
          <p className="run-label">MCP server · {calls.length ? "tool calls recorded during this run" : "no tool calls recorded during this run"}</p>
          {calls.length > 0 && (
            <ul className="log">
              {calls.map((e) => (
                <LogRow key={e.seq} event={e} fresh={e.seq > freshSince} nested />
              ))}
            </ul>
          )}
          {results.length > 0 && (
            <>
              <p className="run-label">Results</p>
              <ul className="log">
                {results.map((e) => (
                  <LogRow key={e.seq} event={e} fresh={e.seq > freshSince} nested />
                ))}
              </ul>
            </>
          )}
          {run.finish && (
            <button className="text-btn" onClick={() => setRecord(!record)} aria-expanded={record}>
              {record ? "Hide" : "Show"} agent explanation and Cominty trace
            </button>
          )}
          {record && <RunRecord id={run.id} />}
        </div>
      )}
    </li>
  );
}
