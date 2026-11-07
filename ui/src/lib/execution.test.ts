import { describe, expect, it } from "vitest";
import type { ActivityEvent, RunSummary } from "../api/types";
import { buildExecution, outcomeHeadline, provenanceOf, traceSummary } from "./execution";

let seq = 0;
function ev(type: string, metadata: Record<string, unknown> = {}, extra: Partial<ActivityEvent> = {}): ActivityEvent {
  seq += 1;
  return { seq, ts: `2026-11-07T17:34:${String(seq % 60).padStart(2, "0")}+00:00`, type, actor: "mcp:python-httpx", status: "ok",
    subject_type: null, subject_id: null, metadata, error: null, ...extra };
}
function run(extra: Partial<RunSummary> = {}): RunSummary {
  return { id: "run-a", status: "failed", started_at: "2026-11-07T17:33:59Z", finished_at: "2026-11-07T17:35:02Z", duration_s: 62,
    agent_id: "-cS4pr7oTPO4", thread_id: "thread-1", message_id: "msg-1", cost: { total: "0.1479441" } as RunSummary["cost"], retrieval: null,
    mcp_calls: [], classifications: { "help-101": "OUTDATED" }, proposals: [], errors: ["discovery search-first contract was not satisfied"],
    event_range: [1, 20], record_file: null, document_ids: ["help-101", "help-102"], ...extra };
}
const R = { run_id: "run-a" };

describe("buildExecution", () => {
  it("rebuilds a real failed run from recorded events only, with lanes and grouped steps", () => {
    const events = [
      ev("agent.run_started", { ...R, policy_version: 2, document_ids: ["help-101", "help-102"], max_steps: 40 }, { actor: "agent:cominty:x" }),
      ev("agent.conversation_message", { ...R, phase: 1, thread_id: "2de88034-78e4" }, { actor: "runner" }),
      ev("agent.discovery", { ...R, operation: "search_documents", query: "send back items", has_words: true, number_free: true, result_documents: ["help-101"] }),
      ev("mcp.tool_call", { ...R, tool: "search_documents" }),
      ev("agent.discovery", { ...R, operation: "search_documents", query: "return days 60 30", has_words: true, result_documents: [] }),
      ev("mcp.tool_call", { ...R, tool: "search_documents" }),
      ev("agent.discovery", { ...R, operation: "search_documents", catalog_browse: true, offset: 0, result_documents: ["help-101", "help-102"] }),
      ev("agent.retrieval", { ...R, kind: "document", version: 1 }, { subject_type: "document", subject_id: "help-101" }),
      ev("mcp.tool_call", { ...R, tool: "get_document" }),
      ev("agent.retrieval", { ...R, kind: "document", version: 1 }, { subject_type: "document", subject_id: "help-102" }),
      ev("finding.recorded", { ...R, document_id: "help-101", category: "OUTDATED" }),
      ev("agent.reply_rejected", { ...R, code: "search_strategy_violation", details: { observed: { distinct_formulated_queries_before_catalog: 3,
        number_free_query: true, lead_followed: false, catalog_complete: true, search_calls: 4 } } }, { status: "error", error: "discovery search-first contract was not satisfied", actor: "agent:cominty:x" }),
      ev("agent.run_finished", { ...R, duration_s: 62 }, { status: "error", error: "discovery search-first contract was not satisfied", actor: "agent:cominty:x" }),
      ev("mcp.tool_call", { tool: "health_check" }), // not correlated with this run
    ];
    const ex = buildExecution(run(), events);
    expect(ex.provenance).toBe("cominty");
    expect(ex.toolCalls).toEqual({ search_documents: 2, get_document: 1 });
    expect(ex.documentsRetrieved).toEqual(["help-101", "help-102"]);
    const titles = ex.steps.map((s) => [s.stage, s.lane, s.title, s.count]);
    expect(titles).toEqual([
      ["start", "backend", "Runner opened the run", 1],
      ["start", "backend", "Task sent to the Cominty agent", 1],
      ["discover", "agent", "search_documents · formulated queries", 2],
      ["discover", "agent", "search_documents · catalog browse", 1],
      ["retrieve", "agent", "get_document", 2],
      ["classify", "agent", "record_finding", 1],
      ["validate", "backend", "Final reply rejected", 1],
      ["outcome", "backend", "Run closed: not completed", 1],
    ]);
    expect(ex.stages.find((s) => s.id === "validate")?.status).toBe("error");
    expect(ex.steps.find((s) => s.stage === "validate")?.note).toBe("Recorded contract checks: 3 distinct searches before the catalog · number-free search yes · search lead retrieved before the catalog no · catalog complete yes · 4 search calls");
    expect(ex.steps.find((s) => s.title.includes("formulated"))?.items).toEqual(["“send back items” · 1 result · no numbers", "“return days 60 30” · 0 results"]);
    // Stages without recorded evidence stay empty instead of being inferred.
    expect(ex.stages.filter((s) => s.status === "none").map((s) => s.id)).toEqual(["clarify", "propose", "decide"]);
    expect(outcomeHeadline(run(), ex)).toBe("Cominty agent recorded 1 finding. Run failed: discovery search-first contract was not satisfied.");
  });

  it("labels frozen evaluation answers as scripted, reviewer answers as human, and fixture actions as fixture", () => {
    const answers = [
      ev("agent.questions_captured", { ...R, question_ids: ["q1", "q2"] }, { actor: "agent:cominty:x" }),
      ev("agent.question_answered", { ...R, document_id: "crm-002" }, { actor: "evaluation" }),
      ev("agent.question_answered", { ...R, document_id: "bot-003" }, { actor: "local-reviewer" }),
    ];
    const ex = buildExecution(run(), answers);
    expect(ex.questions).toBe(2);
    expect(ex.steps.map((s) => s.lane)).toEqual(["agent", "scripted", "human"]);

    const fixture = buildExecution(run({ agent_id: "local-fixture", thread_id: null, message_id: null, proposals: ["prop-1"] }), [
      ev("finding.recorded", { ...R, document_id: "help-001", category: "OUTDATED" }),
      ev("proposal.created", { ...R, document_id: "help-001", proposal_id: "prop-1" }, { subject_type: "proposal", subject_id: "prop-1" }),
      ev("document.version_created", { ...R, document_id: "help-001" }, { actor: "autonomy:bounded-local" }),
      ev("proposal.auto_applied", { ...R, document_id: "help-001", base_version: 1, version: 2 }, { subject_type: "proposal", subject_id: "prop-1", actor: "autonomy:bounded-local" }),
    ]);
    expect(fixture.provenance).toBe("fixture");
    expect(fixture.steps.map((s) => [s.lane, s.stage])).toEqual([["fixture", "classify"], ["backend", "propose"], ["backend", "decide"]]);
    expect(fixture.autoApplied).toEqual(["prop-1"]);
  });

  it("includes human review decisions linked through the run's proposals", () => {
    const ex = buildExecution(run({ status: "completed", errors: [], proposals: ["prop-04"] }), [
      ev("proposal.approved", { document_id: "doc-001" }, { subject_type: "proposal", subject_id: "prop-04", actor: "reviewer:local-reviewer" }),
      ev("proposal.approved", { document_id: "doc-009" }, { subject_type: "proposal", subject_id: "prop-other", actor: "reviewer:local-reviewer" }),
    ]);
    expect(ex.steps).toHaveLength(1);
    expect(ex.steps[0]).toMatchObject({ lane: "human", stage: "decide", items: ["doc-001 · prop-04"] });
  });

  it("correlates older archives by Cominty message ID and server-attributed MCP calls, never by time", () => {
    const legacy = run({ status: "completed", errors: [], message_id: "msg-legacy", proposals: ["prop-04"],
      mcp_calls: [{ seq: 901, ts: "", status: "ok", tool: "get_document", document_id: "doc-001", policy_id: null, version: 3, secret_slot: null, user_agent: null, duration_ms: 3, error: null }] });
    const ex = buildExecution(legacy, [
      ev("mcp.tool_call", { tool: "get_document", document_id: "doc-001", version: 3 }, { seq: 901 }),
      ev("mcp.tool_call", { tool: "get_document", document_id: "doc-002", version: 1 }, { seq: 902 }),
      ev("agent.classification", { category: "OUTDATED", provenance: { message_id: "msg-legacy" } }, { seq: 903, subject_type: "document", subject_id: "doc-001" }),
      ev("agent.verdict_received", { reported_policy_version: 2, provenance: { message_id: "msg-legacy" } }, { seq: 904 }),
      ev("agent.classification", { category: "UNRELATED", provenance: { message_id: "other" } }, { seq: 905, subject_type: "document", subject_id: "doc-009" }),
    ]);
    expect(ex.steps.map((s) => [s.title, s.items])).toEqual([
      ["get_document", ["doc-001 · v3"]],
      ["Classification in final reply", ["doc-001 → OUTDATED"]],
      ["Final reply accepted", ["policy P2 matches the current version"]],
    ]);
  });

  it("uses raw MCP calls only when no semantic events exist (older archives)", () => {
    const ex = buildExecution(run(), [ev("mcp.tool_call", { ...R, tool: "get_policy", policy_id: "shiftly-returns", version: 2 })]);
    expect(ex.steps.map((s) => s.title)).toEqual(["get_policy"]);
  });
});

describe("provenanceOf", () => {
  it("separates diagnostics and aborted runs from real Cominty executions", () => {
    expect(provenanceOf(run({ agent_id: null }))).toBe("diagnostic");
    expect(provenanceOf(run(), [ev("agent.run_started", { cominty_execution: false })])).toBe("diagnostic");
    expect(provenanceOf(run({ thread_id: null, message_id: null }))).toBe("aborted");
    expect(provenanceOf(run())).toBe("cominty");
  });
});

describe("traceSummary", () => {
  it("never surfaces model reasoning text", () => {
    const t = traceSummary({ ...run(), verdict: null, explanation: null, trace: [
      { kind: "llm", status: "success", tool: null, model: "Claude Sonnet 5.5", description: "private reasoning" },
      { kind: "tool_call", status: "success", tool: "subagent", model: null, description: "Retrieve policy and list channels" },
    ] });
    expect(t).toEqual({ models: ["Claude Sonnet 5.5"], modelSteps: 1, tools: [{ tool: "subagent", label: "Retrieve policy and list channels", status: "success" }] });
    expect(JSON.stringify(t)).not.toContain("private reasoning");
  });
});
