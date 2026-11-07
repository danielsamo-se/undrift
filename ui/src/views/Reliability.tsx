import { useState } from 'react';
import { api } from '../api/client';
import type { ReliabilityReport } from '../api/types';
import { ErrorState, LoadingBlock } from '../components/ui';
import { useResource, useNow } from '../lib/hooks';

const METRICS: [string, string, boolean][] = [
  ['accuracy','Overall accuracy',true], ['outdated_recall','Outdated recall',true],
  ['outdated_precision','Outdated precision',true], ['false_positives','False positives',false],
  ['paraphrase_recall','Paraphrase detection',true], ['unknown_recall','UNKNOWN recall',true],
  ['unknown_precision','UNKNOWN precision',true], ['protected_classification_accuracy','Legal / CRM classification',true],
  ['native_clarification_recall','Native clarification recall',true],
  ['native_clarification_precision','Native clarification precision',true], ['retrieval_coverage','Retrieval coverage',true],
  ['duration_s','Duration (seconds)',false], ['native_question_count','Persisted native questions',false],
  ['reserved_parent_steps','Reserved parent allowance',false], ['observed_llm_rounds','Observed LLM rounds',false],
  ['mcp_error_count','MCP errors',false],
];
function value(number: number | null | undefined, percent = true) {
  return number == null ? 'N/A' : percent ? `${(number*100).toFixed(1)}%` : number.toFixed(1);
}

export function ReliabilityView() {
  const [series, setSeries] = useState<string>();
  const tick = useNow(5000);
  const { data, error, reload } = useResource(signal => api.reliability(signal, series), `${series ?? 'active'}:${Math.floor(tick/5000)}`);
  if (error && !data) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <LoadingBlock rows={8} />;
  return <>
    <label className="field"><span>Evaluation series</span>
      <select value={series ?? data.active_series_id ?? data.series_id ?? 'reliability-v1'} onChange={e => setSeries(e.target.value)}>
        {(data.available_series ?? [data.series_id ?? 'reliability-v1']).map(id =>
          <option key={id} value={id}>{id}{id === data.active_series_id ? ' (active)' : ' (archived)'}</option>)}
      </select>
    </label>
    {series && data.series_id !== series ? <LoadingBlock rows={8} /> : <Reliability report={data} />}
    {error && <p role="alert">Series unavailable: {error.message}</p>}
  </>;
}

export function Reliability({ report }: { report: ReliabilityReport }) {
  const a = report.aggregate;
  return <div className="page reliability-page">
    <header className="ph"><div><h1>Reliability</h1><p className="ph-sub">Frozen independent conversations · {report.state.replaceAll('_',' ')}</p></div></header>
    <p><strong>{report.series_id ?? 'reliability-v1'}</strong> · {report.archived ? 'Archived study — preserved results' : 'Active study'}.
      Each series has separate results and denominators; studies are never combined.</p>
    <div className="reliability-summary">
      <div><strong>{a.recorded_conversations} / {a.planned_conversations}</strong><span>Conversations recorded</span></div>
      <div><strong>{a.reconciled_conversations}</strong><span>Final reconciliations</span></div>
      <div><strong>{report.attempted_paid_messages ?? a.message_count} / 10</strong><span>Attempted paid messages</span></div>
      <div><strong>{a.reported_total_cost}</strong><span>Reported cost · stop before next message at 5</span></div>
    </div>
    <p className="muted small">Scores use reconciled labels. Failed or unfinalized conversations retain missing-label penalties.
      Incomplete runs remain visible; honest UNKNOWN findings can coexist with verified independent corrections.
      N/A precision means no positive predictions; its defined sample count is shown.</p>
    {report.state === 'stopped' && <p role="alert" className="field-error">Evaluation stopped: {report.diagnosis?.summary || report.stop_reason || 'A safety invariant failed; inspect preserved execution evidence'}. No automatic retry.</p>}
    {a.not_started > 0 && <p role="status">{a.not_started} planned conversations {report.state === 'executing' ? 'have no recorded outcome yet' : 'have not started'}.</p>}
    {report.diagnosis && <div className="reliability-detail"><strong>Provider emission and product persistence</strong>
      <p>Cominty emitted {report.diagnosis.provider_native_question_count} genuine native questions; the product persisted {report.diagnosis.persisted_question_count}.
        Provider emission recall: {value(report.diagnosis.provider_native_emission_recall)}. Frozen persistence scores remain unchanged.</p>
      <p className="muted small">Failed rule: {report.diagnosis.failed_rule}. Catalog event {report.diagnosis.catalog_event_seq}; first document retrieval event {report.diagnosis.first_document_retrieval_seq}.</p>
    </div>}
    <section className="reliability-table"><table><caption>Aggregate metrics across all recorded conversations</caption>
      <thead><tr><th>Metric</th><th>Minimum</th><th>Mean</th><th>Maximum</th><th>Defined samples</th></tr></thead>
      <tbody>{METRICS.map(([key,label,percent]) => {
        const s = a.statistics[key]; return <tr key={key}><th scope="row">{label}</th>
          <td>{value(s?.minimum,percent)}</td><td>{value(s?.mean,percent)}</td><td>{value(s?.maximum,percent)}</td>
          <td>{s?.defined_count ?? 0} / {a.recorded_conversations}</td></tr>;
      })}</tbody></table></section>
    <section className="reliability-table"><table><caption>Per-conversation results</caption>
      <thead><tr><th>Run</th><th>State / evidence</th><th>Accuracy</th><th>Drift recall / precision</th><th>UNKNOWN recall</th><th>Questions / answered</th><th>Cost / time</th></tr></thead>
      <tbody>{report.results.map(r => <tr key={r.root_run_id}><th scope="row">#{r.slot}<br/><code>{r.root_run_id}</code></th>
        <td>{r.status}<br/><span>{r.reconciled ? 'Reconciled' : 'Unreconciled'}</span><br/><strong>{r.evidence_kind === 'real_cominty' ? 'Real Cominty execution' : 'Test / local evidence'}</strong></td>
        <td>{value(r.metrics.accuracy)}</td><td>{value(r.metrics.outdated_recall)} / {value(r.metrics.outdated_precision)}</td>
        <td>{value(r.metrics.unknown_recall)}</td><td>{r.native_question_count} / {r.answered_count}<br/>{r.message_count} messages</td>
        <td>{r.reported_cost ?? 'Unavailable'}<br/>{r.duration_s.toFixed(1)} s</td></tr>)}</tbody></table></section>
    {report.results.map(r => <details className="reliability-detail" key={r.root_run_id}><summary>Conversation {r.slot}: execution and audit evidence</summary>
      <dl><dt>Thread</dt><dd><code>{r.thread_id}</code></dd><dt>Database identity</dt><dd><code>{r.database_id}</code></dd>
        <dt>Execution phases</dt><dd>{r.phase_run_ids.join(' → ')}</dd><dt>Assistant messages</dt><dd>{r.message_ids.join(', ')}</dd>
        <dt>Protected automation</dt><dd>{r.protected_automation_safe ? 'Safe: no Legal/CRM corrections or proposals' : 'Safety violation'}</dd>
        <dt>Native evidence</dt><dd>{r.native_question_integrity ? 'Prompts/options match terminal native evidence' : 'Integrity failure'}</dd>
        <dt>Retrieval</dt><dd>{r.final_retrieval_complete ? 'Final phase complete' : 'Incomplete final retrieval'}</dd>
        <dt>Parent allowance / observed LLM rounds</dt><dd>{r.reserved_parent_steps} / {r.observed_llm_rounds} (stream observation, not a certified consumed-step count)</dd>
        <dt>MCP calls / errors</dt><dd>{Object.entries(r.mcp_calls).map(([k,n]) => `${k}: ${n}`).join(' · ')} / {r.mcp_error_count}</dd>
        <dt>Search queries</dt><dd>{r.search_queries.map((q,i) => <code key={i}>{q || '(catalog)'}; </code>)}</dd>
        <dt>Pending proposals</dt><dd>{r.proposal_count}</dd><dt>Misclassified / missing</dt><dd>{r.misclassified_document_ids.join(', ') || 'None'}</dd></dl>
      {r.observed_unreconciled_metrics && <p>Provisional observations exist and are excluded from primary scores.
        Observed outdated recall {value(r.observed_unreconciled_metrics.outdated_recall)}, precision {value(r.observed_unreconciled_metrics.outdated_precision)},
        accuracy {value(r.observed_unreconciled_metrics.accuracy)}.</p>}
      {r.errors.map((e,i) => <p className="muted small" key={i}>{e}</p>)}
    </details>)}
    <p className="muted small">Protocol SHA-256: <code>{report.protocol_sha256 ?? 'Not frozen yet'}</code><br/>
      Reported cost units have no currency label in the API. The cost gate applies between messages.
      {report.protected_historical_files_unchanged === true && ' Historical evidence preservation checks passed.'}</p>
  </div>;
}
