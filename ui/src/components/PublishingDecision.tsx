import { useState } from "react";
import { api } from "../api/client";
import { useReviewerSession } from "../api/session";
import type { ProposalDetail, PublicationPlan } from "../api/types";
import { useResource } from "../lib/hooks";
import { Modal, Spinner } from "./ui";
import { DiffView } from "./DiffView";
import { UnlockDialog } from "./ReviewerSession";
import { useJury } from '../lib/jury';

const stages = ["approved", "pr_created", "merged", "deployed", "verified"] as const;
const labels = ["Approved", "PR created", "Merged", "Deployed", "Verified"];

export function PublishingDecision({ detail }: { detail: ProposalDetail }) {
  const jury=useJury();
  const p = detail.proposal;
  const session = useReviewerSession();
  const state = useResource((signal) => api.publication(p.id, signal), p.status);
  const [plan, setPlan] = useState<PublicationPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unlock, setUnlock] = useState(false);
  const publication = state.data?.publication;
  const fresh = detail.checks.document_unchanged && detail.checks.policy_current;
  async function act(action: "plan" | "publish" | "refresh") {
    setBusy(true); setError(null);
    try {
      if (action === "plan") setPlan((await api.publicationPlan(p.id)).plan);
      else if (action === "publish" && plan) { await api.publish(p.id, plan.digest); setPlan(null); }
      else { await api.refreshPublication(p.id); }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); state.reload(); }
  }
  if (!publication && p.status !== "approved") return null;
  return <section className="decision" aria-label="Publish to storefront">
    <header className="decision-head"><h2>Publish to Shiftly</h2></header>
    <ol className="flow" aria-label="Publishing progress">{stages.map((s, i) => {
      const at = publication ? stages.findIndex(stage => stage === publication.status) : 0;
      return <li key={s} className={i < at ? "is-done" : i === at ? "is-current" : ""} aria-current={i === at ? "step" : undefined}>
        <span className="flow-mark">{i + 1}</span>{labels[i]}
      </li>;
    })}</ol>
    {!state.data?.configured && !state.loading && <p className="note note-warn">Repository publishing needs operator setup. Your approval is saved.</p>}
    {state.error && <p className="field-error">{state.error.message}</p>}
    <p>{jury.readonly?'Historical publishing evidence. Approval, PR creation, merge and deployment verification retain their recorded identities and timestamps.':'Create a GitHub pull request for this exact reviewed correction. You review and merge it on GitHub; the connected storefront deploys afterward.'}</p>
    {publication?.pr && <p><a className="text-btn" href={publication.pr.url} target="_blank" rel="noreferrer">Open pull request #{publication.pr.number} ↗</a></p>}
    {publication?.target.site_url && <p><a href={`${publication.target.site_url}/returns/`} target="_blank" rel="noreferrer">View Shiftly Returns &amp; Refunds ↗</a></p>}
    {publication?.status === "verified" && publication.verified_at && <p className="note">Verified public policy against the reviewed text and merged commit at {new Date(publication.verified_at).toLocaleString()}.</p>}
    {publication?.status === "verified" && publication.verification_evidence && <p className="muted small">
      Evidence from the production website: build <code>{publication.verification_evidence.observed_build_commit.slice(0, 12)}</code>, exact approved policy bytes and visible Returns text.
    </p>}
    {publication?.status === "blocked" && <p className="note note-bad">Publishing is blocked. Inspect the error and GitHub PR before retrying.</p>}
    {publication?.error && <p className="field-error">{publication.error}</p>}
    {error && <p className="field-error" role="alert">{error}</p>}
    {!jury.readonly && <div className="decision-actions">
      {publication?.pr ? (session
        ? <button className="btn" disabled={busy} onClick={() => void act("refresh")}>{busy && <Spinner />} Check merge and deployment</button>
        : <button className="btn" disabled={busy} onClick={() => setUnlock(true)}>Unlock reviewer session to check deployment</button>)
        : <button className="btn btn-primary" disabled={!session || !fresh || busy || !state.data?.configured} onClick={() => void act("plan")}>{busy && <Spinner />} Review repository change</button>}
    </div>}
    {publication?.events.length ? <details><summary>Publishing audit trail</summary><ul className="plain-list">{publication.events.map(e => <li key={e.seq}>{new Date(e.ts).toLocaleString()} · {e.type.replace("publishing.", "").replaceAll("_", " ")}</li>)}</ul></details> : null}
    {plan && <Modal title="Authorize this GitHub change" onClose={() => !busy && setPlan(null)} footer={<>
      <button className="btn" disabled={busy} onClick={() => setPlan(null)}>Cancel</button>
      <button className="btn btn-primary" disabled={busy} onClick={() => void act("publish")}>{busy && <Spinner />} Authorize &amp; create PR</button>
    </>}>
      <p><strong>{plan.target.repository}</strong> · {plan.target.path} · base <code>{plan.base_commit.slice(0, 12)}</code></p>
      <p>This authorization binds the repository, original file and exact correction below. GitHub merge remains a separate human decision.</p>
      <DiffView before={plan.original_content} after={plan.proposed_content} unified={p.diff} baseVersion={p.base_version} documentId={p.document_id} />
      {error && <p className="field-error" role="alert">{error}</p>}
    </Modal>}
    {unlock && <UnlockDialog onClose={() => setUnlock(false)} />}
  </section>;
}
