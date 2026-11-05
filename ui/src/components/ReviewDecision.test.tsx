import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { reviewerSession } from "../api/session";
import type { ProposalDetail } from "../api/types";
import { ReviewDecision } from "./ReviewDecision";

const TOKEN = "t".repeat(43);

function detail(overrides: Partial<ProposalDetail["proposal"]> = {}, checks: Partial<ProposalDetail["checks"]> = {}): ProposalDetail {
  return {
    proposal: {
      id: "prop-1",
      document_id: "doc-001",
      base_version: 3,
      policy_id: "shiftly-returns",
      policy_version: 2,
      original_content: "old",
      proposed_content: "new",
      diff: "",
      reason: "r",
      status: "pending_review",
      created_at: "2026-11-05T15:07:25+00:00",
      created_by: "agent:cominty:x",
      provenance: {},
      reviewed_by: null,
      reviewed_at: null,
      review_note: null,
      applied_version: null,
      applied_at: null,
      ...overrides,
    },
    events: [],
    review_flags: [],
    checks: { document_current_version: 3, policy_current_version: 2, document_unchanged: true, policy_current: true, ...checks },
  };
}

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(body), { status }));
}

beforeEach(() => reviewerSession.clear());
afterEach(() => {
  cleanup();
  reviewerSession.clear();
});

describe("ReviewDecision", () => {
  it("shows the review flow and stays locked without a reviewer session", () => {
    const fetchSpy = mockFetch(200, {});
    render(<ReviewDecision detail={detail()} onChanged={() => {}} />);
    expect(screen.getByRole("list", { name: /review flow/i }).textContent).toContain("Apply locally");
    expect(screen.getByRole("button", { name: /^approve$/i }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: /reject/i }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: /unlock reviewer session/i })).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("approves only after explicit confirmation, sending the in-memory token and note", async () => {
    reviewerSession.set(TOKEN, "local-reviewer");
    const fetchSpy = mockFetch(200, { proposal: { ...detail().proposal, status: "approved" } });
    const onChanged = vi.fn();
    render(<ReviewDecision detail={detail()} onChanged={onChanged} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "checked" } });
    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    expect(fetchSpy).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^approve$/i }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const [url, init] = fetchSpy.mock.calls[0]!;
    expect(url).toBe("/api/proposals/prop-1/approve");
    expect((init!.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
    expect(JSON.parse(init!.body as string)).toEqual({ note: "checked" });
  });

  it("blocks approval when the server reports a stale policy", () => {
    reviewerSession.set(TOKEN, "r");
    render(<ReviewDecision detail={detail({}, { policy_current: false, policy_current_version: 3 })} onChanged={() => {}} />);
    expect(screen.getByRole("button", { name: /^approve$/i }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/server will refuse/i)).toBeTruthy();
  });

  it("offers apply as a separate confirmed step and locks again on 401", async () => {
    reviewerSession.set(TOKEN, "r");
    mockFetch(401, { error: "unauthorized", message: "missing or invalid review token" });
    render(<ReviewDecision detail={detail({ status: "approved", reviewed_by: "r" })} onChanged={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /apply locally/i }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Nothing is published to any external/i)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: /apply locally/i }));
    await waitFor(() => expect(screen.getByText(/invalid review token/i)).toBeTruthy());
    expect(reviewerSession.get()).toBeNull();
  });

  it("shows closed proposals read-only", () => {
    render(<ReviewDecision detail={detail({ status: "applied", applied_version: 4, reviewed_by: "r" })} onChanged={() => {}} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/Applied locally as/)).toBeTruthy();
  });
});

describe("reviewer session", () => {
  it("keeps the token out of persistent browser storage", () => {
    reviewerSession.set(TOKEN, "r");
    expect(JSON.stringify({ ...localStorage })).not.toContain(TOKEN);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(TOKEN);
    expect(document.cookie).not.toContain(TOKEN);
    expect(window.location.href).not.toContain(TOKEN);
  });

  it("refuses authorized calls while locked", async () => {
    const fetchSpy = mockFetch(200, {});
    await expect(api.approve("prop-1", null)).rejects.toMatchObject({ status: 401, code: "locked" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
