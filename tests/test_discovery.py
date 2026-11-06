"""Isolated discovery/security/integration checks. No live services or paid agents."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from pathlib import Path

import httpx
from mcp import Client
import pytest
from starlette.testclient import TestClient

import api
import config
import discovery
from discovery_seed import create_demo, seed_hash
import publishing
import readmodel
import server
from store import SCHEMA_VERSION, Store
import workflow

TRUTH = json.loads((Path(__file__).parent / 'data/discovery_ground_truth.json').read_text())
CORRECTION = ('Items purchased on or after 2026-11-04 can be returned within 30 days. '
              'Purchases made before 2026-11-04 keep the return period promised at the time of purchase.')


@pytest.fixture
def demo(tmp_path):
    return create_demo(tmp_path / 'unified.db')


def open_run(store, ids=None, retrieve=False):
    run, token = workflow.open_run(store, 'shiftly-returns', ids or store.document_ids(),
                                 actor='test:discovery', metadata={'discovery': True})
    if retrieve:
        workflow.retrieve_for_run(store, run_id=run.id, run_token=token, kind='policy',
                                  subject_id=run.policy_id, actor='mcp:test')
        for did in run.document_ids:
            workflow.retrieve_for_run(store, run_id=run.id, run_token=token, kind='document',
                                      subject_id=did, actor='mcp:test')
    return run, token


def payload(store, run, token, did='doc-001', **overrides):
    doc = store.document(did)
    return dict(run_id=run.id, run_token=token, policy_id=run.policy_id, policy_version=run.policy_version,
                document_id=did, document_version=doc.version, classification=TRUTH[did],
                evidence=doc.content, reason='Compared with the current policy.', actor='mcp:test', **overrides)


def reply(store, run, categories=None, corrections=None):
    entries = {}
    for did in run.document_ids:
        doc = store.document(did)
        entries[did] = {'category': (categories or TRUTH)[did], 'document_version': doc.version,
                        'evidence': doc.content, 'reason': 'Compared with the current policy.'}
        if did in (corrections or {}): entries[did]['proposed_content'] = corrections[did]
    return '```json\n' + json.dumps({'policy': {'id': run.policy_id, 'version': run.policy_version,
        'voluntary_return_days': 30}, 'documents': entries, 'changes_made': False}) + '\n```'


def test_deterministic_fresh_seed_has_seven_channels_and_no_history(demo, tmp_path):
    other = create_demo(tmp_path / 'second.db')
    assert demo.identity() != other.identity()
    assert demo.documents() == other.documents()
    assert demo.policy_history('shiftly-returns') == other.policy_history('shiftly-returns')
    assert demo.metadata('seed_sha256') == other.metadata('seed_sha256') == seed_hash()
    expected = {'website': 4, 'help-center': 4, 'order-email': 3, 'support-snippets': 4,
                'bot-knowledge': 3, 'legal': 2, 'crm': 4}
    assert {c['id']: sum(d.channel == c['id'] for d in demo.documents()) for c in demo.channels()} == expected
    assert demo.runs() == demo.findings() == demo.proposals() == []
    with demo.read() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert c.execute('SELECT COUNT(*) FROM publications').fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM document_search').fetchone()[0] == 24
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    assert demo.events()[0].type == 'demo.seeded'
    before = demo.path.read_bytes()
    with pytest.raises(FileExistsError): create_demo(demo.path)
    assert demo.path.read_bytes() == before


def test_channel_counts_search_and_similarity_are_run_scoped(demo):
    run, token = open_run(demo, ['doc-001', 'web-003', 'web-004'])
    channels = discovery.list_channels(demo, run_id=run.id, run_token=token)
    assert channels['channels'][0]['document_count'] == channels['document_count'] == 3
    assert len(channels['channels']) == 1
    result = discovery.search_documents(demo, run_id=run.id, run_token=token, query='return months')
    assert {r['document_id'] for r in result['results']} == {'doc-001', 'web-003'}
    similar = discovery.similar_documents(demo, run_id=run.id, run_token=token, document_id='doc-001')
    assert similar['method'] == 'tfidf_cosine'
    assert set(r['document_id'] for r in similar['results']) <= {'web-003', 'web-004'}
    assert similar == discovery.similar_documents(demo, run_id=run.id, run_token=token, document_id='doc-001')
    assert not workflow.retrieval_status(demo, run)['complete']
    assert all(e.metadata['run_id'] == run.id for e in demo.events(type_prefix='agent.discovery'))
    with pytest.raises(workflow.WorkflowError):
        discovery.search_documents(demo, run_id=run.id, run_token=token, channel_id='crm')
    with pytest.raises(workflow.WorkflowError):
        discovery.similar_documents(demo, run_id=run.id, run_token=token, document_id='crm-001')


def test_search_paginates_catalog_and_treats_fts_operators_as_literal_words(demo):
    run, token = open_run(demo)
    first = discovery.search_documents(demo, run_id=run.id, run_token=token, limit=12)
    second = discovery.search_documents(demo, run_id=run.id, run_token=token, limit=12, offset=12)
    assert first['total'] == second['total'] == 24
    assert len({r['document_id'] for r in first['results'] + second['results']}) == 24
    for query in ['" OR *', 'return NEAR(policy)', "' OR 1=1 --", 'two months']:
        result = discovery.search_documents(demo, run_id=run.id, run_token=token, query=query)
        assert all(r['document_id'] in run.document_ids for r in result['results'])
    assert discovery.search_documents(demo, run_id=run.id, run_token=token, query='durability')['results'][0]['document_id'] == 'web-004'


@pytest.mark.parametrize('operation', ['list_channels', 'search_documents', 'similar_documents'])
def test_cross_run_and_closed_tokens_cannot_discover(demo, operation):
    a, ta = open_run(demo, ['doc-001']); b, tb = open_run(demo, ['crm-001'])
    kwargs = {'document_id': 'doc-001'} if operation == 'similar_documents' else {}
    method = getattr(discovery, operation)
    with pytest.raises(workflow.Unauthorized): method(demo, run_id=a.id, run_token=tb, **kwargs)
    workflow.close_run(demo, a.id, status='interrupted', actor='test')
    with pytest.raises(workflow.Conflict): method(demo, run_id=a.id, run_token=ta, **kwargs)


def test_fts_updates_current_versions_and_stale_runs_are_rejected(demo):
    old, token = open_run(demo, ['doc-001'])
    with demo.write() as c:
        c.execute('INSERT INTO document_versions VALUES (?,?,?,?,?)',
                  ('doc-001', 2, 'An entirely different zephyr policy.', datetime.now(UTC).isoformat(), 'test'))
        c.execute("UPDATE documents SET current_version=2 WHERE id='doc-001'")
    with pytest.raises(workflow.Conflict): discovery.search_documents(demo, run_id=old.id, run_token=token, query='return')
    with pytest.raises(workflow.Conflict): workflow.retrieve_for_run(demo, run_id=old.id, run_token=token, kind='document', subject_id='doc-001', actor='mcp:test')
    new, fresh = open_run(demo, ['doc-001'])
    assert discovery.search_documents(demo, run_id=new.id, run_token=fresh, query='60')['results'] == []
    result = discovery.search_documents(demo, run_id=new.id, run_token=fresh, query='zephyr')['results']
    assert result[0]['version'] == 2


@pytest.mark.parametrize('overrides', [
    {'classification': 'INVALID'}, {'document_version': 99}, {'evidence': 'Invented evidence.'},
    {'proposed_content': 'Return everything whenever you want.'}, {'policy_version': 1},
])
def test_finding_validation_rejects_bad_inputs(demo, overrides):
    run, token = open_run(demo, ['doc-001'], retrieve=True)
    args = payload(demo, run, token); args.update(overrides)
    with pytest.raises(workflow.WorkflowError): workflow.record_finding(demo, **args)
    assert demo.findings() == demo.proposals() == []


def test_search_snippet_cannot_authorize_a_finding(demo):
    run, token = open_run(demo, ['doc-001'])
    discovery.search_documents(demo, run_id=run.id, run_token=token, query='returns')
    with pytest.raises(workflow.WorkflowError, match='retrieval'): workflow.record_finding(demo, **payload(demo, run, token))


def test_correction_is_immutable_and_generates_only_a_pending_proposal_at_finalization(demo):
    run, token = open_run(demo, ['doc-001'], retrieve=True)
    args = payload(demo, run, token, proposed_content=CORRECTION)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: workflow.record_finding(demo, **args), range(8)))
    assert sum(not duplicate for _, duplicate in results) == 1
    assert demo.findings()[0].proposed_content == CORRECTION
    assert demo.proposals() == []
    result = workflow.ingest_agent_reply(demo, reply(demo, run), actor='agent:test', provenance={},
                                         run_id=run.id, execution_status='success')
    assert not result['errors'] and len(result['proposals']) == 1
    proposal = demo.proposals()[0]
    assert proposal.proposed_content == CORRECTION and proposal.status == 'pending_review'
    assert proposal.run_id == run.id and proposal.finding_id == demo.findings()[0].id
    assert demo.document('doc-001').content == 'All customers can return items within 60 days.'


def test_final_json_cannot_change_recorded_correction(demo):
    run, token = open_run(demo, ['doc-001'], retrieve=True)
    workflow.record_finding(demo, **payload(demo, run, token, proposed_content=CORRECTION))
    final = reply(demo, run, corrections={'doc-001': CORRECTION + ' Your statutory 14-day right of withdrawal is not affected.'})
    result = workflow.ingest_agent_reply(demo, final, actor='agent:test', provenance={}, run_id=run.id, execution_status='success')
    assert result['errors'] and demo.proposals() == []
    assert demo.findings()[0].proposed_content == CORRECTION


@pytest.mark.parametrize('did', ['help-003', 'legal-001', 'legal-002', 'mail-001', 'web-004', 'web-002'])
def test_protected_and_unrelated_findings_cannot_carry_corrections(demo, did):
    run, token = open_run(demo, [did], retrieve=True)
    with pytest.raises(workflow.WorkflowError):
        workflow.record_finding(demo, **payload(demo, run, token, did, proposed_content=CORRECTION))
    forged = payload(demo, run, token, did, proposed_content=CORRECTION); forged['classification'] = 'OUTDATED'
    with pytest.raises(workflow.GuardrailViolation): workflow.record_finding(demo, **forged)
    assert demo.proposals() == []


def test_semantic_two_month_claim_can_be_corrected_but_unrelated_text_is_preserved(demo):
    run, token = open_run(demo, ['web-003'], retrieve=True)
    workflow.record_finding(demo, **payload(demo, run, token, 'web-003', proposed_content=CORRECTION))
    result = workflow.ingest_agent_reply(demo, reply(demo, run), actor='agent:test', provenance={}, run_id=run.id, execution_status='success')
    assert not result['errors'] and len(demo.proposals()) == 1


def test_unified_publication_tables_reuse_existing_service_and_auth(demo, token, monkeypatch):
    service = publishing.Publisher(demo, publishing.Target('example/shiftly'), journal=demo.path)
    assert service.path == demo.path
    with pytest.raises(workflow.Unauthorized): service.plan('not-approved', 'invalid')
    assert demo.proposals() == []
    client = TestClient(api.build_app(demo, ui_dir=None, publisher=service), base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    assert client.get('/api/proposals/x/publication').json()['configured']
    assert client.post('/api/proposals/x/publication/publish', json={'approval_digest': 'f'*64}).status_code == 401
    monkeypatch.setenv('UNDRIFT_GITHUB_REPOSITORY', 'example/shiftly')
    monkeypatch.delenv('UNDRIFT_PUBLISH_DB', raising=False)
    assert publishing.configured(demo).path == demo.path


def test_mcp_discovery_to_live_finding_and_api_projection(demo):
    old = server._state['store']; server.use_store(demo)
    server.enable_discovery_tools(); server.enable_findings_tool(None)
    run, token = open_run(demo, ['doc-001'])
    async def exercise():
        async with Client(server.mcp) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert set(server.DISCOVERY_TOOLS) <= set(tools)
            assert {'run_id', 'run_token'} <= set(tools['search_documents'].input_schema['required'])
            assert (await client.call_tool('get_document', {'document_id': 'doc-001'})).is_error
            credentials = {'run_id': run.id, 'run_token': token}
            listing = await client.call_tool('list_channels', credentials)
            assert listing.structured_content['document_count'] == 1
            search = await client.call_tool('search_documents', {**credentials, 'query': 'return'})
            assert search.structured_content['results'][0]['document_id'] == 'doc-001'
            await client.call_tool('get_policy', {**credentials, 'policy_id': run.policy_id})
            await client.call_tool('get_document', {**credentials, 'document_id': 'doc-001'})
            args = payload(demo, run, token, proposed_content=CORRECTION); args.pop('actor')
            assert not (await client.call_tool('record_finding', args)).is_error
    try:
        asyncio.run(exercise())
        snapshot = readmodel.impact_snapshot(demo, run.policy_id)
        doc = next(d for d in snapshot['documents'] if d['id'] == 'doc-001')
        assert doc['finding']['run_id'] == run.id
        assert not doc['finding']['verified_conclusion']
        assert all(e.metadata.get('run_id') == run.id for e in demo.events(type_prefix='agent.discovery'))
    finally:
        for name in server.DISCOVERY_TOOLS + ['record_finding']: server.mcp.remove_tool(name)
        server._state['discovery_tools'] = server._state['findings_tool'] = False
        server.use_store(old)


def test_ground_truth_is_not_in_seed_database_or_runtime_sources(demo):
    for path in ['server.py', 'discovery.py', 'discovery_seed.py', 'agents/patch.md']:
        assert 'discovery_ground_truth' not in Path(path).read_text(encoding='utf-8')
    with demo.read() as c:
        assert not c.execute("SELECT name FROM sqlite_master WHERE name LIKE '%truth%' OR name LIKE '%expected%'").fetchall()
    assert len(TRUTH) == 24 and set(TRUTH) == set(demo.document_ids())


def test_ambiguous_corpus_without_corrections_retains_findings_without_inventing_proposals(demo):
    run, token = open_run(demo, retrieve=True)
    for did in run.document_ids:
        workflow.record_finding(demo, **payload(demo, run, token, did))
    result = workflow.ingest_agent_reply(demo, reply(demo, run), actor='agent:test', provenance={},
                                       run_id=run.id, execution_status='success')
    assert len(demo.findings(run_id=run.id)) == 24
    assert result['errors'] and demo.run(run.id).status == 'incomplete'
    assert demo.proposals() == []


def test_actionable_scope_creates_six_proposals_and_reserves_crm_for_manual_review(demo):
    ids = [did for did, category in TRUTH.items() if category == 'OUTDATED']
    run, token = open_run(demo, ids, retrieve=True)
    for did in ids:
        original = demo.document(did).content
        unchanged = workflow.split_sentences(original)[1:]
        correction = CORRECTION + (' ' + ' '.join(unchanged) if unchanged else '')
        changes = {} if demo.document(did).channel in workflow.MANUAL_ONLY_CHANNELS else {'proposed_content': correction}
        workflow.record_finding(demo, **payload(demo, run, token, did, **changes))
    result = workflow.ingest_agent_reply(demo, reply(demo, run), actor='agent:test', provenance={},
                                       run_id=run.id, execution_status='success')
    assert len(result['proposals']) == 6
    assert result['document_outcomes']['crm-004']['action'] == 'manual_only'
    assert demo.run(run.id).status == 'incomplete'
    assert not demo.proposals(document_id='crm-004')
    assert all(p.status == 'pending_review' for p in demo.proposals())
    assert all(p.document_id in ids for p in demo.proposals())


def test_credentials_are_never_logged_in_search_or_optional_corrections(demo):
    run, token = open_run(demo, ['doc-001'], retrieve=True)
    with pytest.raises(workflow.WorkflowError):
        discovery.search_documents(demo, run_id=run.id, run_token=token, query='return ' + token)
    with pytest.raises(workflow.WorkflowError):
        workflow.record_finding(demo, **payload(demo, run, token, proposed_content=CORRECTION + token))
    assert token not in json.dumps([e.model_dump(mode='json') for e in demo.events()])
    assert demo.findings() == []


def test_unified_mcp_to_human_approval_and_verified_publication_is_one_store(demo, token):
    # All provider traffic is simulated; exercises the real publisher against the same SQLite file.
    from test_publishing import Remote, site_client
    run, run_token = open_run(demo, ['doc-001'], retrieve=True)
    workflow.record_finding(demo, **payload(demo, run, run_token, proposed_content=CORRECTION))
    result = workflow.ingest_agent_reply(demo, reply(demo, run), actor='agent:test', provenance={},
                                       run_id=run.id, execution_status='success')
    pid = result['proposals'][0]
    remote = Remote()
    target = publishing.Target(remote.repo, site_url='https://shiftly-demo.vercel.app')
    with httpx.Client(base_url='https://api.github.com', transport=httpx.MockTransport(remote.handle)) as github:
        publisher = publishing.Publisher(demo, target, journal=demo.path, github=publishing.GitHub(target, github))
        client = TestClient(api.build_app(demo, ui_dir=None, publisher=publisher), base_url='http://127.0.0.1', client=('127.0.0.1', 1))
        headers = {'Authorization': 'Bearer ' + token}
        base = '/api/proposals/' + pid
        assert client.post(base + '/publication/plan', headers=headers, json={}).status_code == 409
        assert client.post(base + '/approve', headers=headers, json={}).status_code == 200
        plan = client.post(base + '/publication/plan', headers=headers, json={}).json()['plan']
        response = client.post(base + '/publication/publish', headers=headers, json={'approval_digest': plan['digest']})
        assert response.status_code == 200 and response.json()['publication']['status'] == 'pr_created'
        merged = remote.merged()
        with site_client(content=CORRECTION, commit=merged) as web:
            publisher.web = web
            response = client.post(base + '/publication/refresh', headers=headers, json={})
        state = response.json()['publication']
        assert state['status'] == 'verified' and state['verification_evidence']['source'] == 'production_site'
        assert state['merge_commit'] == merged and len(remote.prs) == 1
        assert publisher.path == demo.path
        assert demo.proposal(pid).run_id == run.id
        assert demo.proposal(pid).status == 'approved'
        with demo.read() as c:
            assert c.execute('SELECT COUNT(*) FROM publications').fetchone()[0] == 1
            assert c.execute("SELECT COUNT(*) FROM publication_events WHERE type='publishing.verified'").fetchone()[0] == 1


def test_discovery_prompt_does_not_supply_ids_or_answers(demo):
    import run_impact
    prompt = run_impact.build_prompt('shiftly-returns', demo.document_ids(), 'run-0123456789abcdef', 'test-token', discovery=True)
    assert all(did not in prompt for did in demo.document_ids())
    assert 'list_channels' in prompt and 'similar_documents' in prompt and 'paginated' in prompt
    instructions = Path('agents/discovery.md').read_text()
    assert 'discovery_ground_truth' not in instructions
    assert all(did not in instructions for did in demo.document_ids())


def test_v3_migration_adds_index_without_changing_existing_records(tmp_path):
    store = Store(tmp_path / 'old.db'); store.init()
    before = store.documents(), store.policy_history('shiftly-returns'), store.events(), store.identity()
    with store.write() as c:
        for name in ['search_document_update', 'search_document_delete', 'search_version_insert', 'search_version_update', 'search_version_delete']:
            c.execute('DROP TRIGGER ' + name)
        c.execute('DROP TABLE document_search'); c.execute('DROP TABLE finding_corrections')
        c.execute('PRAGMA user_version=3')
    store.init(seed=False)
    assert (store.documents(), store.policy_history('shiftly-returns'), store.events(), store.identity()) == before
    with store.read() as c:
        assert c.execute('SELECT COUNT(*) FROM document_search').fetchone()[0] == 4
