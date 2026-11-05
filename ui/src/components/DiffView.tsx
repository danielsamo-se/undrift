import { useMemo, useState } from "react";
import { parseUnifiedDiff, side, wordDiff, type Segment } from "../lib/diff";
import { DocVersion } from "./ui";

type Mode = "inline" | "split" | "unified";

function countWords(segments: Segment[], op: "add" | "remove"): number {
  return segments.filter((s) => s.op === op).reduce((n, s) => n + (s.text.match(/\S+/g)?.length ?? 0), 0);
}

function Segments({ segments }: { segments: Segment[] }) {
  return (
    <>
      {segments.map((s, i) =>
        s.op === "add" ? <ins key={i}>{s.text}</ins> : s.op === "remove" ? <del key={i}>{s.text}</del> : <span key={i}>{s.text}</span>,
      )}
    </>
  );
}

export function DiffView({ before, after, unified, baseVersion, documentId }: {
  before: string;
  after: string;
  unified: string;
  baseVersion: number;
  documentId: string;
}) {
  const [mode, setMode] = useState<Mode>("inline");
  const segments = useMemo(() => wordDiff(before, after), [before, after]);
  const lines = useMemo(() => parseUnifiedDiff(unified), [unified]);
  const added = countWords(segments, "add");
  const removed = countWords(segments, "remove");

  return (
    <div className="diff">
      <div className="diff-bar">
        <span className="diff-file">
          <code>{documentId}</code> <DocVersion version={baseVersion} /> <span className="muted">→ proposed</span>
        </span>
        <span className="diff-stat">
          <span className="add">+{added}</span> <span className="rem">−{removed}</span> <span className="muted">words</span>
        </span>
        <div className="seg" role="tablist" aria-label="Diff mode">
          {(["inline", "split", "unified"] as Mode[]).map((m) => (
            <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? "is-active" : ""} onClick={() => setMode(m)}>
              {m[0]!.toUpperCase() + m.slice(1)}
            </button>
          ))}
        </div>
      </div>
      {mode === "inline" && (
        <div className="diff-body diff-inline">
          <p>
            <Segments segments={segments} />
          </p>
        </div>
      )}
      {mode === "split" && (
        <div className="diff-body diff-split">
          <div>
            <p className="diff-col">Published</p>
            <p>
              <Segments segments={side(segments, "before")} />
            </p>
          </div>
          <div>
            <p className="diff-col">Proposed</p>
            <p>
              <Segments segments={side(segments, "after")} />
            </p>
          </div>
        </div>
      )}
      {mode === "unified" && (
        <div className="diff-body diff-unified" role="table" aria-label="Unified diff">
          {lines
            .filter((l) => l.kind !== "file")
            .map((l, i) => (
              <div key={i} className={`dl dl-${l.kind}`} role="row">
                <span className="dl-no" role="cell">{l.oldNo ?? ""}</span>
                <span className="dl-no" role="cell">{l.newNo ?? ""}</span>
                <span className="dl-sign" role="cell">{l.kind === "add" ? "+" : l.kind === "remove" ? "−" : ""}</span>
                <span className="dl-text" role="cell">{l.text}</span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
