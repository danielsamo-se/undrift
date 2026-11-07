import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { navigate, useRoute, useSearchParam, type View } from './hooks';
import { PUBLIC_JURY, setReplay } from './jury-mode';

interface Replay { id: string; name: string; description: string; database_id: string; source_sha256: string; run_ids: string[]; document_count: number; kind?: 'recorded' | 'fixture' }
interface Catalog { mode: string; snapshot_id: string; captured_at: string; default_replay: string; replays: Replay[] }
type Go = (replay: string, view?: View, param?: string | null, step?: number | null) => void;
const Context = createContext<{readonly:boolean; catalog:Catalog|null; selected:Replay|null; select:(id:string)=>void; go:Go}>({readonly:false,catalog:null,selected:null,select:()=>{},go:()=>{}});
export function useJury() { return useContext(Context); }

/** Every archive switch is one history entry carrying both the archive and the view, so back/forward stay consistent. */
function pushLocation(replay: string, view: View | undefined, param: string | null | undefined, step: number | null | undefined) {
  const url = new URL(window.location.href);
  url.searchParams.set('replay', replay);
  if (step) url.searchParams.set('step', String(step));
  else if (step === null) url.searchParams.delete('step');
  if (view) url.hash = `/${view}${param ? `/${encodeURIComponent(param)}` : ''}`;
  window.history.pushState(null, '', url);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

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
  useEffect(()=>{
    if(!catalog)return;
    const sync=()=>{
      const requested=new URL(window.location.href).searchParams.get('replay');
      const id=catalog.replays.some(r=>r.id===requested)?requested!:catalog.default_replay;
      setReplay(id);setSelected(id);
    };
    window.addEventListener('popstate',sync);
    return()=>window.removeEventListener('popstate',sync);
  },[catalog]);
  if(!PUBLIC_JURY)return <>{children}</>;
  if(error)return <p role="alert" className="jury-loading">{error}</p>;
  if(!catalog)return <p role="status" className="jury-loading">Loading genuine recorded evidence…</p>;
  const go:Go=(id,view,param,step)=>{if(catalog.replays.some(r=>r.id===id)){setReplay(id);setSelected(id);pushLocation(id,view,param,step);}};
  const select=(id:string)=>go(id);
  return <Context.Provider value={{readonly:true,catalog,selected:catalog.replays.find(r=>r.id===selected)??null,select,go}}>
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

/** In the jury replay, points to the archive that actually holds this kind of evidence. */
export function ArchiveHint({ replay, text }: { replay: string; text: string }) {
  const jury = useJury(); const route = useRoute();
  const target = jury.catalog?.replays.find(r => r.id === replay);
  return <div className="note archive-hint" data-testid="archive-hint"><p>{text}</p>
    {target && jury.selected?.id !== replay && <button type="button" className="btn btn-sm" onClick={() => jury.go(replay, route.view, route.param)}>Open “{target.name}”</button>}
  </div>;
}

/** Archive grouping and plain-language type for first-time visitors. Unknown archives stay reachable under "Other". */
export const ARCHIVE_GROUPS: { label: string; ids: string[] }[] = [
  { label: 'Main walkthrough', ids: ['discovery', 'clarification', 'publishing', 'guard'] },
  { label: 'Channel autonomy', ids: ['autonomy', 'autonomy-live', 'autonomy-fixture'] },
  { label: 'Reliability evaluation', ids: ['evaluation-1', 'evaluation-2', 'evaluation-3', 'evaluation-4', 'evaluation-5', 'reliability-v1'] },
];
export const ARCHIVE_TYPE: Record<string, { label: string; kind: EvidenceKind }> = {
  discovery: { label: 'Real Cominty run', kind: 'cominty' },
  clarification: { label: 'Real Cominty run + human answers', kind: 'cominty' },
  publishing: { label: 'Real Cominty run + GitHub/Vercel production', kind: 'production' },
  guard: { label: 'Real Cominty Guard checks', kind: 'cominty' },
  autonomy: { label: 'Recorded operator settings', kind: 'rule' },
  'autonomy-live': { label: 'Real Cominty run · failed closed', kind: 'cominty' },
  'autonomy-fixture': { label: 'Local fixture · not a Cominty run', kind: 'fixture' },
  'reliability-v1': { label: 'Archived evaluation (failed study)', kind: 'study' },
  ...Object.fromEntries([1, 2, 3, 4, 5].map(n => [`evaluation-${n}`, { label: 'Frozen evaluation trial', kind: 'study' as EvidenceKind }])),
};
export function archiveGroups(replays: Replay[]): { label: string; items: Replay[] }[] {
  const known = new Set(ARCHIVE_GROUPS.flatMap(g => g.ids));
  const groups = ARCHIVE_GROUPS.map(g => ({ label: g.label, items: g.ids.map(id => replays.find(r => r.id === id)).filter((r): r is Replay => !!r) }));
  const other = replays.filter(r => !known.has(r.id));
  return [...groups, ...(other.length ? [{ label: 'Other', items: other }] : [])].filter(g => g.items.length);
}

export function JuryBanner() {
  const jury=useJury(); const step=useSearchParam('step');
  if(!jury.readonly)return null;
  const fixture=jury.selected?.kind==='fixture';
  const type=jury.selected ? ARCHIVE_TYPE[jury.selected.id] : undefined;
  return <section className={`jury-banner${fixture?' is-fixture':''}`} aria-label="Historical replay">
    <div><strong>{fixture?'Local test fixture · Read-only':'Recorded evidence · Read-only jury demo'}</strong>
      <span>{fixture?'This archive was produced by a scripted local fixture. It demonstrates server rules, not a Cominty agent run.'
        :'Genuine recorded executions. No agents run from this site. Approval and publishing are disabled.'}</span>
      {jury.selected && <span className="jury-archive-desc" data-testid="archive-description">{type && <span className={`evidence-tag ev-${type.kind}`}>{type.label}</span>} {jury.selected.description}</span>}</div>
    <label>Evidence archive<select aria-label="Evidence archive" value={jury.selected?.id} onChange={e=>jury.go(e.target.value,'overview',null,step?Number(step):undefined)}>
      {archiveGroups(jury.catalog?.replays ?? []).map(g=><optgroup key={g.label} label={g.label}>
        {g.items.map(r=><option key={r.id} value={r.id}>{r.name}{ARCHIVE_TYPE[r.id]?` · ${ARCHIVE_TYPE[r.id]!.label}`:''}</option>)}
      </optgroup>)}
    </select></label>
    <a href="#/tour" onClick={e=>{e.preventDefault();if(jury.selected)jury.go(jury.selected.id,'tour',null,null);}}>Jury guide →</a>
  </section>;
}

interface Step { n: string; title: string; text: string; notice: string; replay: string; view: View; param?: string; cta: string; kinds: EvidenceKind[] }
export const STEPS: Step[] = [
  { n: '01', title: 'One policy changes', text: 'Shiftly shortens voluntary returns from 60 to 30 days for purchases from 4 November 2026. The statutory 14-day right and earlier promises stay valid.',
    notice: 'The policy strip: version P2 sets 30 days for purchases from 4 Nov 2026 and replaces 60 days in P1. Open “Authoritative text and version history” to read both versions, including the kept earlier promises and the unchanged statutory 14-day right.',
    replay: 'discovery', view: 'overview', cta: 'See the policy change', kinds: ['human'] },
  { n: '02', title: 'Undrift finds the drift', text: 'The Cominty Impact Agent searched 24 documents across seven channels through Undrift’s MCP server and recorded evidence-backed findings, original mistakes included.',
    notice: 'Each card is one of 24 documents in seven channels, coloured by the agent’s classification: outdated, valid distinct rule, existing commitment, unrelated or unknown. Click a card to read the exact quoted evidence behind it.',
    replay: 'discovery', view: 'impact', cta: 'Explore the Impact Map', kinds: ['cominty'] },
  { n: '03', title: 'Every agent step is on the record', text: 'The execution timeline rebuilds a real Cominty run from the audit log: each MCP search, retrieval and finding, the native questions, server validation and the outcome, each labelled agent, backend or human.',
    notice: 'Read the stage bar, then the steps: MCP searches, document reads with the exact version served, findings the server accepted, and five native questions. Every step says whether the Cominty agent, the backend or a human produced it.',
    replay: 'clarification', view: 'execution', param: 'run-254c843f8f6b42f9', cta: 'Open the execution timeline', kinds: ['cominty', 'rule'] },
  { n: '04', title: 'It asks instead of guessing', text: 'When facts were missing, the agent asked five native Cominty questions. A reviewer answered and the same conversation continued.',
    notice: 'This document could not be classified from the evidence alone. The panel shows the agent’s native question, its suggested options and the reviewer’s recorded answer; the same Cominty thread then continued in phase 2.',
    replay: 'clarification', view: 'impact', param: 'crm-002', cta: 'Inspect the clarification', kinds: ['cominty', 'human'] },
  { n: '05', title: 'Exact fixes, per-channel autonomy', text: 'Each correction is an exact patch with a diff. Every channel has its own server-enforced limit: legal and CRM stay manual; Help Center may auto-apply at most one change per run. A real delegated run failed the search contract and changed nothing.',
    notice: 'Each proposal is an exact diff with its quoted evidence and the policy version it is pinned to. None targets legal or CRM: those channels are manual-only by server rule.',
    replay: 'clarification', view: 'review', cta: 'Open the review queue', kinds: ['cominty', 'rule'] },
  { n: '06', title: 'A human-approved PR', text: 'An approved Website patch became real GitHub pull request #1 against the Shiftly storefront. Only a human could merge it.',
    notice: 'A human approved this website correction and published it as GitHub pull request #1. The stepper shows each recorded stage; “Open pull request #1” opens the real PR.',
    replay: 'publishing', view: 'review', param: 'prop-04ca726838fc', cta: 'Follow the publishing audit', kinds: ['human', 'production'] },
  { n: '07', title: 'Merged, deployed, verified', text: 'After the merge, Vercel deployed commit c391b36 and Undrift verified that production serves exactly the approved policy text.',
    notice: 'The publishing panel ties the recorded chain together: approval, PR #1, merge c391b36, Vercel deployment and the verified public text, with a link to the live page. It also explains why local document status differs from the published website.',
    replay: 'publishing', view: 'overview', cta: 'See the production verification', kinds: ['production'] },
  { n: '08', title: 'Guard: ALLOW and BLOCK', text: 'A separate tool-less Cominty agent validated two replies. It allowed a correct one and blocked “eight weeks”, a 56-day claim that no deterministic rule caught at the time. The server verified both verdicts.',
    notice: 'Two real Guard verdicts side by side. The red card matters most: the deterministic precheck found nothing, the Cominty Guard blocked “eight weeks” (56 days), and the server verified that verdict before recording it.',
    replay: 'guard', view: 'guard', param: 'gchk-5ce8448d76f85805', cta: 'Compare the two live Guard verdicts', kinds: ['cominty', 'rule'] },
  { n: '09', title: 'Memory that knows its version', text: 'The verified verdict was saved to native Cominty Memory, bound to policy v2, and read back independently. A later check recalled it without letting it decide.',
    notice: 'The Cominty Memory card: the stored file’s version, its binding to policy v2 and an identical independent read-back. The interpretation text is withheld because of a known redaction defect.',
    replay: 'guard', view: 'integrations', cta: 'See the Cominty Memory evidence', kinds: ['cominty'] },
  { n: '10', title: 'Measured honestly', text: 'Five real trials: three reconciled at 100% accuracy, two failed the frozen search contract. Primary accuracy 60%, 25 of 25 questions captured, no observed false positives.',
    notice: 'Five frozen paid trials, failures included: three reconciled at 100%, two failed the search contract and score zero. The aggregate counts them, so primary accuracy is 60%.',
    replay: 'evaluation-5', view: 'reliability', cta: 'Read the complete study', kinds: ['study'] },
];

const METRICS: [string, string][] = [
  ['24', 'documents analysed'], ['7', 'customer channels'], ['5', 'native Cominty questions'],
  ['PR #1', 'merged and verified in production'], ['2 / 2', 'live Guard verdicts verified (ALLOW + BLOCK)'], ['60%', 'primary accuracy, failures included'],
];

export function JuryGuide() {
  const jury=useJury();
  const open=(s:Step)=>jury.go(s.replay,s.view,s.param??null,STEPS.indexOf(s)+1);
  return <div className="page jury-guide">
    <header className="jury-hero">
      <p className="eyebrow">Undrift · Cominty AI Engineering Challenge</p>
      <h1>One policy changes.<br/>Every channel stays right.</h1>
      <p className="jury-lede">Undrift finds drift across customer channels, asks for missing facts, prepares controlled fixes and verifies published corrections, with Cominty Managed Agents doing the reasoning and humans holding the authority.</p>
      <div className="jury-hero-actions">
        <button type="button" className="btn btn-primary" onClick={()=>open(STEPS[0]!)}>Start the walkthrough</button>
        <button type="button" className="btn" onClick={()=>document.getElementById('jury-steps')?.scrollIntoView({behavior:'smooth'})}>See all {STEPS.length} steps</button>
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
      <p>Not shown as live: Slack notifications are prepared but not connected, and channel autonomy’s automatic application is demonstrated only by a clearly labelled local fixture. A real delegated Cominty attempt failed closed and is archived as such.</p>
      <p>Selected archive: {jury.selected?.name} · database <code>{jury.selected?.database_id}</code> · {jury.selected?.document_count} documents.</p>
      <p>Captured {jury.catalog?new Date(jury.catalog.captured_at).toLocaleString():''} · Snapshot <code>{jury.catalog?.snapshot_id.slice(0,16)}</code> · {jury.catalog?.replays.length} archives</p>
      <a href="https://shiftly-storefront.vercel.app/returns/" target="_blank" rel="noreferrer">View the real Shiftly storefront, already corrected ↗</a>
    </section>
  </div>;
}

/** Guided walkthrough: current step, what to notice, previous/next. Navigation only; it never simulates execution. */
export function TourBar() {
  const jury = useJury(); const route = useRoute(); const param = useSearchParam('step');
  if (!jury.readonly || route.view === 'tour') return null;
  const n = Number(param);
  if (!Number.isInteger(n) || n < 1 || n > STEPS.length) return null;
  const step = STEPS[n - 1]!;
  const here = jury.selected?.id === step.replay && route.view === step.view && (!step.param || route.param === step.param);
  const open = (i: number) => { const t = STEPS[i - 1]!; jury.go(t.replay, t.view, t.param ?? null, i); };
  const guide = () => jury.selected && jury.go(jury.selected.id, 'tour', null, null);
  const exit = () => { const url = new URL(window.location.href); url.searchParams.delete('step'); window.history.replaceState(null, '', url); window.dispatchEvent(new PopStateEvent('popstate')); };
  const prev = STEPS[n - 2]; const next = STEPS[n];
  if (!here) return <nav className="tour-bar is-away" aria-label="Walkthrough" data-testid="tour-bar">
    <p className="tour-away">Walkthrough paused at step {n} of {STEPS.length}: <strong>{step.title}</strong></p>
    <div className="tour-actions">
      <button type="button" className="btn btn-sm btn-primary" onClick={() => open(n)}>Return to step {n}</button>
      <button type="button" className="btn btn-sm" onClick={guide}>All steps</button>
      <button type="button" className="btn btn-sm" onClick={exit}>Exit walkthrough</button>
    </div>
  </nav>;
  return <nav className="tour-bar" aria-label="Walkthrough" data-testid="tour-bar">
    <div className="tour-head">
      <span className="tour-count">Step {n} of {STEPS.length}</span>
      <h2 className="tour-title">{step.title}</h2>
      <span className="tour-tags">{step.kinds.map(k => <EvidenceTag key={k} kind={k} />)}</span>
    </div>
    <p className="tour-notice"><span>What to notice</span>{step.notice}</p>
    <div className="tour-actions">
      <button type="button" className="btn btn-sm" disabled={!prev} onClick={() => open(n - 1)} aria-label={prev ? `Previous step: ${prev.title}` : 'No previous step'}>
        {'← '}Previous<span className="tour-label">{prev ? `: ${prev.title}` : ''}</span></button>
      <ol className="tour-dots" aria-label="Steps">{STEPS.map((s, i) => <li key={s.n}>
        <button type="button" className={i + 1 === n ? 'is-current' : ''} aria-current={i + 1 === n ? 'step' : undefined} aria-label={`Step ${i + 1}: ${s.title}`} onClick={() => open(i + 1)}>{i + 1}</button></li>)}</ol>
      <button type="button" className="btn btn-sm" onClick={guide}>All steps</button>
      {next ? <button type="button" className="btn btn-sm btn-primary" onClick={() => open(n + 1)} aria-label={`Next step: ${next.title}`}>
        Next<span className="tour-label">: {next.title}</span>{' →'}</button>
        : <button type="button" className="btn btn-sm btn-primary" onClick={guide}>Finish: back to the guide</button>}
    </div>
  </nav>;
}
