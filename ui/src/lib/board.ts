import type { ActivityEvent, Channel, ImpactDocument, ImpactSnapshot, Proposal } from "../api/types";
import { STATUS, isStale, statusOf, type MapStatus } from "./classification";

export const SEVERITY: Record<MapStatus, number> = {
  outdated: 0,
  unknown: 1,
  unanalyzed: 2,
  commitment: 3,
  valid: 4,
  unrelated: 5,
};

export interface Lane {
  channel: Channel;
  docs: ImpactDocument[];
  total: number;
  worst: MapStatus | null;
}

export interface BoardFilter {
  statuses: Set<MapStatus> | null;
  query: string;
}

function matchesQuery(doc: ImpactDocument, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [doc.title, doc.id, doc.channel, doc.content].some((v) => v.toLowerCase().includes(q));
}

/** Documents grouped by channel; lanes and documents ordered by severity, then name. */
export function buildLanes(snap: ImpactSnapshot, filter: BoardFilter = { statuses: null, query: "" }): Lane[] {
  const lanes = snap.channels.map((channel) => {
    const all = snap.documents.filter((d) => d.channel === channel.id);
    const docs = all
      .filter((d) => (!filter.statuses || filter.statuses.has(statusOf(d))) && matchesQuery(d, filter.query))
      .sort((a, b) => SEVERITY[statusOf(a)] - SEVERITY[statusOf(b)] || a.title.localeCompare(b.title));
    const worst = all.length ? all.map(statusOf).sort((a, b) => SEVERITY[a] - SEVERITY[b])[0]! : null;
    return { channel, docs, total: all.length, worst };
  });
  return lanes
    .filter((l) => l.docs.length > 0)
    .sort((a, b) => SEVERITY[a.worst ?? "unrelated"] - SEVERITY[b.worst ?? "unrelated"] || a.channel.name.localeCompare(b.channel.name));
}

export interface Consistency {
  total: number;
  drifted: number;
  unknown: number;
  stale: number;
  unanalyzed: number;
  consistent: number;
  pendingReview: number;
  awaitingApply: number;
  counts: Map<MapStatus, number>;
}

export function consistency(snap: ImpactSnapshot): Consistency {
  const counts = new Map<MapStatus, number>();
  let drifted = 0;
  let unknown = 0;
  let stale = 0;
  let unanalyzed = 0;
  for (const d of snap.documents) {
    const s = statusOf(d);
    counts.set(s, (counts.get(s) ?? 0) + 1);
    if (s === "unanalyzed") unanalyzed++;
    else if (isStale(d)) stale++;
    else if (s === "outdated") drifted++;
    else if (s === "unknown") unknown++;
  }
  const proposals = snap.documents.flatMap((d) => d.proposals);
  return {
    total: snap.documents.length,
    drifted,
    unknown,
    stale,
    unanalyzed,
    consistent: snap.documents.length - drifted - unknown - stale - unanalyzed,
    pendingReview: proposals.filter((p) => p.status === "pending_review").length,
    awaitingApply: proposals.filter((p) => p.status === "approved").length,
    counts,
  };
}

export type NextAction =
  | { kind: "review"; proposalId: string; title: string }
  | { kind: "apply"; proposalId: string; title: string }
  | { kind: "analyse"; reason: string }
  | { kind: "none" };

/** The single most useful next step, derived only from persisted state. */
export function nextAction(snap: ImpactSnapshot, proposals: Proposal[]): NextAction {
  const title = (docId: string) => snap.documents.find((d) => d.id === docId)?.title ?? docId;
  const oldest = (status: string) =>
    proposals.filter((p) => p.status === status).sort((a, b) => a.created_at.localeCompare(b.created_at))[0];
  const pending = oldest("pending_review");
  if (pending) return { kind: "review", proposalId: pending.id, title: title(pending.document_id) };
  const approved = oldest("approved");
  if (approved) return { kind: "apply", proposalId: approved.id, title: title(approved.document_id) };
  const c = consistency(snap);
  const uncovered = snap.documents.filter(
    (d) => statusOf(d) === "outdated" && !isStale(d) && !d.proposals.some((p) => p.status === "pending_review" || p.status === "approved"),
  );
  if (uncovered.length) return { kind: "analyse", reason: `${uncovered.length} outdated without a correction` };
  if (c.stale || c.unanalyzed) return { kind: "analyse", reason: `${c.stale + c.unanalyzed} not analysed at the current versions` };
  return { kind: "none" };
}

export type LiveKind = "reading" | "classified" | "proposed";

export const LIVE_WINDOW_MS = 15_000;

/**
 * Documents touched by real events that arrived after the page loaded and within the last
 * few seconds: an MCP read, an agent classification or a proposal. Drives the only map animation.
 */
export function liveDocuments(events: ActivityEvent[], freshSince: number, now: number): Map<string, LiveKind> {
  const rank: Record<LiveKind, number> = { reading: 0, classified: 1, proposed: 2 };
  const out = new Map<string, LiveKind>();
  for (const e of events) {
    if (e.seq <= freshSince || now - new Date(e.ts).getTime() > LIVE_WINDOW_MS) continue;
    let doc: string | null = null;
    let kind: LiveKind | null = null;
    if (e.type === "mcp.tool_call" && e.metadata.tool === "get_document" && e.status === "ok") {
      doc = String(e.metadata.document_id ?? "");
      kind = "reading";
    } else if (e.type === "agent.classification") {
      doc = e.subject_id;
      kind = "classified";
    } else if (e.type === "proposal.created") {
      doc = String(e.metadata.document_id ?? "");
      kind = "proposed";
    }
    if (doc && kind && (!out.has(doc) || rank[kind] >= rank[out.get(doc)!])) out.set(doc, kind);
  }
  return out;
}

/** True while an agent run has started and not yet finished, according to the event log. */
export function runInProgress(events: ActivityEvent[]): boolean {
  for (let i = events.length - 1; i >= 0; i--) {
    const t = events[i]!.type;
    if (t === "agent.run_finished") return false;
    if (t === "agent.run_started") return true;
  }
  return false;
}

export function statusLabel(s: MapStatus): string {
  return STATUS[s].short;
}
