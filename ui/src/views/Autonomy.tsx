import { useState } from 'react';
import { api } from '../api/client';
import { useReviewerSession } from '../api/session';
import type { AutonomyMode, AutonomyPolicy } from '../api/types';
import { UnlockDialog } from '../components/ReviewerSession';
import { ErrorState, LoadingBlock } from '../components/ui';
import { useResource } from '../lib/hooks';
import { useJury } from '../lib/jury';
import { useLive } from '../lib/live';

export const MODE_LABELS: Record<AutonomyMode, string> = {
  review_required: 'Human review required', bounded_auto: 'Bounded local auto-application',
  pull_request: 'Human-approved GitHub PR', manual_only: 'Manual only',
};

/** Reader-facing reasons for the server's autonomy blockers (codes come from autonomy.decision). */
export const BLOCKER_LABELS: Record<string, string> = {
  review_required: 'channel requires human review',
  pull_request: 'channel publishes only through a human-approved GitHub PR',
  manual_only: 'protected channel: manual editing only',
  invalid_channel_configuration: 'stored channel configuration is invalid',
  proposal_not_pending: 'proposal is no longer pending',
  delegation_not_pinned_or_changed: 'no delegation was pinned when the run started, or it changed since',
  run_not_fully_reconciled: 'agent run did not complete with full reconciliation',
  unknown_findings: 'the run left UNKNOWN findings',
  no_verified_outdated_finding: 'no verified OUTDATED finding backs this correction',
  finding_binding_mismatch: 'correction does not match the recorded finding exactly',
  document_changed: 'document changed since the proposal',
  policy_changed: 'policy changed since the proposal',
  content_guardrail_violation: 'correction fails content guardrails',
  semantic_edit_needs_review: 'edit goes beyond the flagged sentence',
  not_single_sentence_correction: 'not a single-sentence correction',
  policy_sensitive_exception: 'touches statutory rights or existing commitments',
  invalid_evidence: 'evidence is not an exact quote',
  unverified_retrieval: 'source retrieval was not verified',
  unresolved_clarification: 'a clarification question is unanswered',
  concurrent_analysis: 'another analysis of this document is in progress',
  conflicting_verified_evidence: 'another verified finding conflicts',
  run_application_limit: 'run or channel application limit reached',
  document_missing: 'document no longer exists',
};

export function blockerText(code: string): string {
  return BLOCKER_LABELS[code] ?? code.replaceAll('_', ' ');
}

function ChannelPolicy({ policy: p, readonly, onChanged }: { policy: AutonomyPolicy; readonly: boolean; onChanged: () => void }) {
  const session = useReviewerSession();
  // Pin the editing base. A live reload must never authorize an old draft with a new revision.
  const [base, setBase] = useState(p.version);
  const [mode, setMode] = useState(p.effective_mode);
  const [limit, setLimit] = useState(p.max_per_run);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stale = base !== p.version;
  const editable = !readonly && p.schema_available && p.allowed_modes.length > 1;
  async function save() {
    setBusy(true); setError(null);
    try {
      const res = await api.configureAutonomy(p.channel_id, { expected_version: base, mode, max_per_run: limit, note });
      setBase(res.policy.version); setNote(''); onChanged();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <section className="autonomy-channel" aria-label={p.name}>
    <header><h2>{p.name}</h2><strong>{MODE_LABELS[p.effective_mode]}</strong></header>
    <p className="muted small">{p.schema_available ? `Delegation revision ${p.version}${p.persisted ? ` · set by ${p.updated_by}` : ' · conservative default'}` : 'No recorded delegation in this archive; current conservative defaults shown.'}</p>
    {p.effective_mode === 'pull_request' && <p>Exact reviewed patch only. A human creates the PR and merges it in GitHub. No local application or automatic deployment authorization.</p>}
    {p.effective_mode === 'manual_only' && <p>Agent findings remain visible. Undrift cannot generate or apply corrections in this channel.</p>}
    {p.allowed_modes.includes('bounded_auto') && <p>Optional delegation permits one simple, verified return-window sentence correction at a time in the local document store. Exceptions, commitments and unresolved facts require review.</p>}
    {p.allowed_modes.length === 1 && p.effective_mode === 'review_required' && <p>Proposals need human approval and a separate local application. No external channel writes.</p>}
    {p.note && <p className="note">{p.note}</p>}
    {editable && <form onSubmit={e => { e.preventDefault(); void save(); }}>
      <label className="field"><span>Mode for future runs</span><select value={mode} disabled={!session || busy} onChange={e => setMode(e.target.value as AutonomyMode)}>{p.allowed_modes.map(m => <option key={m} value={m}>{MODE_LABELS[m]}</option>)}</select></label>
      <label className="field"><span>Maximum applications per channel per run (1–3)</span><input type="number" min={1} max={3} value={limit} disabled={!session || busy} onChange={e => setLimit(Number(e.target.value))} /></label>
      <label className="field"><span>Delegation reason (required, recorded in audit)</span><textarea maxLength={1000} value={note} disabled={!session || busy} onChange={e => setNote(e.target.value)} /></label>
      <p className="note">Saving delegates future local writes under server safety checks. Existing runs keep their original authorization; any revision change disables their automatic application.</p>
      {stale && <p role="alert">This policy changed while you were editing. <button type="button" className="text-btn" onClick={() => {setBase(p.version);setMode(p.effective_mode);setLimit(p.max_per_run);setNote('');}}>Reload settings</button> before saving.</p>}
      <button className="btn btn-primary" disabled={!session || busy || stale || !note.trim() || !Number.isInteger(limit) || limit < 1 || limit > 3}>Save future-run delegation</button>
      {error && <p role="alert" className="field-error">{error}</p>}
    </form>}
  </section>;
}

export function AutonomyView() {
  const jury = useJury(); const session = useReviewerSession(); const live = useLive();
  const resource = useResource(signal => api.autonomy(signal), live.latestSeq);
  const [unlock, setUnlock] = useState(false);
  if (resource.error && !resource.data) return <ErrorState error={resource.error} onRetry={resource.reload} />;
  if (!resource.data) return <LoadingBlock rows={8} />;
  const data = resource.data; const readonly = jury.readonly || data.historical;
  return <div className="page autonomy-page"><header className="page-head"><div><h1>Channel autonomy</h1><p>Server-enforced delegation. Local corrections and external publishing have separate authorization.</p></div></header>
    {readonly ? <p className="note">Recorded evidence / read-only replay. These settings cannot authorize live actions.</p> : <p className="note">Operational settings for this database only. At most {data.global_run_limit} documents per run can be applied automatically. All channels start conservatively.</p>}
    {!readonly && data.channels.length > 0 && !data.channels[0]!.schema_available && <p className="note note-warn" role="status">Channel autonomy is not enabled for this database, so every channel keeps its conservative default and nothing can be delegated. A reviewer can enable it explicitly with <code>python cli.py enable-autonomy</code>; frozen evaluation and demo databases are never upgraded automatically.</p>}
    {!readonly && !session && <button className="btn" onClick={() => setUnlock(true)}>Unlock reviewer session to configure autonomy</button>}
    {resource.error && <p className="field-error">Settings could not refresh. Reload before changing delegation.</p>}
    <div className="autonomy-grid">{data.channels.map(p => <ChannelPolicy key={p.channel_id} policy={p} readonly={readonly || !!resource.error} onChanged={resource.reload} />)}</div>
    <p className="note">An applied correction creates a document version and an immutable receipt. It does not invent a fresh agent conclusion: the Impact Map requires analysis of the new version.</p>
    {unlock && <UnlockDialog onClose={() => setUnlock(false)} />}
  </div>;
}
