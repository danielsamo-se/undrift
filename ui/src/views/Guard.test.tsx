import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { GuardCheck } from "../api/types";
import { GuardResult, SOURCE_LABEL } from "./Guard";

afterEach(cleanup);

function check(overrides: Partial<GuardCheck> = {}): GuardCheck {
  return {
    id: "gchk-1", database_id: "db", created_at: "2026-11-07T12:00:00+00:00", requested_by: "local-reviewer",
    input_contract: "undrift.guard.input.v1", output_contract: "undrift.guard.output.v1", channel_id: "support-snippets",
    candidate_sha256: "a".repeat(64), candidate_length: 48, candidate_redacted: "All customers have 60 days.", case_sha256: "b".repeat(64),
    policy_id: "shiftly-returns", policy_version: 2, evidence: [], facts: [],
    precheck: { verdict: "BLOCK", findings: [{ rule: "superseded_period", verdict: "BLOCK", sentence: "All customers have 60 days.", reason: "States a superseded period." }] },
    execution: { mode: "disabled", status: "not_configured", source: "none", error: "Cominty Guard agent is not configured or not enabled", agent_id: null, thread_id: null, message_id: null },
    agent_output: null, verification: { ok: false, problems: [], stale: [] },
    decision: "BLOCK", decision_source: "deterministic_precheck", release_eligible: false, reasons: ["States a superseded period."],
    ...overrides,
  };
}

describe("GuardResult", () => {
  it("labels a deterministic block honestly and never as agent output", () => {
    render(<GuardResult check={check()} />);
    const box = screen.getByTestId("guard-result").textContent ?? "";
    expect(box).toMatch(/Block/);
    expect(box).toMatch(/Deterministic precheck \(no AI\)/);
    expect(box).toMatch(/Not release-eligible\. Nothing has been sent or published/);
    expect(box).toMatch(/No agent output: Cominty Guard agent is not configured/);
    expect(box).not.toMatch(/Real Cominty Guard execution/);
  });

  it("marks test fixtures explicitly and shows verified Cominty ALLOW as release-eligible only", () => {
    render(<GuardResult check={check({ decision: "ALLOW", decision_source: "test_fixture", precheck: { verdict: "CLEAR", findings: [] } })} />);
    expect(screen.getByText(SOURCE_LABEL.test_fixture)).toBeTruthy();
    expect(screen.getByText(/Not release-eligible/)).toBeTruthy();
    cleanup();
    render(<GuardResult check={check({ decision: "ALLOW", decision_source: "cominty_guard", release_eligible: true,
      precheck: { verdict: "CLEAR", findings: [] },
      execution: { mode: "cominty", status: "success", source: "cominty", error: null, agent_id: "g", thread_id: "t", message_id: "m-123" },
      agent_output: { verdict: "ALLOW", uncertainty: null, findings: [{ claim: "Orders purchased on or after November 4, 2026", assessment: "SUPPORTED", evidence_refs: ["P"], reason: "Matches P." }] },
      verification: { ok: true, problems: [], stale: [] } })} />);
    const box = screen.getByTestId("guard-result").textContent ?? "";
    expect(box).toMatch(/Real Cominty Guard execution, verified by the server/);
    expect(box).toMatch(/Release-eligible: Guard cleared this exact text\. Nothing has been sent or published/);
    expect(box).toMatch(/SUPPORTED \[P\]/);
  });

  it("shows server rejection of an agent verdict", () => {
    render(<GuardResult check={check({ decision: "NEEDS_REVIEW", decision_source: "fail_closed", precheck: { verdict: "CLEAR", findings: [] },
      agent_output: { verdict: "ALLOW", uncertainty: null, findings: [] },
      verification: { ok: false, problems: ["finding 1: cites evidence that was not supplied (E9)"], stale: [] } })} />);
    expect(screen.getByText(/Rejected by server verification: finding 1: cites evidence that was not supplied/)).toBeTruthy();
    expect(screen.getByText(SOURCE_LABEL.fail_closed)).toBeTruthy();
  });
});
