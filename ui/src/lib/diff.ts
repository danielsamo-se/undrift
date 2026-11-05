export type Op = "equal" | "remove" | "add";

export interface Segment {
  op: Op;
  text: string;
}

function tokenize(text: string): string[] {
  return text.match(/\s+|[^\s]+/g) ?? [];
}

/** Word-level diff (LCS over words and whitespace runs). Adjacent segments are merged. */
export function wordDiff(before: string, after: string): Segment[] {
  const a = tokenize(before);
  const b = tokenize(after);
  const n = a.length;
  const m = b.length;
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i]![j] = a[i] === b[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
    }
  }
  const out: Segment[] = [];
  const push = (op: Op, text: string) => {
    const last = out[out.length - 1];
    if (last && last.op === op) last.text += text;
    else out.push({ op, text });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push("equal", a[i]!);
      i++;
      j++;
    } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) {
      push("remove", a[i++]!);
    } else {
      push("add", b[j++]!);
    }
  }
  while (i < n) push("remove", a[i++]!);
  while (j < m) push("add", b[j++]!);
  return out;
}

export function side(segments: Segment[], which: "before" | "after"): Segment[] {
  const drop: Op = which === "before" ? "add" : "remove";
  return segments.filter((s) => s.op !== drop);
}

export interface DiffLine {
  kind: "file" | "hunk" | "context" | "add" | "remove" | "meta";
  text: string;
  oldNo?: number;
  newNo?: number;
}

/** Parse a unified diff into typed lines with old/new line numbers. */
export function parseUnifiedDiff(diff: string): DiffLine[] {
  const lines: DiffLine[] = [];
  let oldNo = 0;
  let newNo = 0;
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("--- ") || raw.startsWith("+++ ")) {
      lines.push({ kind: "file", text: raw });
      continue;
    }
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      lines.push({ kind: "hunk", text: raw });
    } else if (raw.startsWith("+")) {
      lines.push({ kind: "add", text: raw.slice(1), newNo: newNo++ });
    } else if (raw.startsWith("-")) {
      lines.push({ kind: "remove", text: raw.slice(1), oldNo: oldNo++ });
    } else if (raw.startsWith(" ")) {
      lines.push({ kind: "context", text: raw.slice(1), oldNo: oldNo++, newNo: newNo++ });
    } else if (raw) {
      lines.push({ kind: "meta", text: raw });
    }
  }
  return lines;
}

/** Split text around an exact quote, for highlighting evidence. */
export function highlight(text: string, quote: string | null | undefined): { text: string; mark: boolean }[] {
  if (!quote) return [{ text, mark: false }];
  const at = text.indexOf(quote);
  if (at < 0) return [{ text, mark: false }];
  return [
    { text: text.slice(0, at), mark: false },
    { text: quote, mark: true },
    { text: text.slice(at + quote.length), mark: false },
  ].filter((p) => p.text);
}
