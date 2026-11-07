import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { navigate, type View } from './hooks';
import { PUBLIC_JURY, setReplay } from './jury-mode';

interface Replay { id: string; name: string; description: string; database_id: string; source_sha256: string; run_ids: string[]; document_count: number; kind?: 'recorded' | 'fixture' }
interface Catalog { mode: string; snapshot_id: string; captured_at: string; default_replay: string; replays: Replay[] }
const Context = createContext<{readonly:boolean; catalog:Catalog|null; selected:Replay|null; select:(id:string)=>void}>({readonly:false,catalog:null,selected:null,select:()=>{}});
export function useJury() { return useContext(Context); }
export function JuryProvider({children}:{children:ReactNode}) {
  const [catalog,setCatalog]=useState<Catalog|null>(null);
  const [selected,setSelected]=useState('clarification');
  const [error,setError]=useState('');
  useEffect(()=>{
    if(!PUBLIC_JURY)return;
    const ctrl=new AbortController();
    fetch('/api/demo',{signal:ctrl.signal,credentials:'omit'}).then(async response=>{
      if(!response.ok)throw Error('Jury evidence catalog unavailable');
      const data=await response.json() as Catalog;
      if(ctrl.signal.aborted)return;
      const requested=new URL(window.location.href).searchParams.get('replay');
      const initial=data.replays.some(r=>r.id===requested)?requested!:data.default_replay;
      setCatalog(data);setReplay(initial);setSelected(initial);
      if(!window.location.hash)navigate('tour');
    }).catch(err=>{if(!ctrl.signal.aborted)setError((err as Error).message)});
    return()=>ctrl.abort();
  },[]);
  if(!PUBLIC_JURY)return <>{children}</>;
  if(error)return <p role="alert" className="jury-loading">{error}</p>;
  if(!catalog)return <p role="status" className="jury-loading">Loading genuine recorded evidence…</p>;
  const select=(id:string)=>{if(catalog.replays.some(r=>r.id===id)){
    setReplay(id);setSelected(id);const url=new URL(window.location.href);url.searchParams.set('replay',id);window.history.replaceState(null,'',url);
  }};
  return <Context.Provider value={{readonly:true,catalog,selected:catalog.replays.find(r=>r.id===selected)??null,select}}>
    <div key={selected}>{children}</div>
  </Context.Provider>;
}

export type EvidenceKind = 'cominty' | 'human' | 'production' | 'rule' | 'fixture' | 'study';
const EVIDENCE: Record<EvidenceKind, string> = {
  cominty: 'Real Cominty execution · recorded',
  human: 'Human decision · recorded',
  production: 'GitHub + Vercel production · verified',
  rule: 'Server-enforced rule · no AI',
  fixture: 'Local test fixture · not a Cominty run',
  study: 'Frozen evaluation · 5 real trials',
};
export function EvidenceTag({ kind }: { kind: EvidenceKind }) {
  return <span className={`evidence-tag ev-${kind}`}>{EVIDENCE[kind]}</span>;
}

export function JuryBanner() {
  const jury=useJury();if(!jury.readonly)return null;
  const fixture=jury.selected?.kind==='fixture';
  return <section className={`jury-banner${fixture?' is-fixture':''}`} aria-label="Historical replay">
    <div><strong>{fixture?'Local test fixture · Read-only':'Recorded evidence · Read-only jury demo'}</strong>
      <span>{fixture?'This archive was produced by a scripted local fixture. It demonstrates server rules, not a Cominty agent run.'
        :'Genuine recorded executions. No agents run from this site. Approval and publishing are disabled.'}</span></div>
    <label>Evidence archive<select aria-label="Evidence archive" value={jury.selected?.id} onChange={e=>{jury.select(e.target.value);navigate('overview');}}>
      {jury.catalog?.replays.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}
    </select></label>
    <a href="#/tour">Jury guide →</a>
  </section>;
}

interface Step { n: string; title: string; text: string; replay: string; view: View; param?: string; cta: string; kinds: EvidenceKind[] }
const STEPS: Step[] = [
  { n: '01', title: 'One policy changes', text: 'Shiftly shortens voluntary returns from 60 to 30 days for purchases from 4 November 2026. The statutory 14-day right and earlier promises stay valid.',
    replay: 'discovery', view: 'impact', cta: 'See the policy and its seven channels', kinds: ['human'] },
  { n: '02', title: 'Undrift finds the drift', text: 'The Cominty Impact Agent searched 24 documents across seven channels through Undrift’s MCP server and recorded evidence-backed findings, original mistakes included.',
    replay: 'discovery', view: 'impact', cta: 'Explore the Impact Map', kinds: ['cominty'] },
  { n: '03', title: 'It asks instead of guessing', text: 'When facts were missing, the agent asked five native Cominty questions. A reviewer answered and the same conversation continued.',
    replay: 'clarification', view: 'impact', cta: 'Inspect the clarification', kinds: ['cominty', 'human'] },
  { n: '04', title: 'Exact fixes, per-channel autonomy', text: 'Each correction is an exact patch with a diff. Every channel has its own server-enforced limit: legal and CRM stay manual; Help Center may auto-apply at most one change per run.',
    replay: 'clarification', view: 'review', cta: 'Open the review queue', kinds: ['cominty', 'rule'] },
  { n: '05', title: 'A human-approved PR', text: 'An approved Website patch became real GitHub pull request #1 against the Shiftly storefront. Only a human could merge it.',
    replay: 'publishing', view: 'review', param: 'prop-04ca726838fc', cta: 'Follow the publishing audit', kinds: ['human', 'production'] },
  { n: '06', title: 'Merged, deployed, verified', text: 'After the merge, Vercel deployed commit c391b36 and Undrift verified that production serves exactly the approved policy text.',
    replay: 'publishing', view: 'review', param: 'prop-04ca726838fc', cta: 'See the production verification', kinds: ['production'] },
  { n: '07', title: 'Guard checks outgoing replies', text: 'A separate Cominty Managed Agent with no tools validated a reply. The server checked every binding and recorded ALLOW: 5.0 s, reported cost 0.028.',
    replay: 'guard', view: 'guard', param: 'gchk-79d5098f2affc413', cta: 'Inspect the live Guard check', kinds: ['cominty', 'rule'] },
  { n: '08', title: 'Memory that knows its version', text: 'The verified verdict was saved to native Cominty Memory, bound to policy v2, and read back independently. A later check recalled it without letting it decide.',
    replay: 'guard', view: 'integrations', cta: 'See the Cominty Memory evidence', kinds: ['cominty'] },
  { n: '09', title: 'Measured honestly', text: 'Five real trials: three reconciled at 100% accuracy, two failed the frozen search contract. Primary accuracy 60%, 25 of 25 questions captured, no observed false positives.',
    replay: 'evaluation-5', view: 'reliability', cta: 'Read the complete study', kinds: ['study'] },
];

const METRICS: [string, string][] = [
  ['24', 'documents analysed'], ['7', 'customer channels'], ['5', 'native Cominty questions'],
  ['PR #1', 'merged and verified in production'], ['1 / 1', 'live Guard check verified'], ['60%', 'primary accuracy, failures included'],
];

export function JuryGuide() {
  const jury=useJury();
  const open=(s:Step)=>{jury.select(s.replay);navigate(s.view,s.param);};
  return <div className="page jury-guide">
    <header className="jury-hero">
      <p className="eyebrow">Undrift · Cominty AI Engineering Challenge</p>
      <h1>One policy changes.<br/>Every channel stays right.</h1>
      <p className="jury-lede">Undrift finds drift across customer channels, asks for missing facts, prepares controlled fixes and verifies published corrections, with Cominty Managed Agents doing the reasoning and humans holding the authority.</p>
      <div className="jury-hero-actions">
        <button type="button" className="btn btn-primary" onClick={()=>open(STEPS[1]!)}>Start the walkthrough</button>
        <button type="button" className="btn" onClick={()=>document.getElementById('jury-steps')?.scrollIntoView({behavior:'smooth'})}>See all nine steps</button>
      </div>
    </header>

    <section className="jury-change" aria-label="The policy change">
      <div className="jury-change-card is-before"><span>Before · policy v1</span><strong>60 days</strong><p>voluntary returns, all purchases</p></div>
      <div className="jury-change-arrow" aria-hidden>→</div>
      <div className="jury-change-card is-after"><span>After · policy v2 · from 4 Nov 2026</span><strong>30 days</strong><p>new purchases · earlier promises kept · statutory 14-day right unchanged</p></div>
      <div className="jury-change-note"><p>Scattered across a help center, website, bot, support snippets, order e-mails, CRM notes and legal terms. Some mentions are outdated, some are still valid, some are protected commitments.</p></div>
    </section>

    <dl className="jury-metrics">{METRICS.map(([v,l])=><div key={l}><dt>{v}</dt><dd>{l}</dd></div>)}</dl>

    <section className="jury-legend" aria-label="Evidence labels">
      <h2>How to read this demo</h2>
      <p>Everything here is recorded evidence from genuine executions, served read-only. Each step is labelled with what produced it:</p>
      <div>{(Object.keys(EVIDENCE) as EvidenceKind[]).map(k=><EvidenceTag key={k} kind={k}/>)}</div>
    </section>

    <ol className="jury-steps" id="jury-steps">{STEPS.map(s=><li key={s.n}>
      <button type="button" className="jury-step" onClick={()=>open(s)}>
        <span className="jury-step-n">{s.n}</span>
        <span className="jury-step-body"><h2>{s.title}</h2><p>{s.text}</p>
          <span className="jury-step-tags">{s.kinds.map(k=><EvidenceTag key={k} kind={k}/>)}</span>
          <strong>{s.cta} →</strong></span>
      </button></li>)}</ol>

    <section className="panel jury-provenance"><h2>Evidence, not a simulation</h2>
      <p>This site serves immutable projections exported through Undrift’s own API from the original databases. Each archive keeps its own database identity and original run IDs; independent workflows are never merged into one story. No reviewer token, agent credential or writable endpoint exists here.</p>
      <p>Not shown as live: Slack notifications are prepared but not connected, and channel autonomy’s automatic application is demonstrated by a clearly labelled local fixture.</p>
      <p>Selected archive: {jury.selected?.name} · database <code>{jury.selected?.database_id}</code> · {jury.selected?.document_count} documents.</p>
      <p>Captured {jury.catalog?new Date(jury.catalog.captured_at).toLocaleString():''} · Snapshot <code>{jury.catalog?.snapshot_id.slice(0,16)}</code> · {jury.catalog?.replays.length} archives</p>
      <a href="https://shiftly-storefront.vercel.app/returns/" target="_blank" rel="noreferrer">View the real Shiftly storefront, already corrected ↗</a>
    </section>
  </div>;
}
