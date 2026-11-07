import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { navigate } from './hooks';
import { PUBLIC_JURY, setReplay } from './jury-mode';

interface Replay { id: string; name: string; description: string; database_id: string; source_sha256: string; run_ids: string[]; document_count: number }
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
  if(error)return <p role="alert">{error}</p>;
  if(!catalog)return <p role="status">Loading genuine historical evidence…</p>;
  const select=(id:string)=>{if(catalog.replays.some(r=>r.id===id)){
    setReplay(id);setSelected(id);const url=new URL(window.location.href);url.searchParams.set('replay',id);window.history.replaceState(null,'',url);
  }};
  return <Context.Provider value={{readonly:true,catalog,selected:catalog.replays.find(r=>r.id===selected)??null,select}}>
    <div key={selected}>{children}</div>
  </Context.Provider>;
}

export function JuryBanner() {
  const jury=useJury();if(!jury.readonly)return null;
  return <section className="jury-banner" aria-label="Historical replay">
    <div><strong>Historical replay · Read-only jury demo</strong><span>Genuine recorded executions. No agents are running. Approval and publishing are disabled.</span></div>
    <label>Evidence archive<select aria-label="Evidence archive" value={jury.selected?.id} onChange={e=>{jury.select(e.target.value);navigate('overview');}}>
      {jury.catalog?.replays.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}
    </select></label>
    <a href="#/tour">Jury guide →</a>
  </section>;
}

export function JuryGuide() {
  const jury=useJury();
  const open=(replay:string,view:'impact'|'review'|'reliability'|'activity',param?:string)=>{jury.select(replay);navigate(view,param);};
  return <div className="page jury-guide"><header className="ph"><div><p className="eyebrow">Undrift · Cominty AI Engineering Challenge</p>
    <h1>Change one fact.<br/>Keep every channel right.</h1><p className="ph-sub">Explore real agent analysis, native clarification and human-authorized publishing.</p></div></header>
    <p className="jury-intro">Shiftly is a fictional storefront changing its voluntary return window from 60 to 30 days for new purchases. Undrift finds drift, preserves statutory rights and existing commitments, and puts every correction behind human approval.</p>
    <div className="jury-cards">
      <button className="card jury-card" onClick={()=>open('discovery','impact')}><span>01 · Discover</span><h2>Search seven channels</h2><p>The first real discovery run inspected 24 documents. Its original mistakes are preserved.</p><strong>Explore the Impact Map →</strong></button>
      <button className="card jury-card" onClick={()=>open('clarification','impact')}><span>02 · Clarify</span><h2>Ask before assuming</h2><p>Five native Cominty questions, reviewer answers, and a continuation in the same conversation.</p><strong>Inspect genuine clarification →</strong></button>
      <button className="card jury-card" onClick={()=>open('clarification','review')}><span>03 · Review</span><h2>Authorize the exact patch</h2><p>Read the original evidence, proposed correction and diff. Public visitors cannot approve or apply it.</p><strong>Open the Review Queue →</strong></button>
      <button className="card jury-card" onClick={()=>open('publishing','review','prop-04ca726838fc')}><span>04 · Publish</span><h2>PR → Vercel → Verified</h2><p>A human merged real GitHub PR #1. Recorded verification checks the production commit and exact approved policy bytes.</p><strong>Follow the publishing audit →</strong></button>
      <button className="card jury-card" onClick={()=>open('evaluation-5','reliability')}><span>05 · Evaluate</span><h2>Measure reliability honestly</h2><p>Five independent conversations: three reconciled and two failed search certification. Every failure stays in the denominator.</p><strong>See the complete Reliability study →</strong></button>
      <button className="card jury-card" onClick={()=>open('clarification','activity')}><span>06 · Audit</span><h2>Inspect what actually happened</h2><p>Canonical run IDs, authenticated MCP activity, original messages and recorded costs, without fabricated fresh events.</p><strong>Explore recorded execution evidence →</strong></button>
    </div>
    <section className="panel jury-provenance"><h2>Evidence, not a simulated execution</h2><p>This cloud application serves persistent, immutable projections exported through the existing Starlette API and readmodel. Each archive retains its own database identity and original run IDs. Independent workflows are never combined into a historical conversation.</p>
      <p>Captured {jury.catalog?new Date(jury.catalog.captured_at).toLocaleString():''} · Snapshot <code>{jury.catalog?.snapshot_id}</code></p>
      <p>Selected database <code>{jury.selected?.database_id}</code> · {jury.selected?.document_count} documents. The connected Shiftly production website already shows 30 days; this replay does not reset it.</p>
      <a href="https://shiftly-storefront.vercel.app/returns/" target="_blank" rel="noreferrer">View the real Shiftly storefront ↗</a>
    </section>
  </div>;
}
