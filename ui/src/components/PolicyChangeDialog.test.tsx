import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PolicyChangeDialog } from "./PolicyChangeDialog";

afterEach(cleanup);

it("keeps focus in the edited policy field across modal rerenders", () => {
  render(<PolicyChangeDialog policy={{ id: "p", version: 2, policy_type: "voluntary_return", voluntary_return_days: 30, effective_from: "2026-11-04", description: "Existing policy." }} onClose={() => {}} onChanged={() => {}} />);
  const text = screen.getByRole("textbox", { name: /authoritative policy text/i });
  text.focus();
  fireEvent.change(text, { target: { value: "Updated policy." } });
  expect(document.activeElement).toBe(text);
});


it("blocks an older draft after the policy prop advances", () => {
  const policy = { id: "p", version: 2, policy_type: "voluntary_return", voluntary_return_days: 30, effective_from: "2026-11-04", description: "Existing policy." };
  const callbacks = { onClose: vi.fn(), onChanged: vi.fn() };
  const view = render(<PolicyChangeDialog policy={policy} {...callbacks} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "My draft." } });
  view.rerender(<PolicyChangeDialog policy={{ ...policy, version: 3, description: "Other reviewer edit." }} {...callbacks} />);
  expect(screen.getByRole("textbox").getAttribute("value") ?? (screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("My draft.");
  expect((screen.getByRole("button", { name: /Create/ }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/Policy changed while editing/)).toBeTruthy();
});
