"""Offline regressions for the second discovery contract. No model calls or live DBs.

These verify handling of labelled inputs, not that a language model will choose the
right label. That distinction must be measured in the next authorized development run.
"""
from concurrent.futures import ThreadPoolExecutor
import asyncio
import json

import httpx
import pytest
from starlette.testclient import TestClient
from mcp import Client

import api
import discovery
from discovery_seed import create_demo
import publishing
import readmodel
import server
import workflow
from test_discovery import CORRECTION, TRUTH, open_run, payload, reply
from test_workflow import edit_document_externally


@pytest.fixture
def demo(tmp_path):
    return create_demo(tmp_path / 'discovery.db')


def finalize(store, run, text=None, status='success'):
    return workflow.ingest_agent_reply(store, text or reply(store, run), actor='agent:test',
                                       provenance={}, run_id=run.id, execution_status=status)


@pytest.mark.parametrize('did', [did for did, category in TRUTH.items() if category == 'UNKNOWN'])
def test_contextual_unknown_needs_clarification_without_blocking_independent_correction(demo, did):
    run, token = open_run(demo, ['doc-001', did], retrieve=True)
    workflow.record_finding(demo, **payload(demo, run, token, proposed_content=CORRECTION))
    workflow.record_finding(demo, **payload(demo, run, token, did))
    result = finalize(demo, run)
    assert demo.run(run.id).status == 'incomplete'
    assert len(result['proposals']) == 1 and demo.proposals()[0].document_id == 'doc-001'
    assert result['document_outcomes'][did]['action'] == 'needs_clarification'
    assert demo.run(run.id).metadata['verified_document_ids'] == ['doc-001']
    snapshot = readmodel.impact_snapshot(demo, run.policy_id)
    docs = {d['id']: d for d in snapshot['documents']}
    assert docs['doc-001']['finding']['verified_conclusion']
    assert docs['doc-001']['provisional_findings'] == []
    assert not docs[did]['finding']['verified_conclusion']
    assert docs[did]['finding']['action'] == 'needs_clarification'
    assert not demo.proposals(document_id=did)


@pytest.mark.parametrize('did', [did for did, category in TRUTH.items() if category == 'UNRELATED'])
def test_independent_subject_remains_verified_unrelated_without_correction(demo, did):
    run, token = open_run(demo, [did], retrieve=True)
    workflow.record_finding(demo, **payload(demo, run, token, did))
    result = finalize(demo, run)
    assert not result['errors'] and demo.run(run.id).status == 'completed'
    assert result['document_outcomes'][did]['action'] == 'no_change'
    assert demo.proposals() == []
    doc = next(d for d in readmodel.impact_snapshot(demo, run.policy_id)['documents'] if d['id'] == did)
    assert doc['finding']['category'] == 'UNRELATED' and doc['finding']['verified_conclusion']


def test_full_rubric_allows_six_safe_proposals_five_clarifications_and_manual_crm(demo):
    run, token = open_run(demo, retrieve=True)
    for did in run.document_ids:
        doc = demo.document(did)
        changes = {}
        if TRUTH[did] == 'OUTDATED' and doc.channel not in workflow.MANUAL_ONLY_CHANNELS:
            rest = workflow.split_sentences(doc.content)[1:]
            changes['proposed_content'] = CORRECTION + (' ' + ' '.join(rest) if rest else '')
        workflow.record_finding(demo, **payload(demo, run, token, did, **changes))
    result = finalize(demo, run)
    assert len(result['proposals']) == 6
    outcomes = result['document_outcomes']
    assert {did for did, item in outcomes.items() if item['action'] == 'needs_clarification'} == {
        did for did, category in TRUTH.items() if category == 'UNKNOWN'}
    assert outcomes['crm-004']['action'] == 'manual_only'
    assert all(demo.document(p.document_id).channel not in workflow.MANUAL_ONLY_CHANNELS for p in demo.proposals())
    client = TestClient(api.build_app(demo, ui_dir=None), base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    response = client.get('/api/policies/shiftly-returns/impact')
    assert response.status_code == 200
    snapshot = response.json()
    assert sum(d['finding']['verified_conclusion'] for d in snapshot['documents']) == 19
    assert len(client.get(f'/api/runs/{run.id}').json()['run']['document_outcomes']) == 24


def test_failed_retrieval_is_local_but_failed_policy_never_authorizes_proposals(demo):
    run, token = open_run(demo, ['doc-001', 'help-002'])
    for kind, did in [('policy', run.policy_id), ('document', 'doc-001')]:
        workflow.retrieve_for_run(demo, run_id=run.id, run_token=token, kind=kind, subject_id=did, actor='mcp:test')
    workflow.record_finding(demo, **payload(demo, run, token, proposed_content=CORRECTION))
    missing = payload(demo, run, token, 'help-002')
    missing.update(document_version=None, evidence='', reason='Retrieval failed; which terms apply?')
    workflow.record_finding(demo, **missing)
    final = workflow.parse_verdict(reply(demo, run))
    final['documents']['help-002'].update(document_version=None, evidence='', reason=missing['reason'])
    result = finalize(demo, run, '```json\n' + json.dumps(final) + '\n```')
    assert len(result['proposals']) == 1
    assert not demo.run(run.id).metadata['retrieval']['complete']
    other, other_token = open_run(demo, ['web-003'])
    unknown = payload(demo, other, other_token, 'web-003')
    unknown.update(classification='UNKNOWN', document_version=None, policy_version=None, evidence='')
    workflow.record_finding(demo, **unknown)
    final = {'policy': {'id': other.policy_id, 'version': None, 'voluntary_return_days': None},
             'documents': {'web-003': {'category': 'UNKNOWN', 'document_version': None,
                 'evidence': '', 'reason': unknown['reason']}}, 'changes_made': False}
    result = finalize(demo, other, '```json\n' + json.dumps(final) + '\n```')
    assert not result['proposals'] and not demo.run(other.id).metadata['verified_document_ids']


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_failed_execution_never_certifies_partial_findings_or_creates_proposals(demo, status):
    run, token = open_run(demo, ['doc-001', 'help-002'], retrieve=True)
    workflow.record_finding(demo, **payload(demo, run, token, proposed_content=CORRECTION))
    workflow.record_finding(demo, **payload(demo, run, token, 'help-002'))
    assert not finalize(demo, run, status=status)['proposals']
    assert not demo.run(run.id).metadata.get('verified_document_ids')
    assert all(not d['finding']['verified_conclusion'] for d in readmodel.impact_snapshot(demo, run.policy_id)['documents'] if d['finding'])


def test_contradictory_final_output_cannot_use_partial_finalization(demo):
    run, token = open_run(demo, ['doc-001', 'help-002'], retrieve=True)
    workflow.record_finding(demo, **payload(demo, run, token, proposed_content=CORRECTION))
    workflow.record_finding(demo, **payload(demo, run, token, 'help-002'))
    final = workflow.parse_verdict(reply(demo, run))
    final['documents']['help-002']['category'] = 'UNRELATED'
    result = finalize(demo, run, '```json\n' + json.dumps(final) + '\n```')
    assert result['errors'] and not result['proposals']
    assert not demo.run(run.id).metadata.get('verified_document_ids')


def test_concurrent_partial_finalization_commits_one_proposal_and_certificate(demo):
    run, token = open_run(demo, ['doc-001', 'help-002'], retrieve=True)
    workflow.record_finding(demo, **payload(demo, run, token, proposed_content=CORRECTION))
    workflow.record_finding(demo, **payload(demo, run, token, 'help-002'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: finalize(demo, run), range(2)))
    assert sum(len(r['proposals']) for r in results) == 1
    assert len(demo.proposals()) == 1 and len(demo.events(type_prefix='agent.run_finished')) == 1
    assert demo.run(run.id).metadata['verified_document_ids'] == ['doc-001']


@pytest.mark.parametrize('later_status', ['open', 'incomplete', 'failed', 'interrupted'])
def test_unreconciled_later_run_cannot_override_an_individually_verified_partial_conclusion(demo, later_status):
    first, token = open_run(demo, ['web-002', 'help-002'], retrieve=True)
    for did in first.document_ids:
        workflow.record_finding(demo, **payload(demo, first, token, did))
    finalize(demo, first)
    assert demo.run(first.id).status == 'incomplete'
    later, secret = open_run(demo, ['web-002'], retrieve=True)
    args = payload(demo, later, secret, 'web-002'); args['classification'] = 'OUTDATED'
    workflow.record_finding(demo, **args)
    if later_status != 'open': workflow.close_run(demo, later.id, status=later_status, actor='test')
    snapshot = readmodel.impact_snapshot(demo, first.policy_id)
    doc = next(d for d in snapshot['documents'] if d['id'] == 'web-002')
    assert doc['finding']['run_id'] == first.id
    assert doc['finding']['category'] == 'UNRELATED' and doc['finding']['verified_conclusion']
    assert len(doc['provisional_findings']) == 1 and doc['provisional_findings'][0]['run_id'] == later.id


@pytest.mark.parametrize('did', ['legal-001', 'legal-002', 'crm-001', 'crm-002', 'crm-003', 'crm-004'])
def test_channel_protection_is_independent_of_text_category_and_actor(demo, did):
    # A trivially editable sentence defeats content-only protection; the channel must still win.
    edit_document_externally(demo, did, 'Items can be returned within 60 days.')
    run, token = open_run(demo, [did], retrieve=True)
    args = payload(demo, run, token, did, proposed_content=CORRECTION)
    args['classification'] = 'OUTDATED'
    with pytest.raises(workflow.GuardrailViolation, match='manual editing'):
        workflow.record_finding(demo, **args)
    with pytest.raises(workflow.GuardrailViolation):
        workflow.submit_proposal(demo, document_id=did, base_version=demo.document(did).version,
            policy_id=run.policy_id, policy_version=run.policy_version, proposed_content=CORRECTION,
            evidence=args['evidence'], reason='A human approved this.', actor='reviewer:spoofed')
    args.pop('proposed_content')
    workflow.record_finding(demo, **args)  # Reporting a potentially outdated record remains allowed.
    final = workflow.parse_verdict(reply(demo, run))
    final['documents'][did]['category'] = 'OUTDATED'
    result = finalize(demo, run, '```json\n' + json.dumps(final) + '\n```')
    assert not result['proposals'] and result['document_outcomes'][did]['action'] == 'manual_only'
    assert demo.document(did).version == 2


@pytest.mark.parametrize('channel', ['legal', 'crm', 'legal-terms', 'support-crm'])
def test_preexisting_proposal_cannot_bypass_protected_approval_apply_or_publishing(demo, token, channel):
    proposal = workflow.submit_proposal(demo, document_id='doc-001', base_version=1,
        policy_id='shiftly-returns', policy_version=2, proposed_content=CORRECTION,
        evidence=demo.document('doc-001').content, reason='Correction.', actor='agent:test')
    workflow.approve(demo, proposal.id, token=token)
    with demo.write() as conn:
        conn.execute('INSERT OR IGNORE INTO channels VALUES (?,?,?)', (channel, channel, 'internal'))
        conn.execute('UPDATE documents SET channel_id=? WHERE id=?', (channel, 'doc-001'))
    with pytest.raises(workflow.GuardrailViolation): workflow.apply(demo, proposal.id, token=token)
    with demo.write() as conn: conn.execute("UPDATE proposals SET status='pending_review' WHERE id=?", (proposal.id,))
    with pytest.raises(workflow.GuardrailViolation): workflow.approve(demo, proposal.id, token=token)
    with demo.write() as conn: conn.execute("UPDATE proposals SET status='approved' WHERE id=?", (proposal.id,))
    publisher = publishing.Publisher(demo, publishing.Target('example/shiftly'), journal=demo.path, github=object())
    with pytest.raises(workflow.GuardrailViolation): publisher.plan(proposal.id, token)
    workflow.reject(demo, proposal.id, token=token)  # Blocking must not trap historical proposals forever.
    assert demo.proposal(proposal.id).status == 'rejected' and demo.document('doc-001').version == 1


def strategy_run(demo):
    return workflow.open_run(demo, 'shiftly-returns', ['doc-001'], actor='test',
                             metadata={'discovery': True, 'discovery_strategy': 'search_first_v1'})


def test_catalog_only_does_not_satisfy_future_search_contract(demo):
    run, token = strategy_run(demo)
    discovery.search_documents(demo, run_id=run.id, run_token=token, query='', limit=25)
    for kind, did in [('policy', run.policy_id), ('document', 'doc-001')]:
        workflow.retrieve_for_run(demo, run_id=run.id, run_token=token, kind=kind, subject_id=did, actor='mcp:test')
    workflow.record_finding(demo, **payload(demo, run, token, proposed_content=CORRECTION))
    result = finalize(demo, run)
    assert result['errors'] and not result['proposals']
    assert 'search-first' in result['errors'][0]['error']
    assert not demo.run(run.id).metadata.get('verified_document_ids')


@pytest.mark.parametrize('queries,follow', [
    (['returns 60', '60 returns', 'returns 60'], True),
    (['returns 60', 'eligibility 30', 'purchase 60'], True),
    (['returns 60', 'send back', 'earlier promises'], False),
])
def test_cosmetic_queries_numeric_only_variations_or_unfollowed_leads_do_not_qualify(demo, queries, follow):
    run, token = strategy_run(demo)
    for query in queries:
        discovery.search_documents(demo, run_id=run.id, run_token=token, query=query)
    if follow:
        workflow.retrieve_for_run(demo, run_id=run.id, run_token=token, kind='document', subject_id='doc-001', actor='mcp:test')
    discovery.search_documents(demo, run_id=run.id, run_token=token, query='', limit=25)
    with demo.read() as conn:
        with pytest.raises(workflow.WorkflowError, match='search-first'):
            discovery.validate_search_strategy(demo, demo.run(run.id), conn)


def test_formulated_queries_followed_leads_and_catalog_pass_without_similarity(demo):
    run, token = strategy_run(demo)
    for query in ['return 60', 'send back', 'purchase promise']:
        discovery.search_documents(demo, run_id=run.id, run_token=token, query=query)
    for kind, did in [('policy', run.policy_id), ('document', 'doc-001')]:
        workflow.retrieve_for_run(demo, run_id=run.id, run_token=token, kind=kind, subject_id=did, actor='mcp:test')
    discovery.search_documents(demo, run_id=run.id, run_token=token, query='', limit=25)
    workflow.record_finding(demo, **payload(demo, run, token, proposed_content=CORRECTION))
    result = finalize(demo, run)
    assert not result['errors'] and len(result['proposals']) == 1
    strategy = demo.run(run.id).metadata['search_strategy']
    assert strategy['distinct_formulated_queries_before_catalog'] == 3 and strategy['similarity_calls'] == 0
    queries = [e.metadata for e in demo.events(type_prefix='agent.discovery') if e.metadata['operation'] == 'search_documents']
    assert [q['query'] for q in queries] == ['return 60', 'send back', 'purchase promise', '']


@pytest.mark.parametrize('operation', ['search_documents', 'similar_documents'])
def test_search_contract_rejects_observed_budget_overruns(demo, operation):
    run, token = strategy_run(demo)
    for query in ['return 60', 'send back', 'purchase promise']:
        discovery.search_documents(demo, run_id=run.id, run_token=token, query=query)
    workflow.retrieve_for_run(demo, run_id=run.id, run_token=token, kind='document', subject_id='doc-001', actor='mcp:test')
    discovery.search_documents(demo, run_id=run.id, run_token=token, query='', limit=25)
    if operation == 'search_documents':
        for _ in range(9): discovery.search_documents(demo, run_id=run.id, run_token=token, query='returns')
    else:
        for _ in range(5): discovery.similar_documents(demo, run_id=run.id, run_token=token, document_id='doc-001')
    with demo.read() as conn:
        with pytest.raises(workflow.WorkflowError): discovery.validate_search_strategy(demo, demo.run(run.id), conn)


def test_protected_patch_in_final_fallback_cannot_isolate_away_a_security_violation(demo):
    edit_document_externally(demo, 'crm-004', 'Items can be returned within 60 days.')
    run, _ = open_run(demo, ['doc-001', 'crm-004'], retrieve=True)
    result = finalize(demo, run, reply(demo, run, corrections={'doc-001': CORRECTION, 'crm-004': CORRECTION}))
    assert result['errors'][0]['document_id'] == 'crm-004'
    assert not demo.proposals() and not demo.findings()
    assert not demo.run(run.id).metadata.get('verified_document_ids')


def test_stale_safe_document_invalidates_reconciliation_instead_of_creating_partial_proposals(demo):
    run, token = open_run(demo, ['doc-001', 'help-002'], retrieve=True)
    workflow.record_finding(demo, **payload(demo, run, token, proposed_content=CORRECTION))
    workflow.record_finding(demo, **payload(demo, run, token, 'help-002'))
    text = reply(demo, run)
    edit_document_externally(demo, 'doc-001', 'Items can be returned within 45 days.')
    result = finalize(demo, run, text)
    assert result['errors'] and not result['proposals']
    assert not demo.run(run.id).metadata.get('verified_document_ids')


def test_mcp_search_findings_finalization_and_partial_api_projection(demo):
    old = server._state['store']
    server.use_store(demo)
    server.enable_discovery_tools(); server.enable_findings_tool(None)
    run, token = workflow.open_run(demo, 'shiftly-returns', ['doc-001', 'help-002'], actor='test',
        metadata={'discovery': True, 'discovery_strategy': 'search_first_v1'})
    credentials = {'run_id': run.id, 'run_token': token}
    async def exercise():
        async with Client(server.mcp) as client:
            health = await client.call_tool('health_check', {})
            assert health.structured_content['discovery_strategy'] == 'search_first_v1'
            assert not (await client.call_tool('get_policy', {**credentials, 'policy_id': run.policy_id})).is_error
            assert not (await client.call_tool('list_channels', credentials)).is_error
            for query in ['return 60', 'send back', 'purchase promise']:
                result = await client.call_tool('search_documents', {**credentials, 'query': query})
                assert not result.is_error
            assert not (await client.call_tool('get_document', {**credentials, 'document_id': 'doc-001'})).is_error
            assert not (await client.call_tool('search_documents', {**credentials, 'query': '', 'limit': 25})).is_error
            assert not (await client.call_tool('get_document', {**credentials, 'document_id': 'help-002'})).is_error
            for did in run.document_ids:
                changes = {'proposed_content': CORRECTION} if did == 'doc-001' else {}
                args = payload(demo, run, token, did, **changes); args.pop('actor')
                assert not (await client.call_tool('record_finding', args)).is_error
    try:
        asyncio.run(exercise())
        api_client = TestClient(api.build_app(demo, ui_dir=None), base_url='http://127.0.0.1', client=('127.0.0.1', 1))
        before = api_client.get('/api/policies/shiftly-returns/impact').json()
        assert all(not d['finding']['verified_conclusion'] for d in before['documents'] if d['finding'])
        result = finalize(demo, run)
        assert len(result['proposals']) == 1
        after = api_client.get('/api/policies/shiftly-returns/impact').json()
        docs = {d['id']: d for d in after['documents']}
        assert docs['doc-001']['finding']['verified_conclusion']
        assert docs['help-002']['finding']['action'] == 'needs_clarification'
        assert not docs['help-002']['finding']['verified_conclusion']
        assert all(f.source == 'mcp' for f in demo.findings())
        assert demo.run(run.id).metadata['search_strategy']['search_calls'] == 4
    finally:
        for name in server.DISCOVERY_TOOLS + ['record_finding']: server.mcp.remove_tool(name)
        server._state['discovery_tools'] = server._state['findings_tool'] = False
        server.use_store(old)
