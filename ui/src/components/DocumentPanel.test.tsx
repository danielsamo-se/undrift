import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ImpactDocument, Policy } from "../api/types";
import { DocumentPanel } from "./DocumentPanel";

afterEach(cleanup);
const policy: Policy = { id: "p", version: 2, policy_type: "voluntary_return", voluntary_return_days: 30,
  effective_from: "2026-11-04", description: "Current policy" };
function document(action: "needs_clarification" | "manual_only"): ImpactDocument {
  return { id: "d", title: "Return advice", channel: "crm", version: 1, content: "Check purchase date.",
    updated_at: "2026-11-04T00:00:00Z", proposals: [], prefilter: { state: "not_stale", stale_sentences: [], protected: {} },
    finding: { category: action === "needs_clarification" ? "UNKNOWN" : "OUTDATED", action,
      verified_conclusion: action === "manual_only", run_status: "incomplete", classified_at: "2026-11-04T00:00:00Z",
      run_id: "r", thread_id: null, agent_id: "a", document_version: 1, policy_version: 2,
      evidence: "Check purchase date.", evidence_verified: true, reason: "What was the purchase date?",
      prefilter: null, agrees_with_prefilter: null, current: true } };
}
it("shows the missing-context question and human clarification state", () => {
  render(<DocumentPanel doc={document("needs_clarification")} policy={policy} channel={undefined} onClose={vi.fn()} />);
  expect(screen.getByText(/Human clarification required/)).toBeTruthy();
  expect(screen.getByText("What was the purchase date?")).toBeTruthy();
  expect(screen.queryByText(/Provisional UNKNOWN/)).toBeNull();
});
it("shows the protected-channel editing boundary even for an outdated finding", () => {
  render(<DocumentPanel doc={document("manual_only")} policy={policy} channel={undefined} onClose={vi.fn()} />);
  expect(screen.getByText(/Legal and CRM documents require a separate manual editing workflow/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /approve|apply|publish/i })).toBeNull();
});
