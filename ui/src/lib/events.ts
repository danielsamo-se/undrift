import type { ActivityEvent, RunSummary } from "../api/types";
import { formatDate, humanizeActor } from "./format";

export type EventGroup = "mcp" | "agent" | "policy" | "review" | "security" | "system";
export type Tone = "neutral" | "brand" | "success" | "warning" | "danger" | "agent" | "mcp";

export interface EventView {
  group: EventGroup;
  tone: Tone;
  icon: IconName;
  title: string;
  detail: string;
  actor: string;
  link?: string;
}

export type IconName =
  | "plug"
  | "bot"
  | "scale"
  | "file-check"
  | "file-x"
  | "file-plus"
  | "shield"
  | "server"
  | "database"
  | "search"
  | "check"
  | "x"
  | "send"
  | "alert";

export const GROUP_LABEL: Record<EventGroup, string> = {
  mcp: "MCP calls",
  agent: "Agent",
  policy: "Policy",
  review: "Review",
  security: "Security",
  system: "System",
};

const CATEGORY_LABEL: Record<string, string> = {
  OUTDATED: "Outdated",
  VALID_DISTINCT: "Valid, distinct rule",
  EXISTING_COMMITMENT: "Existing commitment",
  UNRELATED: "Unrelated",
  UNKNOWN: "Unknown",
};

const str = (v: unknown): string => (v === null || v === undefined ? "" : String(v));
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);

export function groupOf(type: string): EventGroup {
  if (type.startsWith("mcp.")) return "mcp";
  if (type.startsWith("agent.") || type.startsWith("finding.")) return "agent";
  if (type.startsWith("policy.") || type.startsWith("impact.")) return "policy";
  if (type.startsWith("proposal.") || type.startsWith("document.")) return "review";
  if (type.startsWith("auth.")) return "security";
  return "system";
}

export function describeEvent(e: ActivityEvent): EventView {
  const m = e.metadata;
  const group = groupOf(e.type);
  const actor = humanizeActor(e.actor);
  const failed = e.status !== "ok";
  const base = { group, actor };
  switch (e.type) {
    case "mcp.tool_call": {
      const target = str(m.document_id || m.policy_id);
      const version = num(m.version);
      const served = version !== null ? `served ${m.tool === "get_policy" ? "P" : "v"}${version}` : "";
      return {
        ...base,
        tone: failed ? "danger" : "mcp",
        icon: "plug",
        title: `${str(m.tool)}${target ? ` · ${target}` : ""}`,
        detail: failed
          ? e.error ?? "Tool call failed"
          : [served, m.duration_ms !== undefined ? `${str(m.duration_ms)} ms` : "", str(m.user_agent)]
              .filter(Boolean)
              .join(" · "),
      };
    }
    case "agent.run_started":
      return { ...base, tone: "agent", icon: "bot", title: "Agent run started", detail: `max ${str(m.max_steps)} steps` };
    case "agent.run_finished":
      return {
        ...base,
        tone: failed ? "danger" : "agent",
        icon: "bot",
        title: failed ? `Agent run ${str(m.run_status) || "failed"}` : "Agent run completed",
        detail: [m.duration_s !== undefined ? `${str(m.duration_s)} s` : "", e.error ?? ""].filter(Boolean).join(" · "),
      };
    case "agent.verdict_received":
      return {
        ...base,
        tone: failed ? "danger" : "agent",
        icon: failed ? "alert" : "bot",
        title: failed ? "Verdict not accepted" : `Verdict received for policy P${str(m.reported_policy_version)}`,
        detail: failed ? e.error ?? "" : "Policy version matches the current version",
      };
    case "finding.recorded":
    case "agent.classification": {
      const agrees = m.agrees_with_scan;
      return {
        ...base,
        tone: failed ? "danger" : m.category === "OUTDATED" ? "warning" : "agent",
        icon: "search",
        title: `${str(e.subject_id)} classified ${CATEGORY_LABEL[str(m.category)] ?? str(m.category)}`,
        detail: failed ? e.error ?? "Finding rejected" :
          agrees === true
            ? "Agrees with the deterministic prefilter"
            : agrees === false
              ? "Differs from the deterministic prefilter"
              : "",
      };
    }
    case "finding.rejected":
      return { ...base, tone: "danger", icon: "alert", title: "Finding rejected", detail: e.error ?? "" };
    case "agent.retrieval":
      return { ...base, tone: failed ? "danger" : "agent", icon: "search", title: failed ? "Retrieval failed" : "Run retrieval verified", detail: e.error ?? str(e.subject_id) };
    case "agent.reply_unparsed":
    case "agent.reply_rejected":
      return { ...base, tone: "danger", icon: "alert", title: "Agent reply not usable", detail: e.error ?? "" };
    case "policy.version_created":
      return {
        ...base,
        tone: "brand",
        icon: "scale",
        title: `Policy updated to P${str(m.version)}`,
        detail: `${str(m.previous_voluntary_return_days)} → ${str(m.voluntary_return_days)} days, effective ${formatDate(str(m.effective_from))}`,
      };
    case "impact.candidates_detected": {
      const candidates = Array.isArray(m.candidates) ? (m.candidates as { actionable?: boolean }[]) : [];
      return {
        ...base,
        tone: "neutral",
        icon: "search",
        title: `Prefilter scanned ${str(m.documents_scanned)} documents`,
        detail: `${candidates.length} mention a superseded period · ${candidates.filter((c) => c.actionable).length} actionable`,
      };
    }
    case "proposal.created":
      return {
        ...base,
        tone: "brand",
        icon: "file-plus",
        title: `Correction proposed for ${str(m.document_id)}`,
        detail: `+${str(m.lines_added)} −${str(m.lines_removed)} lines · based on v${str(m.base_version)}, policy P${str(m.policy_version)} · pending review`,
        link: `#/review/${str(e.subject_id)}`,
      };
    case "proposal.submission_failed":
      return {
        ...base,
        tone: "danger",
        icon: "file-x",
        title: `Proposal refused for ${str(m.document_id)}`,
        detail: e.error ?? str(m.code),
      };
    case "proposal.approved":
      return { ...base, tone: "success", icon: "check", title: "Proposal approved", detail: str(m.document_id), link: `#/review/${str(e.subject_id)}` };
    case "proposal.rejected":
      return { ...base, tone: "neutral", icon: "x", title: "Proposal rejected", detail: str(m.document_id), link: `#/review/${str(e.subject_id)}` };
    case "proposal.applied":
      return { ...base, tone: "success", icon: "send", title: "Proposal applied", detail: str(m.document_id), link: `#/review/${str(e.subject_id)}` };
    case "document.version_created":
      return { ...base, tone: "success", icon: "file-check", title: `${str(m.document_id)} saved as v${str(m.version)}`, detail: "Local demo store" };
    case "auth.denied":
      return { ...base, tone: "warning", icon: "shield", title: "Reviewer action denied", detail: `${str(m.action)} · ${e.error ?? ""}` };
    case "server.started":
      return {
        ...base,
        tone: "neutral",
        icon: "server",
        title: `MCP server started (${str(m.version)})`,
        detail: `write tools ${m.write_tools ? "ENABLED" : "disabled"}${m.next_secret_staged ? " · rotation staged" : ""}`,
      };
    case "store.seeded":
      return { ...base, tone: "neutral", icon: "database", title: "Demo store seeded", detail: "" };
    default:
      if (e.type.startsWith("proposal.") && e.type.endsWith("_failed")) {
        return { ...base, tone: "danger", icon: "alert", title: `${e.type.slice(9).replace("_failed", "")} failed`, detail: e.error ?? "" };
      }
      return { ...base, tone: failed ? "danger" : "neutral", icon: "alert", title: e.type, detail: e.error ?? "" };
  }
}

export interface RunItem {
  kind: "run";
  id: string;
  start: ActivityEvent;
  finish: ActivityEvent | null;
  children: ActivityEvent[];
  status: "running" | "success" | "error";
  outcome?: string;
}

export interface EventItem {
  kind: "event";
  event: ActivityEvent;
}

export type TimelineItem = RunItem | EventItem;

/**
 * Group events (ascending seq) into agent runs: MCP calls the server recorded between
 * run_started and run_finished, plus results carrying the run's message id.
 */
export function buildTimeline(events: ActivityEvent[], summaries: RunSummary[] = []): TimelineItem[] {
  const items: TimelineItem[] = [];
  const byRun = new Map<string, RunItem>();
  const known = new Map(summaries.map((run) => [run.id, run]));
  const runId = (e: ActivityEvent): string | null => {
    const prov = e.metadata.provenance as { run_id?: string } | undefined;
    return typeof e.metadata.run_id === "string" ? e.metadata.run_id : prov?.run_id ?? null;
  };
  for (const e of events) {
    const rid = runId(e);
    const summary = rid ? known.get(rid) : undefined;
    if (rid && !byRun.has(rid) && (e.type === "agent.run_started" || summary)) {
      // A persisted run supplies its header when its start has fallen out of the
      // activity tail. These are view fields, never invented audit-log entries.
      const start: ActivityEvent = e.type === "agent.run_started" ? e : {
        ...e, seq: summary!.event_range[0], ts: summary!.started_at,
        type: "agent.run_started", subject_type: "run", subject_id: rid,
        metadata: { run_id: rid }, error: null, status: "ok",
      };
      const finish: ActivityEvent | null = summary?.finished_at ? {
        ...start, ts: summary.finished_at, type: "agent.run_finished",
        status: summary.status === "completed" ? "ok" : "error",
        metadata: { run_id: rid, run_status: summary.status, cost: summary.cost,
          retrieval: summary.retrieval, duration_s: summary.duration_s },
      } : null;
      const run: RunItem = { kind: "run", id: rid, start, finish, children: [],
        status: !summary || summary.status === "open" ? "running" : summary.status === "completed" ? "success" : "error",
        outcome: summary?.status,
      };
      byRun.set(rid, run);
      items.push(run);
    }
    if (e.type !== "agent.run_started" || !rid) {
      const run = rid ? byRun.get(rid) : undefined;
      if (run && e.type === "agent.run_finished") {
        run.finish = e;
        run.outcome = String(e.metadata.run_status ?? (e.status === "ok" ? "completed" : "failed"));
        run.status = e.status === "ok" ? "success" : "error";
      } else if (run) {
        run.children.push(e);
      } else {
        items.push({ kind: "event", event: e });
      }
    }
  }
  return items;
}

export function itemSeq(item: TimelineItem): number {
  return item.kind === "run" ? item.start.seq : item.event.seq;
}
