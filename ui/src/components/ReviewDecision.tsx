import { Check, Lock, Send, X } from "lucide-react";
import { useState } from "react";
import { ApiError, api } from "../api/client";
import { useReviewerSession } from "../api/session";
import type { Proposal, ProposalDetail } from "../api/types";
import { formatDateTime } from "../lib/format";
import { UnlockDialog } from "./ReviewerSession";
import { DocVersion, Modal, Spinner } from "./ui";

type Action = "approve" | "reject" | "apply";

const COPY: Record<Action, { title: string; button: string; body: string }> = {
  approve: {
    title: "Approve this correction?",
    button: "Approve",
    body: "Records your approval. The published document stays unchanged until the correction is applied in a separate step.",
  },
  reject: {
    title: "Reject this correction?",
    button: "Reject",
    body: "Closes the proposal. The document stays unchanged; a new agent run can propose a fresh correction.",
  },
  apply: {
    title: "Apply to the local demo store?",
    button: "Apply locally",
    body: "Writes the corrected text as a new document version in the local SQLite demo store. Nothing is published to any external website, repository or channel.",
  },
};

const STEPS = [
  { key: "pending_review", label: "Review" },
  { key: "approved", label: "Approve" },
  { key: "applied", label: "Apply locally" },
] as const;

function Flow({ status }: { status: Proposal["status"] }) {
  const at = status === "rejected" ? -1 : STEPS.findIndex((s) => s.key === status);
  return (
    <ol className="flow" aria-label="Review flow">
      {STEPS.map((s, i) => (
        <li key={s.key} className={i < at ? "is-done" : i === at ? "is-current" : ""} aria-current={i === at ? "step" : undefined}>
          <span className="flow-mark" aria-hidden>
            {i < at ? <Check size={10} /> : i + 1}
          </span>
          {s.label}
        </li>
      ))}
      {status === "rejected" && <li className="is-rejected">Rejected</li>}
    </ol>
  );
}

export function ReviewDecision({ detail, onChanged }: { detail: ProposalDetail; onChanged: (p: Proposal) => void }) {
  const session = useReviewerSession();
  const [pending, setPending] = useState<Action | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unlock, setUnlock] = useState(false);
  const { proposal: p, checks } = detail;
  const fresh = checks.document_unchanged && checks.policy_current;

  const run = async (action: Action) => {
    setBusy(true);
    setError(null);
    try {
      const res =
        action === "approve"
          ? await api.approve(p.id, note.trim() || null)
          : action === "reject"
            ? await api.reject(p.id, note.trim() || null)
            : await api.apply(p.id);
      setPending(null);
      setNote("");
      onChanged(res.proposal);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message}${err.status === 409 ? " (refused by the server)" : ""}` : String(err));
    } finally {
      setBusy(false);
    }
  };

  const closed = p.status === "applied" || p.status === "rejected";
  return (
    <section className="decision" aria-label="Decision">
      <header className="decision-head">
        <h2>Decision</h2>
        <Flow status={p.status} />
      </header>

      {closed ? (
        <p className="decision-outcome">
          {p.status === "applied" ? (
            <>
              Applied locally as <DocVersion version={p.applied_version} />
              {p.applied_at && ` on ${formatDateTime(p.applied_at)}`}.
            </>
          ) : (
            <>Rejected{p.reviewed_at && ` on ${formatDateTime(p.reviewed_at)}`}.</>
          )}
          {p.reviewed_by && <> Reviewer: {p.reviewed_by}.</>}
          {p.review_note && <span className="muted"> “{p.review_note}”</span>}
        </p>
      ) : (
        <>
          {p.status === "approved" && p.reviewed_by && (
            <p className="decision-outcome">
              Approved by {p.reviewed_by}
              {p.reviewed_at && ` on ${formatDateTime(p.reviewed_at)}`}
              {p.review_note && <span className="muted"> · “{p.review_note}”</span>}. Apply it to update the local demo store.
            </p>
          )}
          {!fresh && (
            <p className="note note-bad">
              The {checks.document_unchanged ? "policy" : "document"} changed after this proposal was created, so the server will
              refuse to {p.status === "approved" ? "apply" : "approve"} it. Reject it and run the agent again.
            </p>
          )}
          {p.status === "pending_review" && session && (
            <label className="field">
              <span>Review note (optional, stored with your decision)</span>
              <textarea rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Checked against P2 and the FAQ tone." />
            </label>
          )}
          <div className="decision-actions">
            {session ? (
              <span className="decision-who">
                Signed in as <strong>{session.reviewer}</strong>
              </span>
            ) : (
              <button className="btn" onClick={() => setUnlock(true)}>
                <Lock size={13} aria-hidden /> Unlock reviewer session
              </button>
            )}
            <span className="spacer" />
            <button className="btn btn-danger-quiet" disabled={!session} onClick={() => setPending("reject")}>
              <X size={13} aria-hidden /> Reject
            </button>
            {p.status === "pending_review" ? (
              <button className="btn btn-primary" disabled={!session || !fresh} onClick={() => setPending("approve")}>
                <Check size={13} aria-hidden /> Approve
              </button>
            ) : (
              <button className="btn btn-primary" disabled={!session || !fresh} onClick={() => setPending("apply")}>
                <Send size={13} aria-hidden /> Apply locally
              </button>
            )}
          </div>
          {!session && <p className="decision-hint">Decisions need the local review token. It stays in this tab's memory only.</p>}
        </>
      )}

      {pending && (
        <Modal
          title={COPY[pending].title}
          tone={pending === "reject" ? "danger" : "default"}
          onClose={() => !busy && setPending(null)}
          footer={
            <>
              <button className="btn" disabled={busy} onClick={() => setPending(null)}>
                Cancel
              </button>
              <button className={`btn ${pending === "reject" ? "btn-danger" : "btn-primary"}`} disabled={busy} onClick={() => void run(pending)}>
                {busy && <Spinner />} {COPY[pending].button}
              </button>
            </>
          }
        >
          <p>{COPY[pending].body}</p>
          {pending === "reject" && (
            <label className="field">
              <span>Note (optional)</span>
              <textarea rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
          )}
          {note.trim() && pending === "approve" && <p className="muted">Note: “{note.trim()}”</p>}
          {error && <p className="field-error">{error}</p>}
        </Modal>
      )}
      {unlock && <UnlockDialog onClose={() => setUnlock(false)} />}
    </section>
  );
}
