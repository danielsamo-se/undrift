import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Proposal, Publication } from "../api/types";
import { PublishedEvidence } from "./PublishedEvidence";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const proposal = { id: "prop-04ca726838fc", document_id: "doc-001", status: "approved" } as Proposal;
const ev = (type: string) => ({ seq: 1, ts: "2026-11-06T16:00:00+00:00", type, metadata: { actor: "local-reviewer" } });
const target = { repository: "danielsamo-se/shiftly-storefront", branch: "main", path: "content/returns.txt", site_url: "https://shiftly-storefront.vercel.app" };

it("shows only recorded publishing stages and separates publication from local application", async () => {
  const publication = {
    status: "verified", digest: "d", target,
    pr: { number: 1, url: "https://github.com/danielsamo-se/shiftly-storefront/pull/1", commit: "2e73726" }, merge_commit: "c391b36083663d06bbd0cd98f72806daf005b3e2",
    verification_evidence: { source: "production_site", site_url: target.site_url, checked_at: "", expected_merge_commit: "c391b36", observed_build_commit: "c391b36083663d06",
      approved_content_sha256: "8940b17cfec95a74", raw_policy_sha256: "8940b17cfec95a74", rendered_policy_sha256: "8940b17cfec95a74", returns_page_sha256: "x",
      policy_element_count: 1, http_statuses: { "/returns/": 200 } },
    events: [ev("publishing.approved"), ev("publishing.pr_created"), ev("publishing.merged"), ev("publishing.deployed"), ev("publishing.verified")],
  } as unknown as Publication;
  vi.spyOn(api, "publication").mockResolvedValue({ configured: true, publication });
  render(<PublishedEvidence proposals={[proposal]} notCurrent={4} />);
  await waitFor(() => expect(screen.getByTestId("published-evidence")).toBeTruthy());
  const box = screen.getByTestId("published-evidence").textContent ?? "";
  expect(box).toMatch(/Published to the live website and verified/);
  expect(box).toMatch(/by local-reviewer/);
  expect(box).toMatch(/matches the approved text/);
  expect(box).toMatch(/Two different operations/);
  expect(box).toMatch(/approved, not applied/);
  expect(box).toMatch(/“not current” count concerns local analysis/);
  expect(box).not.toMatch(/not recorded/);
  expect(screen.getByRole("link", { name: /View the live page/ }).getAttribute("href")).toBe("https://shiftly-storefront.vercel.app/returns/");
});

it("marks missing stages as not recorded and renders nothing without publishing evidence", async () => {
  vi.spyOn(api, "publication").mockResolvedValueOnce({ configured: true,
    publication: { status: "pr_created", digest: "d", target, events: [ev("publishing.approved"), ev("publishing.pr_created")] } as unknown as Publication });
  render(<PublishedEvidence proposals={[proposal]} notCurrent={0} />);
  await waitFor(() => expect(screen.getByTestId("published-evidence")).toBeTruthy());
  expect(screen.getAllByText("not recorded")).toHaveLength(3);
  expect(screen.getByTestId("published-evidence").textContent).not.toMatch(/not current/);
  cleanup();
  vi.spyOn(api, "publication").mockResolvedValue({ configured: false, publication: null });
  const { container } = render(<PublishedEvidence proposals={[proposal]} notCurrent={0} />);
  await new Promise((r) => setTimeout(r, 20));
  expect(container.textContent).toBe("");
});
