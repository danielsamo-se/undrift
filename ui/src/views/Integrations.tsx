import { useState } from 'react';
import { ApiError, api } from '../api/client';
import { useReviewerSession } from '../api/session';
import type { MemorySnapshot, NotificationsSnapshot } from '../api/types';
import { UnlockDialog } from '../components/ReviewerSession';
import { ErrorState, LoadingBlock, Spinner } from '../components/ui';
import { formatDateTime, shortId } from '../lib/format';
import { navigate, useResource } from '../lib/hooks';
import { useJury } from '../lib/jury';
import { useLive } from '../lib/live';
import { MEMORY_STATUS } from './Guard';

export function MemorySection({ data, canWrite, onChanged }: { data: MemorySnapshot; canWrite: boolean; onChanged: () => void }) {
  const [fact, setFact] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function save() {
    setBusy(true); setMsg(null);
    try { const r = await api.rememberFact(fact); setMsg(`Saved to Cominty Memory: ${r.file.path}`); setFact(''); onChanged(); }
    catch (e) { setMsg(e instanceof ApiError ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <section aria-label="Cominty Memory" data-testid="memory-section">
    <h2 className="guard-h2">Cominty Memory</h2>
    <p className={`note ${data.live ? '' : 'note-warn'}`}>
      {data.live
        ? <>Live data from the native Cominty Memory API, namespace <code>{data.namespace}</code>. Only server-verified Guard verdicts and reviewer-confirmed facts are stored, each bound to one policy version.</>
        : 'Cominty Memory is not enabled for this service. Nothing below comes from Cominty.'}
    </p>
    {data.error && <p className="note note-bad">{data.error}</p>}
    {data.live && (data.files.length ? data.files.map(f => <article key={f.path} className="integration-card">
      <h3><span className={`mem-status mem-${f.status}`}>{MEMORY_STATUS[f.status]}</span>
        <code>{f.path}</code><span className="origin-tag is-cominty">Cominty memory · version {shortId(f.version ?? '', 10)}</span></h3>
      {f.record?.kind === 'verified_interpretation' && <>
        <p className="small">Verified Guard verdict <strong>{f.record.verdict}</strong> for policy <code>{f.record.policy.id}</code> v{f.record.policy.version}:</p>
        <ul className="plain-list">{f.record.claims?.map((c, i) => <li key={i} className="small">“{c.claim}” <strong>{c.assessment}</strong> [{c.evidence_refs.join(', ')}] {c.reason}</li>)}</ul>
        <p className="muted small">From Guard check{' '}
          <button type="button" className="link" onClick={() => f.record?.source.guard_check_id && navigate('guard', f.record.source.guard_check_id)}><code>{f.record.source.guard_check_id}</code></button>
          {' '}· Cominty message <code>{shortId(f.record.source.message_id ?? '', 10)}</code> · saved by {f.record.saved_by} · {formatDateTime(f.record.saved_at)}</p>
      </>}
      {f.record?.kind === 'reviewer_fact' && <p className="small">Reviewer-confirmed fact (policy v{f.record.policy.version}): {f.record.statement} <span className="muted">· {f.record.saved_by} · {formatDateTime(f.record.saved_at)}</span></p>}
      {!f.record && <p className="muted small">Not an Undrift record. Ignored as unverified.</p>}
      {f.status === 'historical' && <p className="note note-warn">Saved for an older policy state. Shown as history only, never as current policy.</p>}
      {f.changed_since_write && <p className="note note-bad">Content changed in Cominty since Undrift wrote it.</p>}
    </article>) : <p className="muted">No memory files in this namespace yet.</p>)}
    {data.live && canWrite && <div className="integration-card">
      <label className="field"><span>Remember a reviewer-confirmed fact (current policy version; no personal data)</span>
        <textarea rows={2} maxLength={300} value={fact} disabled={busy} onChange={e => setFact(e.target.value)}
          placeholder="e.g. Order #1099 was placed before November 4, 2026." /></label>
      <button type="button" className="btn" disabled={busy || !fact.trim()} onClick={() => void save()}>{busy && <Spinner />} Save fact to Cominty Memory</button>
    </div>}
    {msg && <p className="small" role="status">{msg}</p>}
    {data.writes.length > 0 && <div className="guard-block"><h3>Audit: memory writes by this service</h3><ul className="plain-list">
      {data.writes.map(wr => <li key={wr.id} className="small"><code>{wr.path}</code> · {wr.kind} · v{wr.policy_version} · Cominty version <code>{shortId(wr.cominty_version, 10)}</code> · {wr.actor} · {formatDateTime(wr.created_at)}</li>)}
    </ul></div>}
  </section>;
}

const PHASE_TEXT: Record<string, string> = {
  delivered: 'Delivered (confirmed by Cominty)', unconfirmed: 'Unconfirmed: inspect Slack', failed: 'Failed: not sent', started: 'In progress',
};

export function SlackSection({ data, canWrite, onChanged }: { data: NotificationsSnapshot; canWrite: boolean; onChanged: () => void }) {
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const c = data.connector;
  const ready = data.mode === 'cominty' && !!data.channel;
  async function send(key: string) {
    setBusy(key); setMsg(null);
    try { const r = await api.notify(key, confirm === key); setMsg(PHASE_TEXT[r.delivery.phase] ?? r.delivery.phase); setConfirm(null); onChanged(); }
    catch (e) { setMsg(e instanceof ApiError ? e.message : String(e)); }
    finally { setBusy(null); }
  }
  return <section aria-label="Slack notifications" data-testid="slack-section">
    <h2 className="guard-h2">Slack notifications</h2>
    <div className="integration-card">
      <h3>Cominty connector <code>{data.connection}</code>
        {c && !c.missing && <span className="origin-tag is-cominty">status from Cominty</span>}</h3>
      {data.connector_error ? <p className="note note-warn">{data.connector_error}</p>
        : !c ? <p className="muted small">Connector status unavailable: Cominty platform reads are not enabled for this service.</p>
        : c.missing ? <p className="note note-warn">This connector is not available on the Cominty account.</p>
        : <p className="small">{c.title}: {c.is_enabled ? 'enabled' : 'not enabled'}{c.requires_oauth_connect ? ' · requires OAuth connection' : ''}{c.connection_status ? ` · ${c.connection_status}` : ''}</p>}
      <p className="small">Notifier agent: {data.agent_configured ? 'configured' : 'not created'} · authorized channel: {data.channel ? <code>{data.channel}</code> : 'not set'} · delivery: {ready ? 'live (paid per notification)' : 'disabled'}</p>
      <p className="muted small">Slack is a notification destination only. Approval, rejection and publishing happen in Undrift.</p>
    </div>
    {!ready && <p className="note note-warn">Slack delivery is prepared but not active: it needs the Cominty Slack connection (OAuth), a Notifier agent and an authorized channel. Nothing has been sent.</p>}
    <div className="guard-block"><h3>Ready to notify ({data.candidates.length})</h3>
      {data.candidates.length ? data.candidates.map(n => <article key={n.key} className="integration-card">
        <pre>{n.text}</pre>
        <p className="small muted">{n.kind.replace('_', ' ')} · {n.last_phase ? PHASE_TEXT[n.last_phase] : 'not sent'}</p>
        {ready && canWrite && !n.last_phase && <>
          {data.paid && <label className="guard-check-row"><input type="checkbox" checked={confirm === n.key} onChange={e => setConfirm(e.target.checked ? n.key : null)} /> I confirm one paid Cominty Notifier execution for this message.</label>}
          <button type="button" className="btn" disabled={busy !== null || (data.paid && confirm !== n.key)} onClick={() => void send(n.key)}>{busy === n.key && <Spinner />} Send to Slack</button>
        </>}
      </article>) : <p className="muted">Nothing awaiting review.</p>}
    </div>
    {msg && <p className="small" role="status">{msg}</p>}
    <div className="guard-block"><h3>Delivery records</h3>
      {data.deliveries.length ? <ul className="plain-list">{data.deliveries.map(d => <li key={d.id} className="small">
        <strong>{PHASE_TEXT[d.phase] ?? d.phase}</strong> · <code>{d.channel}</code> · {d.kind.replace('_', ' ')} <code>{shortId(d.subject_id, 18)}</code>
        {d.execution.slack?.message_ts && <> · Slack ts <code>{d.execution.slack.message_ts}</code></>}
        {d.execution.message_id && <> · Cominty message <code>{shortId(d.execution.message_id, 10)}</code></>}
        {d.error && <> · {d.error}</>} · {formatDateTime(d.created_at)}</li>)}</ul>
        : <p className="muted small">No notification has been attempted. No delivery is claimed.</p>}
    </div>
  </section>;
}

export function IntegrationsView() {
  const jury = useJury(); const session = useReviewerSession(); const live = useLive();
  const memory = useResource(signal => api.memory(signal), live.latestSeq);
  const notes = useResource(signal => api.notifications(signal), live.latestSeq);
  const [unlock, setUnlock] = useState(false);
  if (jury.readonly) return <div className="page"><header className="ph"><div><h1>Memory &amp; Slack</h1>
    <p className="ph-sub">Not part of the historical jury replay.</p></div></header></div>;
  const err = (memory.error && !memory.data) ? memory : (notes.error && !notes.data) ? notes : null;
  if (err?.error) return <ErrorState error={err.error} onRetry={err.reload} />;
  if (!memory.data || !notes.data) return <LoadingBlock rows={8} />;
  const canWrite = !!session && memory.data.journal_configured;
  return <div className="page integrations-page">
    <header className="ph"><div><h1>Memory &amp; Slack</h1>
      <p className="ph-sub">Native Cominty platform integrations. Memory keeps verified policy interpretations per policy version; Slack receives review notifications.</p></div>
      {!session && <button type="button" className="btn" onClick={() => setUnlock(true)}>Unlock reviewer session</button>}</header>
    <div className="integrations-grid">
      <MemorySection data={memory.data} canWrite={canWrite} onChanged={memory.reload} />
      <SlackSection data={notes.data} canWrite={!!session} onChanged={notes.reload} />
    </div>
    {unlock && <UnlockDialog onClose={() => setUnlock(false)} />}
  </div>;
}
