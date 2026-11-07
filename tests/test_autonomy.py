"""Isolated policy delegation and exact local correction; no paid or remote calls."""
import json
import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient

import api
import autonomy
from discovery_seed import create_demo
from store import Store
import workflow as w
import server
import discovery
from mcp import Client

FIX='Items purchased on or after 2026-11-04 can be returned within 30 days.'


@pytest.fixture
def demo(tmp_path):return create_demo(tmp_path/'autonomy.db')


def enable(s,token,channel='help-center',limit=1,version=0,mode='bounded_auto'):
    return autonomy.configure(s,channel,token=token,expected_version=version,mode=mode,max_per_run=limit,note='Delegate simple local return-window corrections.')


def begin(s,ids=('help-001',)):
    r,t=w.open_run(s,'shiftly-returns',list(ids),actor='test',agent_id='impact',metadata={'discovery':True,'max_steps':40})
    for kind,did in [('policy',r.policy_id),*[('document',did)for did in ids]]:
        w.retrieve_for_run(s,run_id=r.id,run_token=t,kind=kind,subject_id=did,actor='mcp:test')
    return r,t


def finish(s,r,t,*,corrections=None,categories=None,status='success',finalize=True):
    entries={}
    for did in r.document_ids:
        doc=s.document(did);category=(categories or {}).get(did,'OUTDATED')
        correction=(corrections or {}).get(did,FIX+' '+' '.join(w.split_sentences(doc.content)[1:]))
        entry={'category':category,'document_version':doc.version,'evidence':doc.content,'reason':'Current return policy differs.'}
        if category=='OUTDATED':entry['proposed_content']=correction.strip()
        w.record_finding(s,run_id=r.id,run_token=t,policy_id=r.policy_id,policy_version=r.policy_version,
            document_id=did,document_version=doc.version,classification=category,evidence=entry['evidence'],
            reason=entry['reason'],actor='mcp:test',proposed_content=entry.get('proposed_content'))
        entries[did]=entry
    reply=json.dumps({'policy':{'id':r.policy_id,'version':r.policy_version,'voluntary_return_days':30},
                      'documents':entries,'changes_made':False})
    reply='```json\n'+reply+'\n```'
    return w.ingest_agent_reply(s,reply,run_id=r.id,execution_status=status,actor='test',provenance={}) if finalize else reply


def test_defaults_and_hard_channel_ceilings(demo,token):
    with demo.read() as c:items={p['channel_id']:p for p in autonomy.listing(demo,c)}
    assert items['help-center']['effective_mode']==items['support-snippets']['effective_mode']=='review_required'
    assert items['website']['effective_mode']=='pull_request'
    assert items['legal']['effective_mode']==items['crm']['effective_mode']=='manual_only'
    for channel in ['website','legal','crm','order-email','bot-knowledge']:
        with pytest.raises(w.GuardrailViolation):enable(demo,token,channel)
    with pytest.raises(w.Unauthorized):enable(demo,'wrong')


@pytest.mark.parametrize('channel,did',[('help-center','help-001'),('support-snippets','support-001')])
def test_certified_single_sentence_correction_auto_applies_without_faking_human_approval(demo,token,channel,did):
    enable(demo,token,channel);r,t=begin(demo,[did]);result=finish(demo,r,t)
    [p]=demo.proposals();assert p.status=='applied' and p.reviewed_by is None and p.reviewed_at is None
    assert demo.document(did).version==2 and demo.document(did).content.startswith(FIX)
    assert result['auto_applications'][0]['proposal_id']==p.id
    with demo.read() as c:
        receipt=autonomy.receipt(demo,p.id,c)
        assert receipt['run_id']==r.id and receipt['policy_version']==1 and receipt['applied_version']==2
        assert receipt['original_sha256']==w._sha(p.original_content) and receipt['proposed_sha256']==w._sha(p.proposed_content)
    assert demo.events(type_prefix='proposal.approved')==[]
    [event]=demo.events(type_prefix='proposal.auto_applied')
    assert event.actor==autonomy.ACTOR and event.metadata['human_patch_approval'] is False


@pytest.mark.parametrize('when',['disabled','enabled_after_start','revoked','revised_bound'])
def test_delegation_is_opt_in_pinned_and_revocable_for_inflight_runs(demo,token,when):
    if when in {'revoked','revised_bound'}:enable(demo,token)
    r,t=begin(demo)
    if when=='enabled_after_start':enable(demo,token)
    if when=='revoked':enable(demo,token,version=1,mode='review_required')
    if when=='revised_bound':enable(demo,token,version=1,limit=2)
    finish(demo,r,t)
    assert demo.proposals()[0].status=='pending_review' and demo.document('help-001').version==1
    assert demo.events(type_prefix='autonomy.application_denied')


def test_unknowns_and_partial_reconciliation_require_review_for_all_auto_candidates(demo,token):
    enable(demo,token);r,t=begin(demo,['help-001','help-002'])
    finish(demo,r,t,categories={'help-002':'UNKNOWN'})
    assert demo.run(r.id).status=='incomplete' and demo.proposals()[0].status=='pending_review'
    assert demo.document('help-001').version==1


def test_policy_sensitive_additions_and_semantic_paraphrases_stay_pending(demo,token):
    enable(demo,token)
    r,t=begin(demo);finish(demo,r,t,corrections={'help-001':FIX+' Purchases made before 2026-11-04 keep the return period promised at the time of purchase. Keep your order number handy.'})
    assert demo.proposals()[0].status=='pending_review'
    [event]=demo.events(type_prefix='autonomy.application_denied')
    assert 'policy_sensitive_exception' in event.metadata['blockers']


def test_concurrent_policy_changes_have_one_winner_and_stale_audit(demo,token):
    def change(_):
        try:return enable(demo,token)['version']
        except w.Conflict:return 'conflict'
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(change,range(2)))
    assert sorted(map(str,results))==['1','conflict']
    assert len(demo.events(type_prefix='autonomy.policy_changed'))==1
    assert len(demo.events(type_prefix='autonomy.configuration_denied'))==1


def test_website_cannot_use_direct_local_apply_even_after_human_approval(demo,token):
    r,t=begin(demo,['doc-001']);finish(demo,r,t)
    [p]=demo.proposals();w.approve(demo,p.id,token=token)
    with pytest.raises(w.GuardrailViolation,match='GitHub PR'):w.apply(demo,p.id,token=token)
    assert demo.document('doc-001').version==1 and demo.proposal(p.id).status=='approved'


def test_configuration_api_authentication_versions_and_projection(demo,token):
    client=TestClient(api.build_app(demo,ui_dir=None),base_url='http://127.0.0.1',client=('127.0.0.1',1))
    body={'expected_version':0,'mode':'bounded_auto','max_per_run':1,'note':'Explicit local delegation.'}
    assert client.post('/api/autonomy/help-center',json=body).status_code==401
    assert client.post('/api/autonomy/help-center',json=body,headers={'Authorization':'Bearer '+token}).status_code==200
    assert client.post('/api/autonomy/help-center',json=body,headers={'Authorization':'Bearer '+token}).status_code==409
    response=client.get('/api/autonomy').json()
    assert response['scope']=='local_database_only' and response['global_run_limit']==3


@pytest.mark.parametrize('change',['document','policy','channel'])
def test_version_or_scope_changes_cannot_auto_apply(demo,token,change):
    enable(demo,token);r,t=begin(demo);reply=finish(demo,r,t,finalize=False)
    with demo.write() as c:
        if change=='document':
            c.execute("INSERT INTO document_versions SELECT document_id,2,content,created_at,created_by FROM document_versions WHERE document_id='help-001'")
            c.execute("UPDATE documents SET current_version=2 WHERE id='help-001'")
        elif change=='policy':c.execute("UPDATE policies SET current_version=1 WHERE id='shiftly-returns'")
        else:c.execute("UPDATE documents SET channel_id='crm' WHERE id='help-001'")
    result=w.ingest_agent_reply(demo,reply,run_id=r.id,execution_status='success',actor='test',provenance={})
    assert not result.get('auto_applications') and not demo.events(type_prefix='proposal.auto_applied')
    assert all(p.status=='pending_review' for p in demo.proposals())


def test_conflicting_verified_findings_block_auto_application(demo,token):
    old,ot=begin(demo);finish(demo,old,ot,categories={'help-001':'UNRELATED'})
    enable(demo,token);r,t=begin(demo);finish(demo,r,t)
    assert demo.proposals()[0].status=='pending_review'
    assert 'conflicting_verified_evidence' in demo.events(type_prefix='autonomy.application_denied')[0].metadata['blockers']


def test_active_overlapping_analysis_blocks_auto_application(demo,token):
    enable(demo,token);begin(demo);r,t=begin(demo);finish(demo,r,t)
    assert demo.proposals()[0].status=='pending_review'
    assert 'concurrent_analysis' in demo.events(type_prefix='autonomy.application_denied')[0].metadata['blockers']


@pytest.mark.parametrize('status',['failed','cancelled'])
def test_failed_or_interrupted_analysis_never_auto_applies(demo,token,status):
    enable(demo,token);r,t=begin(demo);finish(demo,r,t,status=status)
    assert not demo.proposals() and demo.document('help-001').version==1


def test_unresolved_native_question_blocks_even_otherwise_completed_run(demo,token):
    import clarification as cq
    from test_clarification import native, pause
    enable(demo,token);r,t=begin(demo,['help-001','help-002'])
    # Real native capture and persisted answer state; no remote SDK execution.
    cq.create(demo,r.id,'connection',b'fixed instructions');cq.bind(demo,r.id,'thread','message')
    reply=finish(demo,r,t,categories={'help-002':'UNRELATED'},finalize=False)
    pause(demo,r,[native()])
    with demo.write() as c:
        # Simulate a conflicting finalizer trying to certify the paused phase.
        # Production capture revokes the token and does not finalize successfully.
        c.execute("UPDATE agent_runs SET status='open' WHERE id=?",(r.id,))
    w.ingest_agent_reply(demo,reply,run_id=r.id,execution_status='success',actor='test',provenance={})
    assert demo.proposals()[0].status=='pending_review'
    assert 'unresolved_clarification' in demo.events(type_prefix='autonomy.application_denied')[0].metadata['blockers']


def add_eligible_documents(demo,count=4):
    with demo.write() as c:
        for n in range(count):
            did=f'local-{n}'
            c.execute('INSERT INTO documents VALUES (?,?,?,1)',(did,'help-center',f'Local help {n}'))
            c.execute('INSERT INTO document_versions VALUES (?,1,?,?,?)',(did,'Items can be returned within 60 days.','2026-11-07','test'))
    return [f'local-{n}' for n in range(count)]


@pytest.mark.parametrize('limit,expected',[(1,1),(3,3)])
def test_channel_and_global_run_bounds_with_atomic_receipts(demo,token,limit,expected):
    ids=add_eligible_documents(demo);enable(demo,token,limit=limit);r,t=begin(demo,ids);finish(demo,r,t)
    assert sum(p.status=='applied' for p in demo.proposals())==expected
    assert sum(demo.document(d).version==2 for d in ids)==expected
    assert len(demo.events(type_prefix='proposal.auto_applied'))==expected


def test_concurrent_finalizers_apply_at_most_once(demo,token):
    enable(demo,token);r,t=begin(demo);reply=finish(demo,r,t,finalize=False)
    def finalize(_):return w.ingest_agent_reply(demo,reply,run_id=r.id,execution_status='success',actor='test',provenance={})
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(finalize,range(2)))
    assert sum(bool(x.get('auto_applications')) for x in results)==1
    assert demo.document('help-001').version==2 and len(demo.proposals())==1
    assert len(demo.events(type_prefix='proposal.auto_applied'))==1


def test_application_receipt_and_grant_are_immutable(demo,token):
    enable(demo,token);r,t=begin(demo);finish(demo,r,t)
    for table in ['run_autonomy_grants','autonomy_applications']:
        for statement in [f'DELETE FROM {table}',f'UPDATE {table} SET policy_version=999']:
            with pytest.raises(sqlite3.IntegrityError,match='immutable'),demo.write() as c:c.execute(statement)
    assert demo.document('help-001').version==2


def test_failed_receipt_insert_rolls_back_document_proposals_certification_and_success_audit(demo,token):
    enable(demo,token);r,t=begin(demo);reply=finish(demo,r,t,finalize=False)
    with demo.write() as c:c.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON autonomy_applications BEGIN SELECT RAISE(ABORT,'receipt unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError,match='receipt unavailable'):
        w.ingest_agent_reply(demo,reply,run_id=r.id,execution_status='success',actor='test',provenance={})
    assert demo.document('help-001').version==1 and not demo.proposals()
    assert demo.run(r.id).status=='open' and len(demo.findings())==1
    assert not demo.events(type_prefix='proposal.auto_applied') and not demo.events(type_prefix='agent.run_finished')


@pytest.mark.parametrize('did',['legal-001','legal-002','crm-001','crm-004'])
def test_protected_channels_never_create_or_apply_a_correction(demo,token,did):
    r,t=begin(demo,[did]);original=demo.document(did)
    with pytest.raises(w.GuardrailViolation):finish(demo,r,t)
    assert demo.document(did)==original and not demo.proposals()


def test_sensitive_original_and_semantic_edits_require_review(demo,token):
    enable(demo,token)
    with demo.write() as c:
        c.execute("UPDATE document_versions SET content='Items can be returned within 60 days. Your statutory 14-day right of withdrawal is unchanged.' WHERE document_id='help-001'")
    r,t=begin(demo);finish(demo,r,t)
    assert demo.proposals()[0].status=='pending_review'
    assert 'policy_sensitive_exception' in demo.events(type_prefix='autonomy.application_denied')[0].metadata['blockers']


def _downgrade_to_v5(s):
    with s.write() as c:
        for name in ['immutable_autonomy_grant_update','immutable_autonomy_grant_delete',
                     'immutable_autonomy_receipt_update','immutable_autonomy_receipt_delete']:
            c.execute('DROP TRIGGER '+name)
        for table in ['autonomy_applications','run_autonomy_grants','channel_autonomy']:c.execute('DROP TABLE '+table)
        c.execute('PRAGMA user_version=5')
    conn=sqlite3.connect(s.path);conn.execute('PRAGMA wal_checkpoint(TRUNCATE)');conn.close()


def test_v5_database_is_not_rewritten_by_plain_startup(demo,token):
    r,t=begin(demo);finish(demo,r,t);_downgrade_to_v5(demo)
    before=demo.path.read_bytes()
    demo.init()
    conn=sqlite3.connect(demo.path);conn.execute('PRAGMA wal_checkpoint(TRUNCATE)');conn.close()
    assert demo.path.read_bytes()==before
    with demo.read() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==5 and not autonomy.installed(c)
        items={p['channel_id']:p for p in autonomy.listing(demo,c)}
    assert items['help-center']['effective_mode']=='review_required' and not items['help-center']['schema_available']
    with pytest.raises(w.Conflict,match='explicitly migrated'):enable(demo,token)


def test_v5_migration_is_explicit_additive_and_does_not_backfill_authority(demo,token):
    r,t=begin(demo);finish(demo,r,t);_downgrade_to_v5(demo)
    original=(demo.identity(),demo.proposals(),demo.findings(),demo.runs(),demo.events(),demo.documents())
    demo.init(enable_autonomy=True)
    assert (demo.identity(),demo.proposals(),demo.findings(),demo.runs(),demo.events(),demo.documents())==original
    with demo.read() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==6
        assert c.execute('SELECT COUNT(*) FROM run_autonomy_grants').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM channel_autonomy').fetchone()[0]==0
        assert not autonomy.decision(demo,demo.proposals()[0],c)['eligible']


def test_cli_schema_opt_in_requires_review_token(demo,token,monkeypatch,capsys):
    import cli
    _downgrade_to_v5(demo)
    monkeypatch.setenv('UNDRIFT_DB',str(demo.path))
    monkeypatch.setattr('sys.argv',['cli.py','enable-autonomy'])
    monkeypatch.setenv('UNDRIFT_REVIEW_TOKEN','x'*8)
    monkeypatch.setattr(w,'authorize',lambda *a,**k:(_ for _ in ()).throw(w.Unauthorized('missing or invalid review token')))
    assert cli.main()==1
    with demo.read() as c:assert c.execute('PRAGMA user_version').fetchone()[0]==5
    monkeypatch.undo()
    monkeypatch.setenv('UNDRIFT_DB',str(demo.path));monkeypatch.setenv('UNDRIFT_REVIEW_TOKEN',token)
    monkeypatch.setattr('sys.argv',['cli.py','enable-autonomy'])
    assert cli.main()==0
    with demo.read() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==6 and autonomy.installed(c)
        assert c.execute('SELECT COUNT(*) FROM channel_autonomy').fetchone()[0]==0
    assert demo.events(type_prefix='autonomy.schema_enabled')[-1].metadata['delegations_granted']==0


def test_delegation_reason_cannot_persist_run_credentials(demo,token):
    r,secret=begin(demo)
    with pytest.raises(w.WorkflowError):autonomy.configure(demo,'help-center',token=token,expected_version=0,mode='bounded_auto',note=secret)
    with demo.read() as c:assert secret not in '\n'.join(c.iterdump())


def test_mcp_finding_to_certification_local_version_and_api_receipt(demo,token):
    enable(demo,token);r,t=begin(demo)
    old=server._state['store'];server.use_store(demo);server.enable_findings_tool(None)
    doc=demo.document('help-001');corrected=FIX+' Keep your order number handy.'
    async def submit():
        async with Client(server.mcp) as client:
            tools={x.name for x in (await client.list_tools()).tools}
            assert not any('autonomy' in x for x in tools)
            result=await client.call_tool('record_finding',dict(run_id=r.id,run_token=t,policy_id=r.policy_id,
                policy_version=r.policy_version,document_id=doc.id,document_version=doc.version,
                classification='OUTDATED',evidence=doc.content,reason='Current return policy differs.',proposed_content=corrected))
            assert not result.is_error
    try:asyncio.run(submit())
    finally:
        server.mcp.remove_tool('record_finding');server._state['findings_tool']=False;server.use_store(old)
    finish(demo,r,t)
    client=TestClient(api.build_app(demo,ui_dir=None),base_url='http://127.0.0.1',client=('127.0.0.1',1))
    [p]=demo.proposals();detail=client.get(f'/api/proposals/{p.id}').json()
    assert detail['autonomy']['receipt']['run_id']==r.id
    assert detail['proposal']['reviewed_by'] is None and detail['proposal']['status']=='applied'
    snapshot=client.get('/api/policies/shiftly-returns/impact').json()
    projected=next(d for d in snapshot['documents'] if d['id']==doc.id)
    assert projected['version']==2 and not projected['finding']['current']
    assert [x['delegated_application'] for x in projected['proposals']]==[True]
    new,nt=begin(demo)
    result=discovery.search_documents(demo,run_id=new.id,run_token=nt,query='60',channel_id='help-center')
    assert all(x['document_id']!=doc.id for x in result['results'])


def test_human_application_is_not_marked_delegated_and_auto_applied_cannot_enter_publishing(demo,token):
    r,t=begin(demo);finish(demo,r,t)
    [p]=demo.proposals();assert p.status=='pending_review'
    w.approve(demo,p.id,token=token);w.apply(demo,p.id,token=token)
    client=TestClient(api.build_app(demo,ui_dir=None),base_url='http://127.0.0.1',client=('127.0.0.1',1))
    doc=next(d for d in client.get('/api/policies/shiftly-returns/impact').json()['documents'] if d['id']=='help-001')
    assert [(x['status'],x['delegated_application']) for x in doc['proposals']]==[('applied',False)]
    assert client.get(f'/api/proposals/{p.id}').json()['autonomy']['receipt'] is None
    [did]=add_eligible_documents(demo,1)
    enable(demo,token);r2,t2=begin(demo,(did,));finish(demo,r2,t2)
    auto=next(x for x in demo.proposals() if x.document_id==did)
    assert auto.status=='applied' and auto.reviewed_by is None
    for action in (w.approve,w.reject):
        with pytest.raises(w.Conflict):action(demo,auto.id,token=token)
    assert demo.proposal(auto.id).reviewed_by is None
