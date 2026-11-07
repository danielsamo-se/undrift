import { useState } from 'react';
import { ApiError, api } from '../api/client';
import { useReviewerSession } from '../api/session';
import type { GuardCheck, GuardDecision, GuardSource, MemoryRecall } from '../api/types';
import { UnlockDialog } from '../components/ReviewerSession';
import { GuardComparison, realGuardDecisions } from '../components/GuardComparison';
import { ErrorState, LoadingBlock, PolicyVersion, Spinner } from '../components/ui';
import { useImpact } from '../lib/data';
import { formatDateTime, shortId } from '../lib/format';
import { navigate, useResource } from '../lib/hooks';
import { ArchiveHint, useJury } from '../lib/jury';
import { useLive } from '../lib/live';

export const SOURCE_LABEL: Record<GuardSource, string> = {
  cominty_guard: 'Real Cominty Guard execution, verified by the server',
  deterministic_precheck: 'Deterministic precheck (no AI)',
  fail_closed: 'Fail-closed: no verified Guard verdict',
  channel_boundary: 'Manual-only channel boundary',
  test_fixture: 'TEST FIXTURE: not a Cominty result',
};

const DECISION_TEXT: Record<GuardDecision, string> = {
  ALLOW: 'Allow', NEEDS_REVIEW: 'Needs review', BLOCK: 'Block',
};

export function GuardResult({ check }: { check: GuardCheck }) {
  const out = check.agent_output;
  return (
    <section className="guard-result" aria-label="Guard result" data-testid="guard-result">
      <header className="guard-result-head">
        <span className={`guard-decision guard-d-${check.decision.toLowerCase()}`}>{DECISION_TEXT[check.decision]}</span>
        <span className="guard-source">{SOURCE_LABEL[check.decision_source] ?? check.decision_source}</span>
      </header>
      <p className={`guard-release ${check.release_eligible ? 'is-ok' : ''}`}>
        {check.release_eligible
          ? 'Release-eligible: Guard cleared this exact text. Nothing has been sent or published; releasing it is a separate action.'
          : 'Not release-eligible. Nothing has been sent or published.'}
      </p>
      <ul className="guard-reasons">{check.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
      <dl className="facts guard-bindings">
        <dt>Check</dt><dd><code>{check.id}</code></dd>
        <dt>Candidate</dt><dd><code title={check.candidate_sha256}>sha256 {shortId(check.candidate_sha256, 12)}</code> · {check.candidate_length} chars</dd>
        <dt>Policy</dt><dd><PolicyVersion version={check.policy_version} /> <code>{check.policy_id}</code></dd>
        <dt>Channel</dt><dd><code>{check.channel_id}</code></dd>
        <dt>Execution</dt><dd>{check.execution.mode} · {check.execution.status}{check.execution.message_id && <> · message <code>{shortId(check.execution.message_id, 10)}</code></>}</dd>
        {check.execution.mode === 'cominty' && <><dt>Cominty run</dt><dd>
          {check.execution.models?.length ? check.execution.models.join(', ') : 'model not reported'}
          {check.execution.duration_s != null && <> · {check.execution.duration_s.toFixed(1)} s</>}
          {' · '}{check.execution.cost?.total != null ? <>reported cost {check.execution.cost.total}</> : 'cost not reported'}
          {' · '}{check.execution.tool_calls?.length ? <strong>tool calls: {check.execution.tool_calls.join(', ')}</strong> : 'no tool calls'}
        </dd></>}
        <dt>Contracts</dt><dd><code>{check.input_contract}</code> → <code>{check.output_contract}</code></dd>
        <dt>Time</dt><dd>{formatDateTime(check.created_at)}</dd>
      </dl>
      {check.evidence.length > 0 && <div className="guard-block"><h3>Evidence supplied</h3><ul className="plain-list">
        {check.evidence.map(e => <li key={e.ref}><code>{e.ref}</code> {e.document_id} · v{e.version} · {e.channel_id}</li>)}</ul></div>}
      {check.facts.length > 0 && <div className="guard-block"><h3>Reviewer facts</h3><ul className="plain-list">
        {check.facts.map(f => <li key={f.ref}><code>{f.ref}</code> {f.statement_redacted}</li>)}</ul></div>}
      <div className="guard-block"><h3>Deterministic precheck · {check.precheck.verdict}</h3>
        {check.precheck.findings.length ? <ul className="plain-list">{check.precheck.findings.map((f, i) =>
          <li key={i}><strong>{f.verdict}</strong> {f.rule.replaceAll('_', ' ')}: {f.reason}</li>)}</ul>
          : <p className="muted small">No rule escalated. A clear precheck never allows on its own.</p>}
      </div>
      <div className="guard-block"><h3>Guard agent output</h3>
        {out ? <>
          <p className="small">Agent verdict: <strong>{out.verdict ?? 'invalid'}</strong>{out.uncertainty && <> · uncertainty: {out.uncertainty}</>}</p>
          <ul className="plain-list">{out.findings.map((f, i) => <li key={i}>
            <span className="guard-claim">“{f.claim}”</span> <strong>{f.assessment}</strong> [{f.evidence_refs.join(', ')}] {f.reason}</li>)}</ul>
        </> : <p className="muted small">No agent output{check.execution.error ? `: ${check.execution.error}` : '.'}</p>}
        {check.verification.problems.length > 0 && <p className="note note-bad">Rejected by server verification: {check.verification.problems.join('; ')}</p>}
        {check.verification.stale.length > 0 && <p className="note note-warn">{check.verification.stale.join('; ')}</p>}
      </div>
    </section>
  );
}

export const MEMORY_STATUS: Record<string, string> = {
  authoritative: 'Current policy', historical: 'Historical (older policy)', unverified: 'Unverified (ignored)',
};

export function MemoryRecallPanel({ recall, check, canSave, onSaved }: {
  recall: MemoryRecall | null; check: GuardCheck; canSave: boolean; onSaved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const eligible = check.decision_source === 'cominty_guard' && check.verification.ok && check.decision !== 'NEEDS_REVIEW';
  async function save() {
    setBusy(true); setMsg(null);
    try { const r = await api.rememberCheck(check.id); setMsg(`Saved to Cominty Memory: ${r.file.path}`); onSaved(); }
    catch (e) { setMsg(e instanceof ApiError ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <div className="guard-block memory-recall" data-testid="memory-recall"><h3>Cominty Memory</h3>
    {recall === null ? <p className="muted small">No memory lookup recorded for this check.</p>
      : recall.status === 'error' ? <p className="note note-warn">{recall.error}</p>
      : recall.matches.length === 0 ? <p className="muted small">Looked up namespace <code>{recall.namespace}</code>: no remembered interpretation of this exact text.</p>
      : <ul className="plain-list">{recall.matches.map(m => <li key={m.path}>
          <span className={`mem-status mem-${m.status}`}>{MEMORY_STATUS[m.status]}</span>{' '}
          Remembered verdict <strong>{m.verdict}</strong> under policy v{m.policy.version}, from check{' '}
          <button type="button" className="link" onClick={() => m.source_check_id && navigate('guard', m.source_check_id)}><code>{m.source_check_id}</code></button>
          {' '}· <code>{m.path}</code></li>)}</ul>}
    {recall && recall.matches.length > 0 && <p className="muted small">Memory is context only. The decision above was computed independently; memory can never release text.</p>}
    {eligible && canSave && <button type="button" className="btn" disabled={busy} onClick={() => void save()}>{busy && <Spinner />} Save verified interpretation to Cominty Memory</button>}
    {msg && <p className="small" role="status">{msg}</p>}
  </div>;
}

export function GuardView({ selectedId = null }: { selectedId?: string | null }) {
  const jury = useJury(); const session = useReviewerSession(); const live = useLive();
  const overview = useResource(signal => api.guard(signal), live.latestSeq);
  const effectiveId = selectedId ?? (jury.readonly ? overview.data?.checks.find(c => c.decision_source === 'cominty_guard')?.id ?? null : null);
  const impact = useImpact();
  const detail = useResource(signal => effectiveId ? api.guardDetail(effectiveId, signal) : Promise.resolve(null),
    `${effectiveId}:${live.latestSeq}`);
  const [candidate, setCandidate] = useState('');
  const [channel, setChannel] = useState('support-snippets');
  const [evidence, setEvidence] = useState<string[]>([]);
  const [facts, setFacts] = useState('');
  const [confirmPaid, setConfirmPaid] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fresh, setFresh] = useState<GuardCheck | null>(null);
  const [unlock, setUnlock] = useState(false);
  if (jury.readonly && !overview.data && overview.error instanceof ApiError && overview.error.status === 404) return <div className="page guard-page">
    <header className="ph"><div><h1>Guard</h1><p className="ph-sub">Fail-closed validation of outgoing policy and support responses.</p></div></header>
    <ArchiveHint replay="guard" text="This archive has no Guard checks. The real Cominty Guard decisions (ALLOW and BLOCK) are in the Guard archive." /></div>;
  if (overview.error && !overview.data) return <ErrorState error={overview.error} onRetry={overview.reload} />;
  if (!overview.data) return <LoadingBlock rows={8} />;
  const o = overview.data; const ex = o.executor;
  const readonly = jury.readonly || !o.journal_configured;
  const docs = impact.data?.documents ?? [];
  const channels = impact.data?.channels ?? [];
  const selected = (fresh && fresh.id === effectiveId ? fresh : o.checks.find(c => c.id === effectiveId)) ?? null;
  const recall = detail.data && detail.data.check.id === effectiveId ? detail.data.memory_recall : null;
  const realCount = realGuardDecisions(o.checks).length;

  async function verify() {
    setBusy(true); setError(null);
    try {
      const res = await api.guardCheck({ candidate, channel_id: channel, evidence_document_ids: evidence,
        facts: facts.split('\n').map(f => f.trim()).filter(Boolean), confirm_paid: confirmPaid });
      setFresh(res.check); setConfirmPaid(false); navigate('guard', res.check.id); overview.reload();
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  const recordedList = <section className="section"><h2 className="guard-h2">Recorded checks</h2>
    {o.checks.length ? <ul className="plain-list">{o.checks.map(c => <li key={c.id}>
      <button className={`row-link${c.id === effectiveId ? ' is-current' : ''}`} onClick={() => navigate('guard', c.id)}>
        <span className={`guard-decision guard-d-${c.decision.toLowerCase()} is-small`}>{DECISION_TEXT[c.decision]}</span>
        <span className="row-link-title">{c.candidate_redacted}</span>
        <span className="muted small guard-src-short">{SOURCE_LABEL[c.decision_source]?.split(':')[0]}</span>
      </button></li>)}</ul> : <p className="muted">No Guard checks recorded for this database.</p>}
  </section>;

  return <div className="page guard-page">
    <header className="ph"><div><h1>Guard</h1>
      <p className="ph-sub">Fail-closed validation of outgoing policy and support responses. Guard decides only whether a text may be released; it never sends or publishes.</p></div></header>
    {jury.readonly ? <p className="note guard-mode" data-testid="guard-mode">Recorded evidence. {realCount === 1 ? 'The separate Cominty Guard agent ran once for real (verified by the server).' : `The separate Cominty Guard agent ran ${realCount} times for real, each verdict verified by the server.`} The other checks were decided by the deterministic precheck or failed closed without an agent; they are labelled as such. Nothing runs from this site.</p> :
    <p className={`note ${ex.mode === 'cominty' ? '' : 'note-warn'} guard-mode`} data-testid="guard-mode">
      {ex.mode === 'cominty'
        ? 'Real Cominty Guard agent configured. Each check is a paid Cominty execution and needs explicit confirmation.'
        : 'Cominty Guard agent not activated. Checks run the deterministic precheck and then fail closed: no response can become release-eligible.'}
    </p>}
    {!jury.readonly && !o.journal_configured && <p className="note note-warn">This service has no Guard audit journal configured, so checks cannot be run here.</p>}
    <GuardComparison checks={o.checks} policy={impact.data?.policy ?? null} />
    <div className="guard-layout">
      {jury.readonly ? recordedList : <form className="guard-form section" onSubmit={e => { e.preventDefault(); void verify(); }}>
        <label className="field"><span>Draft customer-facing response</span>
          <textarea rows={5} maxLength={2000} value={candidate} disabled={readonly || busy} onChange={e => setCandidate(e.target.value)}
            placeholder="e.g. Orders purchased on or after November 4, 2026 are eligible for the standard 30-day voluntary return window." /></label>
        <label className="field"><span>Target channel</span>
          <select value={channel} disabled={readonly || busy} onChange={e => setChannel(e.target.value)}>
            {channels.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <fieldset className="guard-evidence"><legend>Evidence documents (optional, up to 8)</legend>
          {docs.map(d => <label key={d.id} className="guard-check-row"><input type="checkbox" disabled={readonly || busy || (!evidence.includes(d.id) && evidence.length >= 8)}
            checked={evidence.includes(d.id)} onChange={e => setEvidence(e.target.checked ? [...evidence, d.id] : evidence.filter(x => x !== d.id))} />
            <code>{d.id}</code> {d.title} <span className="muted">v{d.version}</span></label>)}
        </fieldset>
        <label className="field"><span>Verified facts (optional, one per line; avoid personal data)</span>
          <textarea rows={3} value={facts} disabled={readonly || busy} onChange={e => setFacts(e.target.value)}
            placeholder="e.g. Order #1099 has no verified purchase date." /></label>
        {ex.paid && <label className="guard-check-row"><input type="checkbox" checked={confirmPaid} disabled={readonly || busy} onChange={e => setConfirmPaid(e.target.checked)} /> I confirm one paid Cominty Guard execution for this check.</label>}
        {!readonly && !session && <button type="button" className="btn" onClick={() => setUnlock(true)}>Unlock reviewer session</button>}
        <button className="btn btn-primary" disabled={readonly || busy || !session || !candidate.trim() || (ex.paid && !confirmPaid)}>
          {busy && <Spinner />} Verify with Guard</button>
        {error && <p role="alert" className="field-error">{error}</p>}
      </form>}
      <div className="guard-side">
        {selected ? <><GuardResult check={selected} />
          <MemoryRecallPanel recall={recall} check={selected} canSave={!readonly && !!session} onSaved={detail.reload} /></>
          : <p className="muted">Run a check or select a recorded one.</p>}
        {!jury.readonly && recordedList}
      </div>
    </div>
    {unlock && <UnlockDialog onClose={() => setUnlock(false)} />}
  </div>;
}
