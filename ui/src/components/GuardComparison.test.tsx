import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { GuardCheck } from "../api/types";
import { GuardComparison, caughtOnlyByGuard, realGuardDecisions } from "./GuardComparison";

afterEach(cleanup);

const real = (id: string, decision: "ALLOW" | "BLOCK", text: string, created: string, extra: Partial<GuardCheck> = {}): GuardCheck => ({
  id, database_id: "db", created_at: created, requested_by: "local-reviewer", input_contract: "undrift.guard.input.v1",
  output_contract: "undrift.guard.output.v1", channel_id: "help-center", candidate_sha256: "a".repeat(64), candidate_length: text.length,
  candidate_redacted: text, case_sha256: "b".repeat(64), policy_id: "shiftly-returns", policy_version: 2, evidence: [], facts: [],
  precheck: { verdict: "CLEAR", findings: [] },
  execution: { mode: "cominty", status: "success", source: "cominty", error: null, agent_id: "_ofnqauQg4_y", thread_id: "t", message_id: "m-1",
    duration_s: 5.7, models: ["Claude Sonnet 5.5"], tool_calls: [], cost: { total: "0.0281955" } },
  agent_output: { verdict: decision, uncertainty: null, findings: [{ claim: text, assessment: decision === "ALLOW" ? "SUPPORTED" : "CONTRADICTS_POLICY",
    evidence_refs: ["P"], reason: decision === "ALLOW" ? "P states 30 days." : "P sets 30 days, not eight weeks (56 days)." }] },
  verification: { ok: true, problems: [], stale: [] }, decision, decision_source: "cominty_guard", release_eligible: decision === "ALLOW",
  reasons: [], ...extra,
});

const allow = real("gchk-allow", "ALLOW", "Orders purchased on or after November 4, 2026 get 30 days.", "2026-11-07T11:38:49+00:00");
const block = real("gchk-block", "BLOCK", "Customers have eight weeks to return their purchases.", "2026-11-07T17:32:12+00:00");
const deterministic = { ...block, id: "gchk-det", decision_source: "deterministic_precheck" as const, execution: { ...block.execution, mode: "disabled", status: "not_configured" },
  precheck: { verdict: "BLOCK" as const, findings: [{ rule: "superseded_period", verdict: "BLOCK", sentence: "x", reason: "60 days" }] } };
const failClosed = { ...allow, id: "gchk-fc", decision: "NEEDS_REVIEW" as const, decision_source: "fail_closed" as const, release_eligible: false,
  verification: { ok: false, problems: ["no JSON output block"], stale: [] } };

describe("GuardComparison", () => {
  it("compares only server-verified real Cominty decisions, oldest first", () => {
    expect(realGuardDecisions([block, deterministic, failClosed, allow]).map((c) => c.id)).toEqual(["gchk-allow", "gchk-block"]);
    render(<GuardComparison checks={[block, deterministic, allow]} policy={{ id: "shiftly-returns", version: 2, policy_type: "voluntary_return", voluntary_return_days: 30, effective_from: "2026-11-04" } as never} />);
    const cards = screen.getAllByTestId("guard-compare-card");
    expect(cards.map((c) => c.querySelector(".guard-decision")?.textContent)).toEqual(["Allow", "Block"]);
    expect(screen.getByText(/2 real Cominty Guard decisions: Allow and Block/)).toBeTruthy();
    expect(cards[0]!.textContent).toMatch(/Release-eligible/);
    expect(cards[1]!.textContent).toMatch(/Not release-eligible/);
    expect(screen.getByTestId("guard-only-catch").textContent).toMatch(/Caught only by the AI Guard.*eight weeks \(56 days\)/);
    expect(screen.getByText(/does not establish that a text is semantically correct/)).toBeTruthy();
  });

  it("does not claim an AI-only catch when the precheck already fired, and hides with fewer than two real checks", () => {
    expect(caughtOnlyByGuard({ ...block, precheck: deterministic.precheck })).toBe(false);
    expect(caughtOnlyByGuard(block)).toBe(true);
    const { container } = render(<GuardComparison checks={[allow, deterministic]} policy={null} />);
    expect(container.textContent).toBe("");
  });
});
