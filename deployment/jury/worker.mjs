// Pure read-only API adapter for canonical, immutable Starlette projections.
// No bindings, credentials, outbound requests, paid SDK or write handlers exist.
const headers = {
  'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store',
  'x-content-type-options': 'nosniff', 'referrer-policy': 'no-referrer',
  'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
};

export function createWorker(bundle, assets) {
  const json = (data, status=200) => new Response(JSON.stringify(data), {status, headers});
  const missing = () => json({error:'not_found',message:'No public replay route exists here.'},404);
  return {
    async fetch(request) {
      if (!['GET','HEAD'].includes(request.method)) return json({error:'jury_readonly',message:'Historical jury replay is read-only. No execution or publication is available.'},403);
      const url = new URL(request.url), path = url.pathname;
      let response;
      if (path === '/api/demo') response=json({mode:bundle.mode,snapshot_id:bundle.snapshot_id,
        captured_at:bundle.captured_at,default_replay:bundle.default_replay,replays:bundle.catalog});
      else if (path.startsWith('/api/')) {
        const replay=url.searchParams.get('replay') || bundle.default_replay;
        if (!Object.hasOwn(bundle.datasets,replay)) return missing();
        const dataset=bundle.datasets[replay];
        if (path==='/api/reliability') {
          const series=url.searchParams.get('series') || 'reliability-v2';
          response=Object.hasOwn(bundle.reports,series) ? json(bundle.reports[series]) : missing();
        } else if (!Object.hasOwn(dataset.routes,path)) response=missing();
        else if (path==='/api/events') {
          const integer=(key,fallback,max=1000) => { const raw=url.searchParams.get(key);const n=raw===null?fallback:Number(raw);if(!Number.isSafeInteger(n)||n<0) throw Error('Invalid pagination');return Math.min(n,max); };
          try {
            const all=dataset.routes[path], after=integer('after',0,Number.MAX_SAFE_INTEGER), tail=url.searchParams.has('tail');
            let events=all.events.filter(e=>(tail||e.seq>after)&&(!url.searchParams.get('type')||e.type.startsWith(url.searchParams.get('type')))
              &&(!url.searchParams.get('subject_type')||(e.subject_type===url.searchParams.get('subject_type')&&e.subject_id===url.searchParams.get('subject_id'))));
            const limit=integer(tail?'tail':'limit',100);
            events=tail?events.slice(-Math.max(1,limit)):events.slice(0,Math.max(1,limit));
            response=json({events,latest_seq:all.latest_seq,next_after:events.at(-1)?.seq??after});
          } catch {response=json({error:'invalid_request',message:'Invalid event pagination.'},400);}
        } else if(path==='/api/proposals') {
          response=json({proposals:dataset.routes[path].proposals.filter(p=>(!url.searchParams.get('status')||p.status===url.searchParams.get('status'))
            &&(!url.searchParams.get('document_id')||p.document_id===url.searchParams.get('document_id')))});
        } else response=json({...dataset.routes[path], replay_id:replay, historical:true, snapshot_id:bundle.snapshot_id});
      } else {
        const name=path==='/'?'index.html':path.slice(1);
        if(!Object.hasOwn(assets,name)) return missing();
        const asset=assets[name];
        const binary=Uint8Array.from(atob(asset.body),c=>c.charCodeAt(0));
        response=new Response(binary,{headers:{...headers,'content-type':asset.type,
          'cache-control':name==='index.html'?'no-cache':'public, max-age=31536000, immutable'}});
      }
      return request.method==='HEAD'?new Response(null,{status:response.status,headers:response.headers}):response;
    }
  };
}
