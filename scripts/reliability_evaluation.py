"""Frozen five-conversation evaluation. prepare/freeze are free; execute needs --paid.

No retry/resume command exists. Every paid POST is marked before dispatch, and one
continuation per conversation is the maximum. Fixtures and raw evidence stay private.
"""
import argparse
import asyncio
import contextlib
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
from importlib.metadata import version
import json
import logging
import os
import re
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)
import config
import clarification
from cominty_sdk import AsyncCominty
from discovery_seed import create_demo, manifest, seed_hash
import httpx
from mcp import Client
import reliability
import run_impact
from scripts.evaluate_clarification import supply_answers
from store import Store

PRIVATE = ROOT / '.tools/reliability-v1'
PLAN = PRIVATE / 'plan.json'
ACTIVE = PRIVATE / 'active.json'
PROTOCOL = ROOT / 'evaluations/reliability-v1-protocol.json'
REPORT = reliability.DEFAULT_REPORT
TRUTH = ROOT / 'tests/data/discovery_ground_truth.json'
SOURCES = ['run_impact.py','workflow.py','clarification.py','clarification_schema.py','discovery.py',
           'discovery_seed.py','store.py','models.py','fixtures.py','server.py','config.py','reliability.py',
           'scripts/reliability_evaluation.py','scripts/evaluate_clarification.py','scripts/evaluation_service.py','scripts/evaluation_services.ps1']
HOST = 'chatter-poker-theorize.ngrok-free.dev'
CONNECTION = 'undrift_mcp_ngrok'
MODEL = 'Claude Sonnet 5.5 (High)'
TOOLS = {'health_check','get_policy','get_document','list_channels','search_documents','similar_documents','record_finding'}
SERIES = 'reliability-v1'
LIFECYCLE = 'native_evidence_independent_v2'


def select_series(name):
    """Separate study identity from the backwards-compatible report JSON schema."""
    if not re.fullmatch(r'reliability-v[1-9][0-9]*', name):
        raise ValueError('Expected a versioned series name such as reliability-v2')
    global SERIES, PRIVATE, PLAN, ACTIVE, PROTOCOL, REPORT
    SERIES = name
    PRIVATE = ROOT / '.tools' / name
    PLAN, ACTIVE = PRIVATE / 'plan.json', PRIVATE / 'active.json'
    PROTOCOL = ROOT / 'evaluations' / (name + '-protocol.json')
    REPORT = ROOT / 'evaluations' / (name + '-results.json')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_trial(measured, *, domain_unchanged=True):
    """Quality failures stay in scores; trust/evidence failures stop the entire series."""
    require(measured['protected_automation_safe'], 'Protected-channel automation violation')
    require(measured['native_question_integrity'], 'Provider/native-question persistence mismatch')
    require(measured['mcp_error_count'] == 0, 'MCP validation/authentication failure')
    # health_check is deliberately unscoped; it is not retrieval/finding evidence.
    require(measured.get('unattributed_scoped_mcp_calls', measured['unattributed_mcp_calls']) == 0,
            'Unattributed scoped MCP activity')
    require(domain_unchanged, 'Seeded policy/document state changed')


def provider_evidence(run, thread, message):
    return {'run_id': run.id, 'thread_id': str(thread.id), 'message_id': str(message.id),
            'status': message.status.value, 'live': message.live,
            'questions': [{'prompt': q.prompt, 'options': q.options} for q in (message.questions or [])],
            'content_sha256': hashlib.sha256((message.content or '').encode()).hexdigest()}


def check_provider(snapshot, terminals):
    require(bool(terminals) and terminals[-1].get('questions') == snapshot['questions'],
            'Provider terminal/native-question evidence mismatch')
    require(snapshot['status'] == 'success' and not snapshot['live'], 'Provider execution failed or remains live')


def stop_diagnostic(exc):
    """Never emit an empty assertion reason, unknown exception text, or credentials."""
    diagnostic = run_impact.failure_diagnostics(exc)
    if isinstance(exc, (AssertionError, RuntimeError)) and str(exc):
        diagnostic['message'] = config.safe_text(str(exc))
    tb = exc.__traceback__
    if tb:
        while tb.tb_next: tb = tb.tb_next
        diagnostic['location'] = {'file': Path(tb.tb_frame.f_code.co_filename).name, 'line': tb.tb_lineno}
    return diagnostic

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

def file_hash(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path, value, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = config.redact(json.dumps(value, indent=2, ensure_ascii=False)) + '\n'
    if exclusive:
        with path.open('x', encoding='utf-8') as f: f.write(text)
    else:
        temporary = path.with_suffix('.tmp'); temporary.write_text(text, encoding='utf-8'); temporary.replace(path)

def domain(store):
    with store.snapshot() as c:
        return {name: digest([tuple(row) for row in c.execute('SELECT * FROM '+name+' ORDER BY 1,2')])
                for name in ['policies','policy_versions','channels','documents','document_versions']}

def configure(slot):
    config.load_env()
    os.environ.update(UNDRIFT_DB=str(ROOT / slot['database']), MCP_PORT='8001', MCP_PUBLIC_HOST=HOST,
        UNDRIFT_MCP_CONNECTION=CONNECTION, UNDRIFT_EXPECTED_MODEL=MODEL, UNDRIFT_REVIEWER='evaluation:fixture-v1')

def paid_gate(total, cost_known, attempts):
    assert cost_known, 'Reported cost unavailable; paid execution blocked'
    assert total.is_finite() and total >= 0 and total < Decimal('5'), 'Cumulative reported evaluation cost reached 5; stop before next message'
    assert len(attempts) < 10, 'Ten-message authorization exhausted'
    keys=[(a['slot'],a['phase']) for a in attempts]
    assert len(set(keys))==len(keys) and all(1<=slot<=5 and phase in (1,2) for slot,phase in keys), 'Invalid or duplicate execution attempt'

def prepare(ui):
    assert not PLAN.exists() and not PROTOCOL.exists() and not REPORT.exists()
    config.load_env()
    ui = ui.resolve(); assert ui.is_relative_to((ROOT / '.test-tmp').resolve()) and (ui/'index.html').is_file()
    archives = [p for p in (ROOT/'data').rglob('*') if p.is_file() and (p.suffix=='.db' or p.name.endswith('.db-wal'))]
    archives += list((ROOT/'runs').glob('impact_*.json'))
    archives += [p for p in (ROOT/'evaluations').glob('*') if p.is_file()]
    archives += [p for p in (ROOT/'.tools').glob('reliability-v*/*') if p.is_file() and p.suffix in {'.json','.lock'}]
    archives += [ROOT/'.env', ROOT/'tests/data/discovery_ground_truth.json', ROOT/'agents/discovery.md']
    protection = {str(p.relative_to(ROOT)): file_hash(p) for p in archives}
    answers = {'version':'unavailable-facts-v1', 'answers':{did:{'text':'I cannot establish this fact'} for did in
        ['crm-002','bot-003','help-002','help-004','support-002']}}
    write(PRIVATE/'answers.v1.json', answers, True)
    slots = []
    for number in range(1,6):
        path = ROOT / f'data/{SERIES}/evaluation-{number:02}.db'
        store = create_demo(path)
        slots.append({'slot':number, 'series_id':SERIES, 'database':str(path.relative_to(ROOT)).replace('\\','/'),
            'database_id':store.identity(), 'seed_sha256':seed_hash(), 'domain':domain(store),
            'ui_directory':str(ui.relative_to(ROOT)).replace('\\','/'), 'public_host':HOST})
    assert len({s['database_id'] for s in slots}) == 5 and all(s['domain']==slots[0]['domain'] for s in slots)
    plan = {'version':reliability.VERSION, 'series_id':SERIES, 'lifecycle':LIFECYCLE, 'slots':slots, 'protected_files':protection,
            'answers_sha256':file_hash(PRIVATE/'answers.v1.json'), 'created_at':datetime.now(UTC).isoformat()}
    write(PLAN, plan, True); write(ACTIVE, slots[0])
    write(REPORT, {'version':reliability.VERSION,'series_id':SERIES,'state':'prepared','evidence_kind':'real_cominty_evaluation',
        'results':[],'aggregate':reliability.aggregate([]), 'stop_reason':None})
    print('Prepared five distinct deterministic stores and private versioned answers; no agent executed.')

class ReadOnlyCominty(AsyncCominty):
    def __init__(self, **kwargs):
        super().__init__(**kwargs); request=self._transport.request
        async def guarded(method,path,**options):
            assert method=='GET', 'Metadata checks cannot write or execute'
            return await request(method,path,**options)
        self._transport.request=guarded

async def attestation(store):
    async with ReadOnlyCominty(timeout=25) as remote:
        agent=await remote.agents.get(os.environ['COMINTY_AGENT_ID'])
        instructions=(agent.instructions or '').replace('\r\n','\n').strip()
        assert instructions==(ROOT/'agents/discovery.md').read_text(encoding='utf-8').replace('\r\n','\n').strip()
        assert [m.name for m in agent.models]==[MODEL] and agent.capabilities.mcp.connections==[CONNECTION]
        assert agent.capabilities.mcp.activation=='always'
        integration=next(i for i in await remote.mcp.list(enabled=None) if i.name==CONNECTION)
        assert integration.is_enabled and integration.mcp_url=='https://'+HOST+config.mcp_path(config.path_secret())
        metadata={'instructions_normalized_sha256':hashlib.sha256(instructions.encode()).hexdigest(),
            'agent_id':agent.id, 'models':[{'id':m.id,'name':m.name} for m in agent.models],
            'capabilities_sha256':digest(agent.capabilities.model_dump(mode='json')),
            'connection':CONNECTION, 'public_host':HOST, 'integration_enabled':True}
    async with Client('https://'+HOST+config.mcp_path(config.path_secret()),read_timeout_seconds=20) as mcp:
        tools=(await mcp.list_tools()).tools
        assert {t.name for t in tools}==TOOLS
        metadata['tools_sha256']=digest([t.model_dump(mode='json') for t in sorted(tools,key=lambda t:t.name)])
        metadata['tools']=sorted(TOOLS)
        for t in tools:
            if t.name!='health_check': assert {'run_id','run_token'} <= set(t.input_schema.get('properties',{}))
        health=await mcp.call_tool('health_check',{})
        assert not health.is_error and health.structured_content['database_id']==store.identity()
        assert health.structured_content['discovery_strategy']=='search_first_v1'
        # No accepted result or diagnostic run is created by this negative auth check.
        assert (await mcp.call_tool('get_policy',{'policy_id':'shiftly-returns'})).is_error
    async with httpx.AsyncClient(timeout=20) as web:
        assert (await web.get('http://127.0.0.1:8401/api/health')).json()['database']==store.path.name
        tunnels=(await web.get('http://127.0.0.1:4040/api/tunnels')).json()['tunnels']
        tunnel=next(t for t in tunnels if t['public_url']=='https://'+HOST)
        assert tunnel['config']['inspect'] is False and tunnel['config']['addr']=='http://127.0.0.1:8001'
        for path in [f'/.tools/{SERIES}/answers.v1.json','/tests/data/discovery_ground_truth.json']:
            assert (await web.get('https://'+HOST+path)).status_code==404
    return metadata

async def freeze():
    plan=json.loads(PLAN.read_text(encoding='utf-8')); configure(plan['slots'][0])
    assert not PROTOCOL.exists()
    metadata=await attestation(Store(ROOT/plan['slots'][0]['database']))
    protocol={'version':reliability.VERSION,'series_id':SERIES,'lifecycle':LIFECYCLE,'frozen_at':datetime.now(UTC).isoformat(), 'remote':metadata,
        'instructions_raw_sha256':file_hash(ROOT/'agents/discovery.md'), 'corpus_sha256':digest(manifest()),
        'seed_sha256':seed_hash(), 'rubric_sha256':file_hash(TRUTH), 'answer_fixture_version':'unavailable-facts-v1',
        'answer_fixture_sha256':plan['answers_sha256'], 'source_sha256':{p:file_hash(ROOT/p) for p in SOURCES},
        'conversations':5,'max_continuations_per_conversation':1,'max_paid_messages':10,'initial_max_steps':40,
        'continuation_max_steps':20,'search_calls_max':12,'similarity_calls_max':4,'reported_cost_stop':'5',
        'paraphrase_documents':['web-003','bot-001'], 'scoring':'Reconciled labels only; missing labels count as misses. Native question recall is separate.',
        'undefined_precision':'null; aggregates display defined_count and never invent precision for empty predictions.',
        'continuation_policy':'Only for actual native questions, with the private fixed factual answers; no third turn.',
        'quality_failure_policy':'Search-contract failure is terminal for that conversation, with no answers/continuation or reconciled labels. Retain it in all denominators; proceed to the next independent slot only if trust/provider/isolation checks pass.',
        'batch_stop_policy':'Stop on safety, authentication, isolation, provider/evidence inconsistency, unknown cost, interruption or spending/message limits.',
        'sdk_version':version('cominty-sdk'),
        'steps_measurement':'Reserved parent allowance plus unique llm correlation IDs; no exact consumed-step API.'}
    write(PROTOCOL,protocol,True)
    print('Frozen protocol:',file_hash(PROTOCOL),'No paid messages started.')

def assert_frozen(protocol,plan):
    assert file_hash(TRUTH)==protocol['rubric_sha256'] and file_hash(PRIVATE/'answers.v1.json')==protocol['answer_fixture_sha256']
    assert all(file_hash(ROOT/p)==value for p,value in protocol['source_sha256'].items())
    assert digest(manifest())==protocol['corpus_sha256']
    assert all(file_hash(ROOT/p)==value for p,value in plan['protected_files'].items()), 'Historical evidence changed'

def switch(slot):
    write(ACTIVE,slot)
    # Long-lived Windows children can inherit pipe handles. Wait only for the
    # launcher process, never for EOF on a pipe held by the new services.
    with (PRIVATE/f"switch-{slot['slot']}.log").open('a',encoding='utf-8') as log:
        subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/evaluation_services.ps1'),
                        '-Config',str(ACTIVE)],cwd=ROOT,check=True,stdout=log,stderr=subprocess.STDOUT,timeout=30)

async def recover_preflight():
    """Free infrastructure recovery ONLY when no paid dispatch was attempted."""
    plan=json.loads(PLAN.read_text(encoding='utf-8')); old=json.loads(PROTOCOL.read_text(encoding='utf-8'))
    report=json.loads(REPORT.read_text(encoding='utf-8'))
    assert report['state']=='stopped' and not report['results'] and report.get('attempted_paid_messages',0)==0
    assert not list(PRIVATE.glob('attempt-*.json'))
    for slot in plan['slots']:
        assert not Store(ROOT/slot['database']).runs()
    assert file_hash(TRUTH)==old['rubric_sha256'] and file_hash(PRIVATE/'answers.v1.json')==old['answer_fixture_sha256']
    assert file_hash(ROOT/'agents/discovery.md')==old['instructions_raw_sha256'] and digest(manifest())==old['corpus_sha256']
    assert all(file_hash(ROOT/p)==v for p,v in plan['protected_files'].items())
    configure(plan['slots'][0])
    assert await attestation(Store(ROOT/plan['slots'][0]['database']))==old['remote']
    write(PRIVATE/'preflight-protocol-1.json',old,True)
    write(PRIVATE/'preflight-stop-1.json',report,True)
    (PRIVATE/'execution.lock').rename(PRIVATE/'preflight-execution-1.lock')
    revised={**old,'source_sha256':{p:file_hash(ROOT/p) for p in SOURCES},'preflight_revision':2,
        'frozen_at':datetime.now(UTC).isoformat(),
        'pre_execution_correction':'Windows inherited pipe blocked service handoff; zero conversations or paid dispatches. Original protocol/stop/lock preserved privately and in prior Git checkpoint.'}
    write(PROTOCOL,revised)
    write(REPORT,{'version':reliability.VERSION,'state':'prepared','evidence_kind':'real_cominty_evaluation',
        'results':[],'aggregate':reliability.aggregate([]),'stop_reason':None})
    print('Preflight infrastructure recovered; no paid execution was retried. Revised protocol:',file_hash(PROTOCOL))

async def wait_service(slot):
    for _ in range(25):
        try:
            async with httpx.AsyncClient(timeout=2) as web:
                health=(await web.get('http://127.0.0.1:8401/api/health')).json()
                if health['database']==Path(slot['database']).name: return
        except Exception: pass
        await asyncio.sleep(1)
    raise RuntimeError('Evaluation API did not activate')

async def execute():
    protocol=json.loads(PROTOCOL.read_text(encoding='utf-8')); plan=json.loads(PLAN.read_text(encoding='utf-8'))
    lock=PRIVATE/'execution.lock'
    if lock.exists():
        raise FileExistsError('Evaluation execution already attempted; no retry')
    require(protocol.get('lifecycle') == LIFECYCLE and protocol.get('series_id') == SERIES
            and plan.get('series_id') == SERIES, 'Lifecycle/source changes require a fresh versioned evaluation series')
    with lock.open('x') as f: f.write(datetime.now(UTC).isoformat())
    results=[]; attempts=[]; total=Decimal(0); cost_known=True
    report={'version':reliability.VERSION,'series_id':SERIES,'state':'executing','evidence_kind':'real_cominty_evaluation',
            'protocol_sha256':file_hash(PROTOCOL),'protocol':protocol,'results':results,'stop_reason':None}
    def persist():
        report.update(results=results,aggregate=reliability.aggregate(results),attempted_paid_messages=len(attempts),
                      cumulative_reported_cost=str(total),updated_at=datetime.now(UTC).isoformat())
        write(REPORT,report)
    def budget():
        paid_gate(total,cost_known,attempts)
    async def message(store,slot,root_id=None):
        nonlocal total,cost_known
        budget(); assert_frozen(protocol,plan)
        assert await attestation(store)==protocol['remote'], 'Provider/tool configuration changed'
        before=[r.id for r in store.runs()]
        number=2 if root_id else 1
        conversation=next((v for v in clarification.listing(store) if v['id']==root_id),None) if root_id else None
        thread=conversation['thread_id'] if conversation else None
        attempt_path=PRIVATE/f"attempt-{slot['slot']}-{number}.json"
        attempt={'slot':slot['slot'],'phase':number,'root_run_id':root_id,'thread_id':thread,'state':'not_dispatched'}
        class BoundedCominty(AsyncCominty):
            def __init__(self,**kwargs):
                super().__init__(**kwargs); request=self._transport.request
                async def guarded(method,path,**options):
                    if method=='POST' and path==('/chat/'+thread if thread else '/chat'):
                        budget(); assert not attempt_path.exists()
                        assert options['json_body']['options']['max_steps']==(20 if thread else 40)
                        if not thread:
                            caps=options['json_body']['options']['capabilities']
                            assert caps['mcp']['connections']==[CONNECTION] and caps['mcp']['activation']=='always'
                            assert all(v['activation']=='never' for k,v in caps.items() if k!='mcp')
                        else:
                            assert 'capabilities' not in options['json_body']['options']
                        opened=[r for r in store.runs() if r.status=='open']; assert len(opened)==1
                        attempt.update(run_id=opened[0].id,state='dispatch_attempted',at=datetime.now(UTC).isoformat())
                        write(attempt_path,attempt,True); attempts.append(attempt); persist()
                        response=await request(method,path,**options)
                        if thread:
                            assert response['thread_id']==thread
                            attempt['message_id']=response['id']
                        else:
                            attempt['thread_id']=response['id']
                            assistant=[m for m in response['messages'] if m['role']=='assistant'][-1]
                            attempt['message_id']=assistant['id']
                        attempt['state']='dispatched'; write(attempt_path,attempt); return response
                    if method=='POST' and attempt.get('message_id') and path==f"/chat/messages/{attempt['message_id']}/cancel":
                        attempt['cancel_requested']=True; write(attempt_path,attempt)
                        return await request(method,path,**options)
                    assert method=='GET', 'Unauthorized extra paid message or remote modification'
                    return await request(method,path,**options)
                self._transport.request=guarded
        original=run_impact.AsyncCominty; run_impact.AsyncCominty=BoundedCominty
        sys.argv=['run_impact.py','--run','--discovery','--max-steps', '20' if root_id else '40']
        if root_id: sys.argv+=['--continue-conversation',root_id]
        error=None; code=None
        try:
            with (PRIVATE/f"slot-{slot['slot']}-phase-{number}.log").open('x',encoding='utf-8') as log:
                with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log): code=await run_impact.main()
        except BaseException as exc: error=stop_diagnostic(exc)
        finally: run_impact.AsyncCominty=original
        new=[r for r in store.runs() if r.id not in before]
        if not new:
            raise RuntimeError('Preflight/dispatch stopped before a local phase; no automatic retry')
        assert len(new)==1
        run=new[0]; root=root_id or run.id
        phase=clarification.phase(store,run.id)
        cost=json.loads(phase['cost']) if phase and phase['cost'] else None
        if cost and not cost.get('failed') and cost.get('total') is not None:
            total+=Decimal(str(cost['total']))
        else: cost_known=False
        attempt.update(exit_code=code,error=error,cost=cost,run_id=run.id,state='finished')
        write(PRIVATE/f"completion-{slot['slot']}-{number}.json",attempt,True)
        # Capture actual terminal provider state through read-only metadata.
        async with ReadOnlyCominty(timeout=25) as remote:
            remote_thread=await remote.threads.get(run.thread_id)
            remote_message=next(m for m in remote_thread.messages if str(m.id)==run.message_id)
            with store.read() as c:
                terminals=[json.loads(r[0]) for r in c.execute("SELECT payload FROM conversation_evidence WHERE run_id=? AND kind='terminal'",(run.id,))]
            snapshot=provider_evidence(run,remote_thread,remote_message)
            # Preserve the actual observation BEFORE rejecting inconsistent state.
            write(PRIVATE/f"provider-{slot['slot']}-{number}.json",snapshot,True)
            check_provider(snapshot,terminals)
        require(error is None, 'Local execution interrupted or failed; inspect completion diagnostics')
        return root
    try:
        assert_frozen(protocol,plan)
        answers=json.loads((PRIVATE/'answers.v1.json').read_text(encoding='utf-8'))['answers']
        truth=json.loads(TRUTH.read_text(encoding='utf-8'))
        for slot in plan['slots']:
            budget(); assert_frozen(protocol,plan)
            configure(slot); store=Store(ROOT/slot['database'])
            assert not store.runs() and store.identity()==slot['database_id'] and domain(store)==slot['domain']
            switch(slot); await wait_service(slot)
            root=await message(store,slot)
            measured=reliability.score(store,root,truth,evidence_kind='real_cominty')
            measured.update(slot=slot['slot'],database=slot['database'],database_id=slot['database_id'])
            measured['message_count']=sum(a['slot']==slot['slot'] for a in attempts)
            results.append(measured); persist()
            check_trial(measured,domain_unchanged=domain(store)==slot['domain'])
            conv=next(v for v in clarification.listing(store) if v['id']==root)
            if conv['state']=='awaiting_answers':
                budget()
                supply_answers(store,root,answers,token=os.environ.get('UNDRIFT_REVIEW_TOKEN'))
                store.record_event('evaluation.answers_supplied',actor='evaluation:fixture-v1',subject=('run',root),
                    metadata={'run_id':root,'fixture_sha256':protocol['answer_fixture_sha256'],'slot':slot['slot']})
                await message(store,slot,root)
                measured=reliability.score(store,root,truth,evidence_kind='real_cominty')
                measured.update(slot=slot['slot'],database=slot['database'],database_id=slot['database_id'])
                measured['message_count']=sum(a['slot']==slot['slot'] for a in attempts)
                results[-1]=measured; persist()
            check_trial(measured,domain_unchanged=domain(store)==slot['domain'])
            assert_frozen(protocol,plan)
            print(f"Evaluation {slot['slot']}/5: status={measured['status']} reconciled={measured['reconciled']} cost={measured['reported_cost']}",flush=True)
        report['state']='completed'
    except BaseException as exc:
        report['state']='stopped'
        report['stop_diagnostic']=stop_diagnostic(exc)
        report['stop_reason']=report['stop_diagnostic'].get('message') or type(exc).__name__
        # Preserve a partially executed conversation even if a safety/provider check stopped it.
        for slot in plan['slots'] if 'truth' in locals() else []:
            store=Store(ROOT/slot['database'])
            for conv in clarification.listing(store):
                item=reliability.score(store,conv['id'],truth,evidence_kind='real_cominty' if any(a.get('run_id')==conv['id'] for a in attempts) else 'local_preflight_failure')
                item.update(slot=slot['slot'],database=slot['database'],database_id=slot['database_id'])
                item['message_count']=sum(a['slot']==slot['slot'] for a in attempts)
                existing=next((i for i,r in enumerate(results) if r['root_run_id']==conv['id']),None)
                if existing is None: results.append(item)
                else: results[existing]=item
    finally:
        report['protected_historical_files_unchanged']=all(file_hash(ROOT/p)==v for p,v in plan['protected_files'].items())
        persist()
    print(json.dumps({'state':report['state'],'stop_reason':report['stop_reason'],'aggregate':report['aggregate']},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','freeze','recover-preflight','execute'])
    parser.add_argument('--ui-dir',type=Path)
    parser.add_argument('--paid',action='store_true')
    parser.add_argument('--series',required=True,help='Fresh versioned study identity, e.g. reliability-v2; never reuse a paid series')
    args=parser.parse_args()
    try:
        select_series(args.series)
        if args.action=='prepare': prepare(args.ui_dir)
        elif args.action=='freeze': asyncio.run(freeze())
        elif args.action=='recover-preflight': asyncio.run(recover_preflight())
        else:
            if not args.paid: parser.error('execute requires explicit --paid authorization')
            asyncio.run(execute())
    except BaseException as exc:
        print('Evaluation stopped:',type(exc).__name__,'(inspect private evidence; no retry)')
        raise SystemExit(1) from None
