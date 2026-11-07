import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AutonomyPolicy } from "../api/types";
import { proposedBy } from "../lib/format";
import { ChannelPolicy, blockerText } from "./Autonomy";

afterEach(cleanup);

function policy(overrides: Partial<AutonomyPolicy> = {}): AutonomyPolicy {
  return { id: "help-center", name: "Help Center", audience: "public", channel_id: "help-center", version: 0,
    mode: "review_required", effective_mode: "review_required", max_per_run: 1,
    allowed_modes: ["review_required", "bounded_auto"], schema_available: false, persisted: false,
    configuration_valid: true, updated_at: null, updated_by: null, note: "", ...overrides };
}

describe("channel autonomy card", () => {
  it("tells a live operator that autonomy is not enabled, without calling the database an archive", () => {
    render(<ChannelPolicy policy={policy()} readonly={false} onChanged={() => {}} />);
    expect(screen.getByText(/Autonomy not enabled for this database/)).toBeTruthy();
    expect(screen.queryByText(/archive/)).toBeNull();
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
  });

  it("keeps archive wording for read-only replays", () => {
    render(<ChannelPolicy policy={policy()} readonly onChanged={() => {}} />);
    expect(screen.getByText(/No recorded delegation in this archive/)).toBeTruthy();
  });

  it("shows a recorded delegation revision and its author once enabled", () => {
    render(<ChannelPolicy policy={policy({ schema_available: true, persisted: true, version: 1, mode: "bounded_auto",
      effective_mode: "bounded_auto", updated_by: "local-reviewer", note: "Delegate one sentence." })} readonly={false} onChanged={() => {}} />);
    expect(screen.getByText(/Delegation revision 1 · set by local-reviewer/)).toBeTruthy();
    expect(screen.getByText("Delegate one sentence.")).toBeTruthy();
  });
});

describe("attribution and reasons", () => {
  it("labels only real Cominty actors as the Cominty agent", () => {
    expect(proposedBy("agent:cominty:-cS4pr7oTPO4")).toBe("proposed by Cominty agent");
    expect(proposedBy("agent:local-fixture")).not.toMatch(/Cominty/);
    expect(proposedBy("reviewer:local-reviewer")).toBe("proposed by Reviewer local-reviewer");
  });

  it("maps every server blocker to readable text", () => {
    expect(blockerText("delegation_not_pinned_or_changed")).toMatch(/pinned when the run started/);
    expect(blockerText("some_new_code")).toBe("some new code");
  });
});
