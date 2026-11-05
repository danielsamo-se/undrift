import { describe, expect, it } from "vitest";
import { highlight, parseUnifiedDiff, side, wordDiff } from "./diff";

const BEFORE = "All customers can return items within 60 days.";
const AFTER =
  "Customers can return items within 30 days for purchases made on or after 2026-11-04. Purchases made before 2026-11-04 keep the return period promised at the time of purchase.";

describe("wordDiff", () => {
  it("reconstructs both sides exactly", () => {
    const segs = wordDiff(BEFORE, AFTER);
    expect(side(segs, "before").map((s) => s.text).join("")).toBe(BEFORE);
    expect(side(segs, "after").map((s) => s.text).join("")).toBe(AFTER);
  });

  it("marks only the changed words", () => {
    const segs = wordDiff("return within 60 days.", "return within 30 days.");
    expect(segs).toEqual([
      { op: "equal", text: "return within " },
      { op: "remove", text: "60" },
      { op: "add", text: "30" },
      { op: "equal", text: " days." },
    ]);
  });

  it("handles empty inputs", () => {
    expect(wordDiff("", "new")).toEqual([{ op: "add", text: "new" }]);
    expect(wordDiff("old", "")).toEqual([{ op: "remove", text: "old" }]);
  });
});

describe("parseUnifiedDiff", () => {
  it("parses the server's diff format with line numbers", () => {
    const diff = `--- doc-001@v3\n+++ doc-001@proposed\n@@ -1 +1 @@\n-${BEFORE}\n+${AFTER}`;
    const lines = parseUnifiedDiff(diff);
    expect(lines.map((l) => l.kind)).toEqual(["file", "file", "hunk", "remove", "add"]);
    expect(lines[3]).toMatchObject({ text: BEFORE, oldNo: 1 });
    expect(lines[4]).toMatchObject({ text: AFTER, newNo: 1 });
  });

  it("numbers context lines on both sides", () => {
    const lines = parseUnifiedDiff("--- a\n+++ b\n@@ -1,3 +1,3 @@\n intro\n-old\n+new\n outro");
    expect(lines.filter((l) => l.kind === "context").map((l) => [l.oldNo, l.newNo])).toEqual([
      [1, 1],
      [3, 3],
    ]);
  });
});

describe("highlight", () => {
  it("marks an exact quote and leaves text untouched otherwise", () => {
    expect(highlight("A. B quote here. C.", "B quote here.")).toEqual([
      { text: "A. ", mark: false },
      { text: "B quote here.", mark: true },
      { text: " C.", mark: false },
    ]);
    expect(highlight("text", "missing")).toEqual([{ text: "text", mark: false }]);
    expect(highlight("text", null)).toEqual([{ text: "text", mark: false }]);
  });
});
