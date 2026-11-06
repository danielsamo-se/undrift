import asyncio
import json
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from mcp import Client

import fixtures
import readmodel
import server
import workflow
from conftest import analysis_run
from test_findings import args, finalize, verdict, old_store
from test_readmodel import client_for


@pytest.mark.parametrize('status', ['open', 'failed', 'incomplete', 'interrupted'])
def test_provisional_cannot_replace_completed_conclusion(changed, tmp_path, status):
    completed, _ = analysis_run(changed, ['doc-001'])
    finalize(changed, completed)
    retry, token = analysis_run(changed, ['doc-001'])
    payload = args(retry, token)
    payload['classification'] = 'UNRELATED'
    workflow.record_finding(changed, **payload)
    if status != 'open':
        workflow.close_run(changed, retry.id, status=status, actor='test')
    doc = readmodel.impact_snapshot(changed, retry.policy_id, tmp_path)['documents'][0]
    assert doc['finding']['run_id'] == completed.id
    assert doc['finding']['verified_conclusion']
    assert doc['provisional_findings'][0]['run_id'] == retry.id


@pytest.mark.parametrize('original', [
    'Items can be returned within 60 days and shipping costs 5 EUR.',
    'Refunds are processed within 60 days.',
    'Items can be returned within 60 days if unused.',
])
def test_ambiguous_correction_fails_closed(original):
    violations, _ = workflow.check_guardrails(original,
        'Items purchased on or after 2026-11-04 can be returned within 30 days.',
        current_days=30, stale=workflow.days_pattern({60}), evidence=original,
        effective_from=fixtures.SHIFTLY_RETURNS_CHANGE['effective_from'])
    assert violations


def test_unknown_missing_version_is_normalized(changed):
    run, _ = analysis_run(changed, ['doc-001'], retrieve=False)
    reply = {'policy': {'id': run.policy_id, 'version': None, 'voluntary_return_days': None},
        'documents': {'doc-001': {'category': 'UNKNOWN', 'evidence': '', 'reason': 'Read failed.'}},
        'changes_made': False}
    result = finalize(changed, run, '```json\n' + json.dumps(reply) + '\n```')
    assert result['classifications'] == {'doc-001': 'UNKNOWN'}
    assert changed.run(run.id).status == 'incomplete'
    assert not changed.proposals()


def test_success_failure_terminal_race(changed, monkeypatch):
    run, _ = analysis_run(changed, ['doc-001'])
    barrier = Barrier(2)
    original = changed.run
    def synchronized(rid, conn=None):
        value = original(rid, conn)
        if conn is None and value.status == 'open':
            barrier.wait(timeout=5)
        return value
    monkeypatch.setattr(changed, 'run', synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda status: finalize(changed, run, status=status), ['success', 'failed']))
    assert len(outcomes) == 2
    assert len(changed.events(type_prefix='agent.run_finished')) == 1
    assert bool(changed.proposals()) == (original(run.id).status == 'completed')


def test_impact_uses_one_snapshot_during_new_finding(changed, tmp_path, monkeypatch):
    original = changed.findings
    inserted = False
    def insert(*a, **kw):
        nonlocal inserted
        if not a and not kw and not inserted:
            inserted = True
            run, token = analysis_run(changed, ['doc-001'])
            workflow.record_finding(changed, **args(run, token))
        return original(*a, **kw)
    monkeypatch.setattr(changed, 'findings', insert)
    snap = readmodel.impact_snapshot(changed, fixtures.POLICY_ID, tmp_path)
    assert snap['documents'][0]['finding'] is None
    assert changed.findings()


@pytest.mark.parametrize('execution,status', [('failed', 'failed'), ('cancelled', 'interrupted')])
def test_legacy_failed_finish_is_not_completed(tmp_path, execution, status):
    s = old_store(tmp_path)
    s.record_event('agent.run_started', actor='test')
    s.record_event('agent.run_finished', actor='test', status='error',
        metadata={'message_id': 'old-message', 'status': execution}, error='Remote failure')
    s.init()
    assert s.runs()[0].status == status
    assert s.runs()[0].error == 'Remote failure'


def test_rejected_patch_retains_structured_diagnostics(changed):
    run, _ = analysis_run(changed, ['doc-001'])
    reply = workflow.parse_verdict(verdict(run))
    reply['documents']['doc-001']['proposed_content'] += ' Free gifts for everyone.'
    result = finalize(changed, run, '```json\n' + json.dumps(reply) + '\n```')
    error = result['errors'][0]
    assert error['document_id'] == 'doc-001'
    assert error['code'] == 'guardrail_violation'
    assert any(v['rule'] == 'unsupported_added_claim' for v in error['details']['violations'])
    event = changed.events(type_prefix='agent.reply_rejected')[0]
    assert all(event.metadata[k] == v for k, v in error.items())
    assert not changed.proposals()


def test_mcp_to_finalization_to_api_conclusion(changed, tmp_path, findings_tool):
    run, token = analysis_run(changed, ['doc-001', 'doc-004'], retrieve=False)
    async def submit():
        async with Client(server.mcp) as client:
            credentials = {'run_id': run.id, 'run_token': token}
            assert not (await client.call_tool('get_policy', {'policy_id': run.policy_id, **credentials})).is_error
            for did in run.document_ids:
                assert not (await client.call_tool('get_document', {'document_id': did, **credentials})).is_error
                payload = args(run, token, did)
                payload.pop('actor')
                assert not (await client.call_tool('record_finding', payload)).is_error
    asyncio.run(submit())
    api = client_for(changed, tmp_path)
    url = f'/api/policies/{run.policy_id}/impact'
    before = api.get(url).json()
    assert not before['documents'][0]['finding']['verified_conclusion']
    assert not finalize(changed, run)['errors']
    doc = api.get(url).json()['documents'][0]
    assert doc['finding']['verified_conclusion'] and doc['finding']['source'] == 'mcp'
    assert doc['proposals'][0]['status'] == 'pending_review'
    # Execute the actual UI consistency code on the API payload, without a browser,
    # transpiler install or fixture translation. Node 24 strips TypeScript in memory.
    root = Path(__file__).resolve().parents[1]
    node = root / '.tools' / 'node' / 'node.exe'
    if not node.exists():
        pytest.skip('portable Node unavailable; API integration assertions passed')
    javascript = r"""
const fs=require('fs'),vm=require('vm'),{stripTypeScriptTypes}=require('module');
function load(name,classification={}) {
 let js=stripTypeScriptTypes(fs.readFileSync('ui/src/lib/'+name+'.ts','utf8'),{mode:'transform'});
 js=js.replace(/import \{([^}]+)\} from "[^\"]+";/g,'const {$1} = classification;').replace(/\bexport /g,'');
 return vm.runInNewContext(js+';({'+(name==='board'?'consistency':'STATUS,isStale,statusOf')+'})',{classification});
}
const board=load('board',load('classification'));
const snapshots=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(snapshots.map(s=>board.consistency(s).consistent)));
"""
    result = subprocess.run([str(node), '-e', javascript], cwd=root, input=json.dumps([before, api.get(url).json()]),
        text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == [0, 1]



def test_failure_finalizer_observing_stale_open_state_is_idempotent(changed, monkeypatch):
    run, _ = analysis_run(changed, ['doc-001'])
    original = changed.run
    triggered = False
    def stale_read(rid, conn=None):
        nonlocal triggered
        value = original(rid, conn)
        if conn is None and not triggered:
            triggered = True
            assert not finalize(changed, run)['errors']
        return value
    monkeypatch.setattr(changed, 'run', stale_read)
    finalize(changed, run, status='failed')
    assert original(run.id).status == 'completed'
    assert len(changed.events(type_prefix='agent.run_finished')) == 1
    assert len(changed.proposals()) == 1
