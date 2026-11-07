import type { ActivityEvent, NativeQuestion, RunDetail, RunSummary } from "../api/types";

/** Who produced a step. Derived only from the recorded event type and actor, never guessed. */
export type Lane = "agent" | "backend" | "human" | "scripted" | "fixture";
export type StageId = "start" | "discover" | "retrieve" | "classify" | "clarify" | "validate" | "propose" | "decide" | "outcome";
export type Provenance = "cominty" | "fixture" | "diagnostic" | "aborted";
export type StepStatus = "ok" | "error" | "denied";

export const LANE_LABEL: Record<Lane, string> = {
  agent: "Cominty agent action",
  backend: "Undrift backend rule",
  human: "Human decision",
  scripted: "Scripted evaluation input",
  fixture: "Local fixture (not Cominty)",
};

export const PROVENANCE_LABEL: Record<Provenance, string> = {
  cominty: "Real Cominty execution",
  fixture: "Local scripted fixture: not a Cominty execution",
  diagnostic: "Local connectivity diagnostic: no Cominty execution",
  aborted: "Stopped before reaching Cominty",
};

export const STAGES: { id: StageId; label: string; hint: string }[] = [
  { id: "start", label: "Run opened", hint: "Trusted runner issues a scoped run token and pins policy, documents and delegation" },
  { id: "discover", label: "Discovery", hint: "Agent explores the authorized catalog through MCP" },
  { id: "retrieve", label: "Retrieval", hint: "Agent reads the policy and documents; the server records the exact versions served" },
  { id: "classify", label: "Findings", hint: "Agent records evidence-quoted findings; the server validates each one" },
  { id: "clarify", label: "Clarification", hint: "Native Cominty questions and their answers" },
  { id: "validate", label: "Validation", hint: "Server checks the final reply against contracts and recorded findings" },
  { id: "propose", label: "Proposals", hint: "Finalizer turns validated corrections into exact patches" },
  { id: "decide", label: "Decision", hint: "Human review, or the bounded channel-delegation rule" },
  { id: "outcome", label: "Outcome", hint: "Final run state as recorded" },
];

export interface Step {
  key: string;
  stage: StageId;
  lane: Lane;
  status: StepStatus;
  title: string;
  /** Short recorded facts, one per underlying event (document IDs, queries, categories...). */
  items: string[];
  note?: string;
  first: ActivityEvent;
  last: ActivityEvent;
  count: number;
}

export interface StageSummary { id: StageId; label: string; hint: string; status: StepStatus | "none"; count: number; lanes: Lane[] }

export interface Execution {
  provenance: Provenance;
  steps: Step[];
  stages: StageSummary[];
  toolCalls: Record<string, number>;
  documentsRetrieved: string[];
  findings: Record<string, string>;
  questions: number;
  proposals: string[];
  autoApplied: string[];
  denials: string[];
}

const s = (v: unknown): string => (v === null || v === undefined ? "" : String(v));

export function provenanceOf(run: RunSummary, events: ActivityEvent[] = []): Provenance {
  const start = events.find((e) => e.type === "agent.run_started");
  if (run.agent_id === "local-fixture" || start?.metadata.fixture === true) return "fixture";
  if (!run.agent_id || start?.metadata.cominty_execution === false || start?.metadata.not_agent_execution === true) return "diagnostic";
  if (!run.thread_id && !run.message_id) return "aborted";
  return "cominty";
}

/** Events explicitly correlated with this run: by run ID, by the run's Cominty message ID (older archives),
 *  by the MCP calls the server attributed to the run, or by one of the run's proposals. Never by time alone. */
export function runEvents(run: RunSummary, events: ActivityEvent[]): ActivityEvent[] {
  const proposals = new Set(run.proposals);
  const calls = new Set(run.mcp_calls.map((c) => c.seq));
  const linked = (e: ActivityEvent): boolean => {
    const prov = (e.metadata.provenance ?? {}) as Record<string, unknown>;
    if (e.metadata.run_id === run.id || prov.run_id === run.id || calls.has(e.seq)) return true;
    if (run.message_id && (prov.message_id === run.message_id || e.metadata.message_id === run.message_id)) return true;
    return e.subject_type === "proposal" && e.subject_id !== null && proposals.has(e.subject_id) && e.type !== "proposal.created";
  };
  return events.filter(linked).sort((a, b) => a.seq - b.seq);
}

interface Mapped { stage: StageId; lane: Lane; group: string; title: string; item: string; note?: string }

/** The server's recorded search-first contract observations, when present. */
export function strategyNote(m: Record<string, unknown>): string | undefined {
  const details = (m.details ?? {}) as Record<string, unknown>;
  const o = (details.observed ?? m.observed) as Record<string, unknown> | undefined;
  if (!o || typeof o !== "object") return undefined;
  const yn = (v: unknown) => (v ? "yes" : "no");
  return `Recorded contract checks: ${s(o.distinct_formulated_queries_before_catalog)} distinct searches before the catalog · `
    + `number-free search ${yn(o.number_free_query)} · search lead retrieved before the catalog ${yn(o.lead_followed)} · `
    + `catalog complete ${yn(o.catalog_complete)} · ${s(o.search_calls)} search calls`;
}

function humanOrScripted(actor: string): Lane {
  return actor.startsWith("evaluation") ? "scripted" : "human";
}

function mapEvent(e: ActivityEvent, hasSemanticCalls: boolean): Mapped | null {
  const m = e.metadata;
  const failed = e.status !== "ok";
  switch (e.type) {
    case "agent.run_started":
      return { stage: "start", lane: "backend", group: "start", title: e.actor === "runner" ? "Runner opened a continuation phase" : "Runner opened the run",
        item: [m.policy_version !== undefined ? `policy P${s(m.policy_version)}` : "", Array.isArray(m.document_ids) ? `${m.document_ids.length} documents in scope` : "",
          m.max_steps !== undefined ? `max ${s(m.max_steps)} steps` : ""].filter(Boolean).join(" · ") };
    case "agent.conversation_message":
      return { stage: "start", lane: "backend", group: "message", title: "Task sent to the Cominty agent",
        item: [m.phase !== undefined ? `phase ${s(m.phase)}` : "", m.thread_id ? `thread ${s(m.thread_id).slice(0, 8)}…` : ""].filter(Boolean).join(" · ") };
    case "agent.discovery": {
      const op = s(m.operation);
      if (op === "list_channels") return { stage: "discover", lane: "agent", group: "list", title: "list_channels", item: `${s(m.document_count)} documents reported` };
      if (op === "similar_documents") return { stage: "discover", lane: "agent", group: "similar", title: "similar_documents", item: s(m.document_id) || "reference document" };
      const n = Array.isArray(m.result_documents) ? m.result_documents.length : 0;
      const results = `${n} result${n === 1 ? "" : "s"}`;
      if (m.catalog_browse) return { stage: "discover", lane: "agent", group: "catalog", title: "search_documents · catalog browse (empty query)",
        item: `offset ${s(m.offset ?? 0)} · ${results}` };
      return { stage: "discover", lane: "agent", group: "search", title: "search_documents · formulated queries",
        item: `“${s(m.query)}” · ${results}${m.number_free ? " · no numbers" : ""}` };
    }
    case "agent.retrieval":
      return { stage: "retrieve", lane: "agent", group: `retrieve-${s(m.kind)}-${failed}`,
        title: s(m.kind) === "policy" ? "get_policy" : "get_document",
        item: failed ? `${s(e.subject_id)} · ${e.error ?? "failed"}` : `${s(e.subject_id)} · v${s(m.version)} served`,
        note: failed ? undefined : "server recorded the exact version served" };
    case "mcp.tool_call":
      if (hasSemanticCalls) return null;
      return { stage: "retrieve", lane: "agent", group: `mcp-${s(m.tool)}`, title: s(m.tool),
        item: [s(m.document_id || m.policy_id), m.version !== undefined && m.version !== null ? `v${s(m.version)}` : ""].filter(Boolean).join(" · ") || "call recorded" };
    case "finding.recorded":
      return { stage: "classify", lane: "agent", group: "finding", title: "record_finding", item: `${s(m.document_id || e.subject_id)} → ${s(m.category)}`,
        note: "accepted by server validation (exact quote, retrieved version)" };
    case "agent.classification":
      return { stage: "classify", lane: "agent", group: "classification", title: "Classification in final reply", item: `${s(e.subject_id)} → ${s(m.category)}` };
    case "finding.rejected":
      return { stage: "classify", lane: "backend", group: "finding-rejected", title: "Finding rejected by the server", item: e.error ?? "rejected" };
    case "agent.questions_captured":
      return { stage: "clarify", lane: "agent", group: "questions", title: "Native Cominty questions asked",
        item: `${Array.isArray(m.question_ids) ? m.question_ids.length : "?"} questions; the run pauses for answers` };
    case "agent.question_answered":
      return { stage: "clarify", lane: humanOrScripted(e.actor), group: `answer-${humanOrScripted(e.actor)}`,
        title: humanOrScripted(e.actor) === "human" ? "Reviewer answered" : "Frozen evaluation answer supplied", item: s(m.document_id) };
    case "evaluation.answers_supplied":
      return { stage: "clarify", lane: "scripted", group: "answers-supplied", title: "Evaluation answer book applied", item: "frozen answers, not a live human" };
    case "agent.search_strategy_rejected":
    case "agent.reply_rejected":
    case "agent.reply_unparsed":
      return { stage: "validate", lane: "backend", group: e.type, title: e.type === "agent.reply_unparsed" ? "Final reply could not be parsed" : "Final reply rejected",
        item: e.error ?? (s(m.message) || "rejected"), note: strategyNote(m) };
    case "agent.verdict_received":
      return { stage: "validate", lane: "backend", group: "verdict", title: failed ? "Final reply not accepted" : "Final reply accepted",
        item: failed ? e.error ?? "" : `policy P${s(m.reported_policy_version)} matches the current version` };
    case "proposal.created":
      return { stage: "propose", lane: "backend", group: "proposal", title: "Proposal created from the agent's validated correction",
        item: `${s(m.document_id)} · ${s(m.proposal_id || e.subject_id)}` };
    case "autonomy.application_denied":
      return { stage: "decide", lane: "backend", group: "denied", title: "Delegation rule refused automatic application",
        item: `${s(e.subject_id)} · ${Array.isArray(m.blockers) ? m.blockers.join(", ") : ""}` };
    case "proposal.auto_applied":
      return { stage: "decide", lane: "backend", group: "auto", title: "Applied under channel delegation (no human patch approval)",
        item: `${s(m.document_id)} v${s(m.base_version)} → v${s(m.version)}` };
    case "document.version_created":
      return null; // duplicated by proposal.auto_applied / proposal.applied
    case "proposal.approved":
    case "proposal.rejected":
    case "proposal.applied":
      return { stage: "decide", lane: "human", group: e.type, title: `Reviewer ${e.type.split(".")[1]} the proposal`, item: `${s(m.document_id)} · ${s(e.subject_id)}` };
    case "agent.run_finished":
      return { stage: "outcome", lane: "backend", group: "finished", title: failed ? `Run closed: ${s(m.run_status) || "not completed"}` : "Run closed: completed",
        item: [m.duration_s !== undefined ? `${s(m.duration_s)} s` : "", e.error ?? ""].filter(Boolean).join(" · ") || "recorded" };
    default:
      return null;
  }
}

export function buildExecution(run: RunSummary, all: ActivityEvent[]): Execution {
  const events = runEvents(run, all);
  const provenance = provenanceOf(run, events);
  const hasSemantic = events.some((e) => ["agent.discovery", "agent.retrieval", "finding.recorded"].includes(e.type));
  const steps: Step[] = [];
  for (const e of events) {
    const mapped = mapEvent(e, hasSemantic);
    if (!mapped) continue;
    const lane: Lane = provenance === "fixture" && mapped.lane === "agent" ? "fixture" : mapped.lane;
    const status: StepStatus = e.status === "ok" ? "ok" : e.status === "denied" ? "denied" : "error";
    const prev = steps.at(-1);
    const key = `${mapped.stage}:${lane}:${mapped.group}:${status}`;
    if (prev && prev.key === key) {
      prev.items.push(mapped.item);
      prev.last = e;
      prev.count += 1;
    } else {
      steps.push({ key, stage: mapped.stage, lane, status, title: mapped.title, items: [mapped.item], note: mapped.note, first: e, last: e, count: 1 });
    }
  }
  const stages: StageSummary[] = STAGES.map((st) => {
    const own = steps.filter((x) => x.stage === st.id);
    const status = own.length === 0 ? "none" : own.some((x) => x.status === "error") ? "error" : own.some((x) => x.status === "denied") ? "denied" : "ok";
    return { ...st, status, count: own.reduce((n, x) => n + x.count, 0), lanes: [...new Set(own.map((x) => x.lane))] };
  });
  const toolCalls: Record<string, number> = {};
  for (const e of events) if (e.type === "mcp.tool_call" && e.status === "ok") toolCalls[s(e.metadata.tool)] = (toolCalls[s(e.metadata.tool)] ?? 0) + 1;
  const retrieved = [...new Set(events.filter((e) => e.type === "agent.retrieval" && e.status === "ok" && e.metadata.kind === "document").map((e) => s(e.subject_id)))];
  return {
    provenance, steps, stages, toolCalls, documentsRetrieved: retrieved,
    findings: run.classifications, proposals: run.proposals,
    questions: events.filter((e) => e.type === "agent.questions_captured").reduce((n, e) => n + (Array.isArray(e.metadata.question_ids) ? e.metadata.question_ids.length : 0), 0),
    autoApplied: events.filter((e) => e.type === "proposal.auto_applied").map((e) => s(e.subject_id)),
    denials: events.filter((e) => e.type === "autonomy.application_denied").map((e) => s(e.subject_id)),
  };
}

/** One-line, evidence-grounded headline for the outcome. */
export function outcomeHeadline(run: RunSummary, ex: Execution): string {
  if (ex.provenance === "diagnostic") return "Connectivity diagnostic only: no agent, no findings accepted, no proposals.";
  if (ex.provenance === "aborted") return `Stopped before any Cominty request: ${run.errors[0] ?? "runner error"}.`;
  const who = ex.provenance === "fixture" ? "Scripted fixture" : "Cominty agent";
  const n = Object.keys(ex.findings).length;
  const parts = [`${who} recorded ${n} finding${n === 1 ? "" : "s"}`];
  if (ex.questions) parts.push(`asked ${ex.questions} native question${ex.questions === 1 ? "" : "s"}`);
  if (ex.proposals.length) parts.push(`${ex.proposals.length} proposal${ex.proposals.length === 1 ? "" : "s"} created`);
  if (ex.autoApplied.length) parts.push(`${ex.autoApplied.length} applied under delegation`);
  const tail = run.status === "completed" ? "Run completed." : run.errors[0] ? `Run ${run.status}: ${run.errors[0]}.` : `Run ${run.status}.`;
  return `${parts.join(", ")}. ${tail}`;
}

export function questionsFor(runId: string, phases: { run_id: string; questions: NativeQuestion[] }[]): NativeQuestion[] {
  return phases.filter((p) => p.run_id === runId).flatMap((p) => p.questions);
}

/** Cominty-reported trace without model reasoning text: tool calls keep their task label, model steps are counted only. */
export function traceSummary(run: RunDetail | null): { models: string[]; modelSteps: number; tools: { tool: string; label: string; status: string }[] } {
  const trace = run?.trace ?? [];
  return {
    models: [...new Set(trace.filter((t) => t.kind === "llm" && t.model).map((t) => t.model as string))],
    modelSteps: trace.filter((t) => t.kind === "llm").length,
    tools: trace.filter((t) => t.kind === "tool_call").map((t) => ({ tool: t.tool ?? "tool", label: t.description, status: t.status })),
  };
}
