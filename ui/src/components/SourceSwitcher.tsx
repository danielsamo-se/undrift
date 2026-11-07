import { api } from '../api/client';
import { useResource } from '../lib/hooks';

const KIND_TEXT: Record<string, string> = {
  live: 'Live operator data', isolated: 'Isolated demo copy', fixture: 'Local test fixture', historical: 'Historical evaluation',
};

/** Operational only: which database this UI talks to, and labelled links to the other local environments. */
export function SourceSwitcher() {
  const res = useResource(signal => api.sources(signal), 'sources');
  if (!res.data) return null;
  const { current, sources } = res.data;
  const here = sources.find(s => s.url === window.location.origin);
  return <div className="source-switcher" data-testid="source-switcher">
    <p className="source-label">Data source</p>
    <p className="source-current"><strong>{here?.label ?? current.profile ?? current.database}</strong>
      {here && <span className={`source-kind kind-${here.kind}`}>{KIND_TEXT[here.kind]}</span>}</p>
    <p className="muted small source-db" title={current.database_id}>{current.database}</p>
    {sources.length > 1 && <label className="source-select"><span className="sr-only">Switch environment</span>
      <select aria-label="Switch environment" value={here?.url ?? ''} onChange={e => { if (e.target.value) window.location.href = e.target.value + '/' + window.location.hash; }}>
        {!here && <option value="">Switch environment…</option>}
        {sources.map(s => <option key={s.url} value={s.url}>{s.label} · {KIND_TEXT[s.kind]}</option>)}
      </select></label>}
  </div>;
}
