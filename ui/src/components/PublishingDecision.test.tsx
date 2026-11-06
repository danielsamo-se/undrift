import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PublishingDecision } from "./PublishingDecision";
import type { ProposalDetail, Publication, PublicationPlan } from "../api/types";
import { api } from "../api/client";
import { reviewerSession } from "../api/session";

vi.mock("../api/client", () => ({ api: { publication: vi.fn(), publicationPlan: vi.fn(), publish: vi.fn(), refreshPublication: vi.fn(), verifySession: vi.fn() } }));
const detail = {
  proposal: { id: "prop-test", status: "approved", base_version: 3, document_id: "doc-001", diff: "", original_content: "60 days", proposed_content: "30 days" },
  checks: { document_unchanged: true, policy_current: true },
} as ProposalDetail;
const plan: PublicationPlan = { digest: "a".repeat(64), base_commit: "b".repeat(40), target: { repository: "owner/shiftly", branch: "main", path: "content/returns.txt", site_url: "https://shiftly.vercel.app" }, original_content: "60 days", proposed_content: "30 days" };
const pr: Publication = { status: "pr_created", digest: plan.digest, target: plan.target, events: [], pr: { number: 7, url: "https://github.com/owner/shiftly/pull/7", commit: "c".repeat(40) } };

beforeEach(() => {
  vi.resetAllMocks(); reviewerSession.set("test-only-token", "reviewer");
  vi.mocked(api.publication).mockResolvedValue({ configured: true, publication: null });
  vi.mocked(api.publicationPlan).mockResolvedValue({ plan });
  vi.mocked(api.publish).mockResolvedValue({ publication: pr });
  vi.mocked(api.verifySession).mockResolvedValue({ reviewer: "reviewer" });
});
afterEach(() => { cleanup(); reviewerSession.clear(); });

describe("repository authorization", () => {
  it("requires a second human decision and sends the exact preview digest", async () => {
    render(<PublishingDecision detail={detail} />);
    const preview = await screen.findByRole("button", { name: "Review repository change" });
    await waitFor(() => expect((preview as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(preview);
    expect(await screen.findByText("owner/shiftly")).toBeTruthy();
    expect(api.publish).not.toHaveBeenCalled();
    vi.mocked(api.publication).mockResolvedValue({ configured: true, publication: pr });
    fireEvent.click(screen.getByRole("button", { name: "Authorize & create PR" }));
    await waitFor(() => expect(api.publish).toHaveBeenCalledWith("prop-test", plan.digest));
    expect(await screen.findByRole("link", { name: /Open pull request #7/ })).toBeTruthy();
    expect(api.refreshPublication).not.toHaveBeenCalled();
  });
  it("requires an unlocked reviewer", async () => {
    reviewerSession.clear();
    render(<PublishingDecision detail={detail} />);
    const button = await screen.findByRole("button", { name: "Review repository change" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(api.publicationPlan).not.toHaveBeenCalled();
  });
  it("does not offer publishing for a rejected proposal", async () => {
    render(<PublishingDecision detail={{ ...detail, proposal: { ...detail.proposal, status: "rejected" } }} />);
    expect(screen.queryByRole("button", { name: "Review repository change" })).toBeNull();
  });
  it("preserves a failed authorization for review without claiming a PR exists", async () => {
    vi.mocked(api.publish).mockRejectedValue(new Error("Repository plan changed"));
    render(<PublishingDecision detail={detail} />);
    const button = await screen.findByRole("button", { name: "Review repository change" });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: "Authorize & create PR" }));
    await waitFor(() => expect(screen.getAllByRole("alert").some(el => el.textContent === "Repository plan changed")).toBe(true));
    expect(screen.queryByRole("link", { name: /Open pull request/ })).toBeNull();
  });
  it("checks deployment only on request and keeps deployment separate from verification", async () => {
    vi.mocked(api.publication).mockResolvedValue({ configured: true, publication: { ...pr, status: "deployed" } });
    vi.mocked(api.refreshPublication).mockResolvedValue({ publication: pr });
    render(<PublishingDecision detail={detail} />);
    const check = await screen.findByRole("button", { name: "Check merge and deployment" });
    expect(api.refreshPublication).not.toHaveBeenCalled();
    expect(screen.queryByText(/Verified public policy/)).toBeNull();
    fireEvent.click(check);
    await waitFor(() => expect(api.refreshPublication).toHaveBeenCalledWith("prop-test"));
  });
  it("offers the existing unlock flow for deployment checks without automatically refreshing", async () => {
    reviewerSession.clear();
    vi.mocked(api.publication).mockResolvedValue({ configured: true, publication: pr });
    const { unmount } = render(<PublishingDecision detail={detail} />);
    fireEvent.click(await screen.findByRole("button", { name: "Unlock reviewer session to check deployment" }));
    expect(await screen.findByRole("dialog", { name: "Unlock reviewer session" })).toBeTruthy();
    const input = screen.getByLabelText("Review token") as HTMLInputElement;
    expect(input.type).toBe("password");
    fireEvent.change(input, { target: { value: "private-test-only-reviewer-token" } });
    fireEvent.click(screen.getByRole("button", { name: /^Unlock$/ }));
    const check = await screen.findByRole("button", { name: "Check merge and deployment" });
    expect((check as HTMLButtonElement).disabled).toBe(false);
    expect(api.verifySession).toHaveBeenCalledWith("private-test-only-reviewer-token");
    expect(reviewerSession.token()).toBe("private-test-only-reviewer-token");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
    expect(api.refreshPublication).not.toHaveBeenCalled();
    expect(api.publish).not.toHaveBeenCalled();
    unmount();
    // A new page/module starts locked: clearing memory must require unlocking again.
    reviewerSession.clear();
    render(<PublishingDecision detail={detail} />);
    expect(await screen.findByRole("button", { name: "Unlock reviewer session to check deployment" })).toBeTruthy();
    expect(api.refreshPublication).not.toHaveBeenCalled();
  });
  it("blocks duplicate deployment checks while a request is pending and relocks when memory is cleared", async () => {
    vi.mocked(api.publication).mockResolvedValue({ configured: true, publication: pr });
    let finish!: (value: { publication: Publication }) => void;
    vi.mocked(api.refreshPublication).mockReturnValue(new Promise(resolve => { finish = resolve; }));
    render(<PublishingDecision detail={detail} />);
    const check = await screen.findByRole("button", { name: "Check merge and deployment" });
    fireEvent.click(check);
    expect((check as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(check);
    expect(api.refreshPublication).toHaveBeenCalledTimes(1);
    await act(async () => finish({ publication: pr }));
    await waitFor(() => expect((check as HTMLButtonElement).disabled).toBe(false));
    act(() => reviewerSession.clear());
    expect(await screen.findByRole("button", { name: "Unlock reviewer session to check deployment" })).toBeTruthy();
    expect(api.publish).not.toHaveBeenCalled();
  });
  it("reloads persisted blocked state after refresh errors and removes a stale verified banner", async () => {
    vi.mocked(api.publication).mockResolvedValueOnce({ configured: true, publication: {
      ...pr, status: "verified", verified_at: "2026-11-06T12:00:00Z",
    } }).mockResolvedValue({ configured: true, publication: { ...pr, status: "blocked", error: "PR head changed" } });
    vi.mocked(api.refreshPublication).mockRejectedValue(new Error("PR head changed"));
    render(<PublishingDecision detail={detail} />);
    expect(await screen.findByText(/Verified public policy/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Check merge and deployment" }));
    expect(await screen.findByText(/Publishing is blocked/)).toBeTruthy();
    expect(screen.queryByText(/Verified public policy/)).toBeNull();
    expect(api.publication).toHaveBeenCalledTimes(2);
    expect(api.publish).not.toHaveBeenCalled();
  });
});
