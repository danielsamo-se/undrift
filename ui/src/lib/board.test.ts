import { describe, expect, it } from "vitest";
import type { ActivityEvent, Category, ImpactDocument, ImpactSnapshot, Proposal } from "../api/types";
import { buildLanes, consistency, liveDocuments, nextAction, runInProgress } from "./board";

function doc(id: string, channel: string, category: Category | null, extra: Partial<ImpactDocument> = {}): ImpactDocument {
  return {
    id,
    channel,
    title: `Title ${id}`,
    content: `content of ${id}`,
    version: 1,
    updated_at: "2026-11-05T10:00:00+00:00",
    finding: category
      ? {
          category,
          classified_at: "2026-11-05T15:07:25+00:00",
          run_id: "msg-1",
          thread_id: "t",
          agent_id: "a",
          document_version: 1,
          policy_version: 2,
          evidence: null,
          evidence_verified: false,
          reason: null,
          prefilter: null,
          agrees_with_prefilter: null,
          current: true,
        }
      : null,
    prefilter: { state: "not_stale", stale_sentences: [], protected: {} },
    proposals: [],
    ...extra,
  };
}

function snap(documents: ImpactDocument[], channels = ["web", "legal", "crm"]): ImpactSnapshot {
  return {
    policy: { id: "p", version: 2, policy_type: "voluntary_return", voluntary_return_days: 30, effective_from: "2026-11-04", description: "" },
    policy_title: "P",
    policy_versions: [],
    activity: { events: 0, mcp_calls: 0, latest_seq: 0 },
    runs_total: 0,
    channels: channels.map((id) => ({ id, name: id.toUpperCase(), audience: "public" as const })),
    documents,
    latest_run: null,
  };
}

const pending: Proposal = {
  id: "prop-1",
  document_id: "d1",
  base_version: 1,
  policy_id: "p",
  policy_version: 2,
  original_content: "",
  proposed_content: "",
  diff: "",
  reason: "",
  status: "pending_review",
  created_at: "2026-11-05T15:07:25+00:00",
  created_by: "agent:cominty:a",
  provenance: {},
  reviewed_by: null,
  reviewed_at: null,
  review_note: null,
  applied_version: null,
  applied_at: null,
};

describe("buildLanes", () => {
  it("orders channels and documents by severity and drops empty channels", () => {
    const s = snap([doc("d1", "legal", "VALID_DISTINCT"), doc("d2", "web", "UNRELATED"), doc("d3", "web", "OUTDATED"), doc("d4", "crm", null)]);
    const lanes = buildLanes(s);
    expect(lanes.map((l) => l.channel.id)).toEqual(["web", "crm", "legal"]);
    expect(lanes[0]!.docs.map((d) => d.id)).toEqual(["d3", "d2"]);
  });

  it("filters by status and free text without hiding lane totals", () => {
    const s = snap([doc("d1", "web", "OUTDATED"), doc("d2", "web", "UNRELATED", { title: "Shipping" })]);
    const outdated = buildLanes(s, { statuses: new Set(["outdated"]), query: "" });
    expect(outdated[0]!.docs.map((d) => d.id)).toEqual(["d1"]);
    expect(outdated[0]!.total).toBe(2);
    expect(buildLanes(s, { statuses: null, query: "shipp" })[0]!.docs.map((d) => d.id)).toEqual(["d2"]);
    expect(buildLanes(s, { statuses: null, query: "nothing" })).toEqual([]);
  });

  it("keeps 25 documents across 6 channels fully addressable", () => {
    const channels = ["a", "b", "c", "d", "e", "f"];
    const docs = Array.from({ length: 25 }, (_, i) => doc(`d${i}`, channels[i % 6]!, i % 5 === 0 ? "OUTDATED" : "UNRELATED"));
    const lanes = buildLanes(snap(docs, channels));
    expect(lanes).toHaveLength(6);
    expect(lanes.flatMap((l) => l.docs)).toHaveLength(25);
  });
});

describe("consistency and next action", () => {
  it("counts drift, stale and unanalysed separately", () => {
    const stale = doc("d4", "web", "OUTDATED");
    stale.finding!.current = false;
    const c = consistency(snap([doc("d1", "web", "OUTDATED"), doc("d2", "web", "VALID_DISTINCT"), doc("d3", "web", null), stale]));
    expect([c.drifted, c.consistent, c.unanalyzed, c.stale]).toEqual([1, 1, 1, 1]);
  });

  it("prefers reviewing a pending proposal, then analysis, then nothing", () => {
    const outdated = doc("d1", "web", "OUTDATED");
    expect(nextAction(snap([outdated]), [pending])).toEqual({ kind: "review", proposalId: "prop-1", title: "Title d1" });
    expect(nextAction(snap([outdated]), [{ ...pending, status: "approved" }])).toMatchObject({ kind: "apply" });
    expect(nextAction(snap([outdated]), [])).toMatchObject({ kind: "analyse" });
    expect(nextAction(snap([doc("d2", "web", "UNRELATED")]), [])).toEqual({ kind: "none" });
  });
});

let seq = 100;
function ev(type: string, ts: string, metadata: Record<string, unknown> = {}, subject_id: string | null = null): ActivityEvent {
  seq += 1;
  return { seq, ts, type, actor: "x", status: "ok", subject_type: null, subject_id, metadata, error: null };
}

describe("live activity", () => {
  const now = new Date("2026-11-05T16:00:10Z").getTime();
  it("marks documents only for recent events that arrived after page load", () => {
    const old = ev("mcp.tool_call", "2026-11-05T16:00:05Z", { tool: "get_document", document_id: "d1" });
    const events = [
      old,
      ev("mcp.tool_call", "2026-11-05T16:00:06Z", { tool: "get_document", document_id: "d2" }),
      ev("agent.classification", "2026-11-05T16:00:07Z", {}, "d2"),
      ev("mcp.tool_call", "2026-11-05T15:00:00Z", { tool: "get_document", document_id: "d3" }),
    ];
    const live = liveDocuments(events, old.seq, now);
    expect([...live.entries()]).toEqual([["d2", "classified"]]);
  });

  it("detects a run in progress from the event log only", () => {
    expect(runInProgress([ev("agent.run_started", "2026-11-05T16:00:00Z")])).toBe(true);
    expect(runInProgress([ev("agent.run_started", "2026-11-05T16:00:00Z"), ev("agent.run_finished", "2026-11-05T16:00:09Z")])).toBe(false);
    expect(runInProgress([])).toBe(false);
  });
});
