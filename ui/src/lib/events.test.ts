import { describe, expect, it } from "vitest";
import type { ActivityEvent } from "../api/types";
import { statusOf } from "./classification";
import { buildTimeline, describeEvent, groupOf } from "./events";
import { parseHash } from "./hooks";

let seq = 0;
function ev(type: string, metadata: Record<string, unknown> = {}, extra: Partial<ActivityEvent> = {}): ActivityEvent {
  seq += 1;
  return {
    seq,
    ts: `2026-11-05T15:07:${String(seq).padStart(2, "0")}+00:00`,
    type,
    actor: "system",
    status: "ok",
    subject_type: null,
    subject_id: null,
    metadata,
    error: null,
    ...extra,
  };
}

const prov = { message_id: "msg-1", thread_id: "t-1", agent_id: "a", source: "cominty" };

describe("buildTimeline", () => {
  it("groups MCP calls and run results under the agent run", () => {
    const events = [
      ev("policy.version_created", { version: 2 }),
      ev("agent.run_started", { agent_id: "a" }),
      ev("mcp.tool_call", { tool: "get_document", document_id: "doc-001", version: 3 }),
      ev("mcp.tool_call", { tool: "get_policy", policy_id: "shiftly-returns", version: 2 }),
      ev("agent.run_finished", { message_id: "msg-1", duration_s: 23.6 }),
      ev("agent.verdict_received", { provenance: prov, reported_policy_version: 2 }),
      ev("agent.classification", { provenance: prov, category: "OUTDATED" }, { subject_id: "doc-001" }),
      ev("proposal.created", { provenance: prov, document_id: "doc-001" }, { subject_id: "prop-1" }),
      ev("mcp.tool_call", { tool: "health_check" }),
    ];
    const items = buildTimeline(events);
    expect(items.map((i) => i.kind)).toEqual(["event", "run", "event"]);
    const run = items[1]!;
    if (run.kind !== "run") throw new Error("expected run");
    expect(run.id).toBe("msg-1");
    expect(run.status).toBe("success");
    expect(run.children.map((e) => e.type)).toEqual([
      "mcp.tool_call",
      "mcp.tool_call",
      "agent.verdict_received",
      "agent.classification",
      "proposal.created",
    ]);
  });

  it("reports an unfinished run as running and a rejected verdict as an error", () => {
    const running = buildTimeline([ev("agent.run_started"), ev("mcp.tool_call", { tool: "get_policy" })]);
    expect(running[0]).toMatchObject({ kind: "run", status: "running" });
    const failed = buildTimeline([
      ev("agent.run_started"),
      ev("agent.run_finished", { message_id: "m2" }),
      ev("agent.verdict_received", { provenance: { message_id: "m2" } }, { status: "error", error: "bad" }),
    ]);
    expect(failed[0]).toMatchObject({ kind: "run", status: "error" });
  });
});

describe("describeEvent", () => {
  it("describes MCP calls with the served version", () => {
    const v = describeEvent(ev("mcp.tool_call", { tool: "get_document", document_id: "doc-001", version: 3, duration_ms: 4.2, user_agent: "Cominty" }));
    expect(v.title).toBe("get_document · doc-001");
    expect(v.detail).toBe("served v3 · 4.2 ms · Cominty");
    expect(v.group).toBe("mcp");
  });

  it("links proposals to the review queue and never claims success on errors", () => {
    expect(describeEvent(ev("proposal.created", { document_id: "doc-001", lines_added: 1, lines_removed: 1, base_version: 3, policy_version: 2 }, { subject_id: "prop-x" })).link).toBe("#/review/prop-x");
    const failed = describeEvent(ev("mcp.tool_call", { tool: "get_document" }, { status: "error", error: "Unknown document_id" }));
    expect(failed.tone).toBe("danger");
    expect(failed.detail).toBe("Unknown document_id");
  });

  it("covers every event type the backend emits", () => {
    for (const type of [
      "store.seeded", "server.started", "policy.version_created", "impact.candidates_detected",
      "agent.run_started", "agent.run_finished", "agent.verdict_received", "agent.classification",
      "agent.reply_unparsed", "proposal.created", "proposal.submission_failed", "proposal.approved",
      "proposal.rejected", "proposal.applied", "proposal.apply_failed", "document.version_created", "auth.denied",
    ]) {
      expect(describeEvent(ev(type)).title, type).not.toBe(type);
    }
    expect(groupOf("auth.denied")).toBe("security");
  });
});

describe("classification and routing", () => {
  it("maps agent categories and distinguishes unanalysed documents", () => {
    const f = (category: string) => ({ finding: { category } as never });
    expect(statusOf(f("OUTDATED"))).toBe("outdated");
    expect(statusOf(f("EXISTING_COMMITMENT"))).toBe("commitment");
    expect(statusOf(f("SOMETHING_NEW"))).toBe("unknown");
    expect(statusOf({ finding: null })).toBe("unanalyzed");
  });

  it("parses deep links", () => {
    expect(parseHash("#/review/prop-04ca726838fc")).toEqual({ view: "review", param: "prop-04ca726838fc" });
    expect(parseHash("#/impact")).toEqual({ view: "impact", param: null });
    expect(parseHash("")).toEqual({ view: "overview", param: null });
    expect(parseHash("#/nope/x")).toEqual({ view: "overview", param: "x" });
  });
});
