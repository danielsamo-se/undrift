import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createWorker} from './worker.mjs';

function fixture(){return {mode:'historical_readonly',snapshot_id:'immutable-snapshot',default_replay:'history',catalog:[{id:'history',database_id:'original-id'}],
  reports:{'reliability-v2':{state:'completed',results:[{status:'failed'}]}},datasets:{history:{routes:{
    '/api/health':{database_id:'original-id'},'/api/proposals':{proposals:[{id:'p',status:'approved',document_id:'d'}]},
    '/api/proposals/p/publication':{publication:{status:'verified',pr:{number:1}}},
    '/api/events':{events:[1,2,3].map(seq=>({seq,type:'agent.finding',subject_type:'run',subject_id:'real-run'})),latest_seq:3}
  }}}};}
const call=(worker,path,options={})=>worker.fetch(new Request('https://jury.example'+path,options));

test('all writes fail even with credentials; reads cannot mutate immutable evidence',async()=>{
  const bundle=fixture(),before=JSON.stringify(bundle),worker=createWorker(bundle,{});
  for(const method of ['POST','PUT','PATCH','DELETE','OPTIONS'])for(const path of ['/api/session','/api/proposals/p/approve','/api/proposals/p/publication/publish','/api/questions/q/answer','/api/run','/mcp/private']){
    assert.equal((await call(worker,path,{method,headers:{authorization:'Bearer operator-token'}})).status,403);
  }
  assert.equal(JSON.stringify(bundle),before);
});
test('unknown archives, private files and unlisted routes are inaccessible',async()=>{
  const worker=createWorker(fixture(),{});
  for(const path of ['/api/health?replay=__proto__','/api/health?replay=unknown','/.env','/.tools/answers.v1.json','/data/undrift.db','/mcp','/api/session','/api/reliability?series=../../private'])
    assert.equal((await call(worker,path)).status,404,path);
});
test('database identity and completed failures survive a fresh worker instance',async()=>{
  const bundle=fixture();
  for(let i=0;i<2;i++){
    const worker=createWorker(JSON.parse(JSON.stringify(bundle)),{});
    assert.equal((await(await call(worker,'/api/health')).json()).database_id,'original-id');
    assert.equal((await(await call(worker,'/api/reliability')).json()).results[0].status,'failed');
    assert.equal((await(await call(worker,'/api/proposals/p/publication')).json()).publication.status,'verified');
  }
});
test('events paginate immutable recorded sequence numbers without fresh fabricated events',async()=>{
  const data=fixture(),before=JSON.stringify(data),worker=createWorker(data,{});
  assert.deepEqual((await(await call(worker,'/api/events?tail=2')).json()).events.map(e=>e.seq),[2,3]);
  assert.deepEqual((await(await call(worker,'/api/events?after=3')).json()).events,[]);
  assert.equal((await call(worker,'/api/events?limit=NaN')).status,400);
  assert.equal((await call(worker,'/api/events?after=-1')).status,400);
  assert.equal(JSON.stringify(data),before);
});
test('static routing serves only explicit assets with security headers and HEAD support',async()=>{
  const worker=createWorker(fixture(),{'index.html':{type:'text/html',body:btoa('<h1>Undrift</h1>')}});
  const page=await call(worker,'/');assert.equal(await page.text(),'<h1>Undrift</h1>');
  assert.match(page.headers.get('content-security-policy'),/frame-ancestors 'none'/);
  assert.equal((await call(worker,'/unknown')).status,404);
  assert.equal(await(await call(worker,'/',{method:'HEAD'})).text(),'');
});
