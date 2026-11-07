import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { GuardCheck, MemorySnapshot, NotificationsSnapshot } from "../api/types";
import { MemoryRecallPanel } from "./Guard";
import { MemorySection, SlackSection } from "./Integrations";

afterEach(cleanup);

const POLICY = { id: "shiftly-returns", version: 2, sha256: "c".repeat(64) };

function memory(overrides: Partial<MemorySnapshot> = {}): MemorySnapshot {
  return {
    live: true, namespace: "undrift-shiftly-returns", error: null, journal_configured: true, writes: [],
    files: [{
      path: "shiftly-returns-v2/guard-16ca185bdee457a5.md", purpose: "p", version: "abc123", created_at: null, updated_at: null,
      status: "authoritative", written_by_this_service: true, changed_since_write: false,
      record: { contract: "undrift.memory.v1", kind: "verified_interpretation", policy: POLICY, verdict: "ALLOW",
        claims: [{ claim: "Orders purchased on or after November 4, 2026 ...", assessment: "SUPPORTED", evidence_refs: ["P"], reason: "P states 30 days." }],
        source: { guard_check_id: "gchk-1", message_id: "bdea19da-0c3c" }, saved_by: "local-reviewer", saved_at: "2026-11-07T12:00:00+00:00" },
    }],
    ...overrides,
  };
}

function notes(overrides: Partial<NotificationsSnapshot> = {}): NotificationsSnapshot {
  return {
    mode: "disabled", paid: false, connection: "slack_official", agent_configured: false, channel: null,
    connector: { name: "slack_official", title: "Slack Official", is_enabled: false, requires_oauth_connect: true, connection_status: null },
    connector_error: null, journal_configured: true, grants_approval: false,
    candidates: [{ key: "k".repeat(64), kind: "proposal_pending", subject_id: "prop-1", text: "Undrift: correction awaiting review", last_phase: null }],
    deliveries: [], ...overrides,
  };
}

describe("MemorySection", () => {
  it("labels genuine Cominty memory and its policy binding", () => {
    render(<MemorySection data={memory()} canWrite={false} onChanged={() => {}} />);
    const box = screen.getByTestId("memory-section").textContent ?? "";
    expect(box).toMatch(/Live data from the native Cominty Memory API/);
    expect(box).toMatch(/Current policy/);
    expect(box).toMatch(/Verified Guard verdict ALLOW for policy shiftly-returns v2/);
    expect(box).toMatch(/Cominty memory · version/);
  });

  it("marks historical memory and never claims Cominty data when disabled", () => {
    const m = memory();
    m.files[0] = { ...m.files[0]!, status: "historical" };
    render(<MemorySection data={m} canWrite={false} onChanged={() => {}} />);
    expect(screen.getByText(/Shown as history only, never as current policy/)).toBeTruthy();
    cleanup();
    render(<MemorySection data={memory({ live: false, files: [] })} canWrite={false} onChanged={() => {}} />);
    expect(screen.getByText(/Nothing below comes from Cominty/)).toBeTruthy();
  });
});

describe("SlackSection", () => {
  it("never claims delivery without a platform-confirmed record and states Slack has no approval authority", () => {
    render(<SlackSection data={notes()} canWrite={true} onChanged={() => {}} />);
    const box = screen.getByTestId("slack-section").textContent ?? "";
    expect(box).toMatch(/Slack Official: not enabled · requires OAuth connection/);
    expect(box).toMatch(/prepared but not active/);
    expect(box).toMatch(/No notification has been attempted\. No delivery is claimed/);
    expect(box).toMatch(/Approval, rejection and publishing happen in Undrift/);
    expect(screen.queryByText("Send to Slack")).toBeNull();
  });

  it("requires per-message paid confirmation and shows Slack evidence for delivered records", () => {
    render(<SlackSection data={notes({ mode: "cominty", paid: true, channel: "#undrift-review", agent_configured: true,
      deliveries: [{ id: "n1", created_at: "2026-11-07T12:00:00+00:00", key: "k".repeat(64), kind: "proposal_pending", subject_id: "prop-1",
        phase: "delivered", actor: "r", channel: "#undrift-review", text: "x", error: null,
        execution: { mode: "cominty", message_id: "m-1", slack: { message_ts: "1731.0001" } } }] })} canWrite={true} onChanged={() => {}} />);
    const send = screen.getByText("Send to Slack").closest("button") as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    expect(screen.getByText(/Delivered \(confirmed by Cominty\)/)).toBeTruthy();
    expect(screen.getByText("1731.0001")).toBeTruthy();
  });
});

describe("MemoryRecallPanel", () => {
  const check = { id: "gchk-2", decision: "NEEDS_REVIEW", decision_source: "fail_closed", verification: { ok: false, problems: [], stale: [] } } as unknown as GuardCheck;
  it("shows recall as context only and offers no save for unverified checks", () => {
    render(<MemoryRecallPanel check={check} canSave={true} onSaved={() => {}} recall={{ id: "r", check_id: "gchk-2", namespace: "undrift-shiftly-returns",
      status: "ok", error: null, created_at: "x", matches: [{ path: "shiftly-returns-v2/guard-1.md", version: "v", status: "authoritative", verdict: "ALLOW",
        policy: POLICY, source_check_id: "gchk-1", message_id: "m", saved_by: "r", saved_at: "x" }] }} />);
    const box = screen.getByTestId("memory-recall").textContent ?? "";
    expect(box).toMatch(/Remembered verdict ALLOW under policy v2, from check gchk-1/);
    expect(box).toMatch(/memory can never release text/);
    expect(screen.queryByText(/Save verified interpretation/)).toBeNull();
  });
});
