from __future__ import annotations

import asyncio
import ast
import json
import secrets
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mcp import Client

import config
import fixtures
import readmodel
import run_impact
import server
import workflow
from conftest import GOOD_FAQ, analysis_run, propose
from store import Store
from test_readmodel import client_for
from test_workflow import edit_document_externally, reply_with


def args(run, secret, did='doc-001', **overrides):
    return dict(run_id=run.id, run_token=secret, policy_id=run.policy_id,
        policy_version=run.policy_version, document_id=did,
        document_version=run.document_versions[did],
        classification=fixtures.EXPECTED_CLASSIFICATION[did],
        evidence=fixtures.DOCUMENTS[did].content, reason='Compared with the policy.',
        actor='mcp:test', **overrides)


def verdict(run, **overrides):
    entries = {did: {'category': fixtures.EXPECTED_CLASSIFICATION[did],
        'document_version': run.document_versions[did], 'evidence': fixtures.DOCUMENTS[did].content,
        'reason': 'Compared with the policy.'} for did in run.document_ids}
    if 'doc-001' in entries:
        entries['doc-001']['proposed_content'] = GOOD_FAQ
    entries.update(overrides)
    return reply_with(entries)


def finalize(store, run, reply=None, status='success'):
    return workflow.ingest_agent_reply(store, reply or verdict(run), actor='agent:test',
        provenance={'thread_id': 't-1', 'message_id': 'm-1'}, run_id=run.id, execution_status=status)


def test_live_finding_is_authoritative_before_final_reply(changed, tmp_path):
    run, secret = analysis_run(changed)
    finding, duplicate = workflow.record_finding(changed, **args(run, secret))
    assert not duplicate and finding.retrieved_during_run
    snap = readmodel.impact_snapshot(changed, run.policy_id, tmp_path)
    doc = next(d for d in snap['documents'] if d['id'] == 'doc-001')
    assert doc['finding']['id'] == finding.id
    assert doc['finding']['run_id'] == run.id
    assert doc['finding']['evidence_verified'] and doc['finding']['current']
    assert snap['active_run_ids'] == [run.id]
    assert snap['latest_run']['classifications'] == {'doc-001': 'OUTDATED'}
    assert changed.proposals() == []


@pytest.mark.parametrize('field,value,error', [
    ('run_token', 'wrong', workflow.Unauthorized),
    ('run_token', None, workflow.Unauthorized),
    ('run_token', 123, workflow.Unauthorized),
    ('run_id', 'run-' + 'f' * 16, workflow.Unauthorized),
    ('policy_version', 1, workflow.Conflict),
    ('policy_version', True, workflow.Conflict),
    ('document_id', 'doc-004', workflow.WorkflowError),
    ('document_version', True, workflow.WorkflowError),
    ('document_version', 2, workflow.Conflict),
    ('document_version', None, workflow.Conflict),
    ('classification', 'MADE_UP', workflow.WorkflowError),
    ('evidence', 'Invented quote.', workflow.WorkflowError),
    ('evidence', '', workflow.WorkflowError),
    ('reason', '', workflow.WorkflowError),
    ('reason', 'x' * 1001, workflow.WorkflowError),
])
def test_finding_validation(changed, field, value, error):
    run, secret = analysis_run(changed, ['doc-001'])
    payload = args(run, secret)
    payload[field] = value
    with pytest.raises(error):
        workflow.record_finding(changed, **payload)
    assert changed.findings() == [] and changed.proposals() == []


def test_concurrent_identical_retries_are_idempotent(changed):
    run, secret = analysis_run(changed, ['doc-001'])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: workflow.record_finding(changed, **args(run, secret)), range(20)))
    assert len({f.id for f, _ in results}) == 1
    assert sum(not duplicate for _, duplicate in results) == 1
    assert len(changed.events(type_prefix='finding.recorded')) == 1


def test_conflicting_concurrent_submissions_have_one_winner(changed):
    run, secret = analysis_run(changed, ['doc-001'])
    def submit(category):
        payload = args(run, secret)
        payload['classification'] = category
        try:
            return workflow.record_finding(changed, **payload)[0]
        except workflow.Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ['OUTDATED', 'UNRELATED']))
    assert sum(r is not None for r in results) == 1
    assert len(changed.findings()) == 1


def test_unscoped_and_other_run_reads_do_not_verify_retrieval(changed):
    first, secret = analysis_run(changed, ['doc-001'], retrieve=False)
    analysis_run(changed, ['doc-001'])
    server.get_policy(fixtures.POLICY_ID, None)
    server.get_document('doc-001', None)
    with pytest.raises(workflow.WorkflowError, match='authenticated'):
        workflow.record_finding(changed, **args(first, secret))
    result = finalize(changed, first)
    assert result['errors'] and not changed.proposals()
    assert changed.run(first.id).status == 'incomplete'


def test_unknown_without_retrieved_version_is_honest_and_incomplete(changed):
    run, secret = analysis_run(changed, ['doc-001'], retrieve=False)
    payload = args(run, secret)
    payload.update(document_version=None, classification='UNKNOWN', evidence='', reason='Tool connection failed.')
    finding, _ = workflow.record_finding(changed, **payload)
    assert finding.document_version is None and not finding.retrieved_during_run
    entry = {'category': 'UNKNOWN', 'document_version': None, 'evidence': '', 'reason': payload['reason']}
    summary = finalize(changed, run, reply_with({'doc-001': entry}))
    assert summary['classifications'] == {'doc-001': 'UNKNOWN'}
    assert changed.run(run.id).status == 'incomplete'
    assert not changed.proposals()


def test_unknown_can_report_lost_response_after_server_read(changed):
    run, secret = analysis_run(changed, ['doc-001'])
    payload = args(run, secret)
    payload.update(document_version=None, classification='UNKNOWN', evidence='')
    finding, _ = workflow.record_finding(changed, **payload)
    assert finding.document_version is None and not finding.retrieved_during_run
    assert not changed.proposals()


@pytest.mark.parametrize('status', ['failed', 'cancelled', None])
def test_failed_execution_cannot_create_proposals(changed, status):
    run, _ = analysis_run(changed, ['doc-001'])
    result = finalize(changed, run, status=status)
    assert result['errors'] and result['proposals'] == []
    assert not changed.proposals() and not changed.findings()
    assert changed.run(run.id).status in {'failed', 'interrupted'}


@pytest.mark.parametrize('field,value', [
    ('category', 'UNRELATED'), ('document_version', 2),
    ('evidence', 'All customers'), ('reason', 'A different reason'),
])
def test_rejected_final_output_never_overwrites_live_finding(changed, tmp_path, field, value):
    run, secret = analysis_run(changed, ['doc-001'])
    accepted, _ = workflow.record_finding(changed, **args(run, secret))
    entry = workflow.parse_verdict(verdict(run))['documents']['doc-001']
    entry[field] = value
    summary = finalize(changed, run, reply_with({'doc-001': entry}))
    assert summary['proposals'] == [] and summary['errors']
    assert changed.findings()[0] == accepted
    # Even an erroneous legacy-style event must not become product state.
    changed.record_event('agent.classification', actor='agent:test', status='error',
        subject=('document', 'doc-001'), metadata={'category': 'UNRELATED', 'run_id': run.id})
    snap = readmodel.impact_snapshot(changed, run.policy_id, tmp_path)
    assert snap['documents'][0]['finding']['category'] == 'OUTDATED'


def test_final_json_fallback_uses_same_validation_and_persists_evidence(changed, tmp_path):
    run, _ = analysis_run(changed, ['doc-001'])
    result = finalize(changed, run)
    assert not result['errors'] and len(result['proposals']) == 1
    finding = changed.findings()[0]
    proposal = changed.proposal(result['proposals'][0])
    assert finding.source == 'final_json'
    assert proposal.finding_id == finding.id and proposal.run_id == run.id
    assert proposal.evidence == fixtures.DOCUMENTS['doc-001'].content
    later, secret = analysis_run(changed, ['doc-001'])
    payload = args(later, secret)
    payload.update(classification='UNKNOWN', evidence=fixtures.DOCUMENTS['doc-001'].content)
    workflow.record_finding(changed, **payload)
    detail = client_for(changed, tmp_path).get(f'/api/proposals/{proposal.id}').json()
    assert detail['evidence'] == proposal.evidence
    assert readmodel.impact_snapshot(changed, run.policy_id, tmp_path)['documents'][0]['finding']['category'] == 'OUTDATED'


def test_one_unretrieved_document_blocks_all_proposals(changed):
    run, secret = analysis_run(changed, ['doc-001', 'doc-004'], retrieve=False)
    for kind, did in [('policy', run.policy_id), ('document', 'doc-001')]:
        workflow.retrieve_for_run(changed, run_id=run.id, run_token=secret, kind=kind,
                                  subject_id=did, actor='mcp:test')
    reply = verdict(run, **{'doc-004': {'category': 'UNKNOWN', 'document_version': None,
        'evidence': '', 'reason': 'Retrieval failed.'}})
    result = finalize(changed, run, reply)
    assert result['proposals'] == [] and not changed.proposals()
    assert changed.run(run.id).status == 'incomplete'
    assert len(changed.findings()) == 2


def test_scope_mismatch_rejects_entire_final_batch(changed):
    run, _ = analysis_run(changed, ['doc-001'])
    reply = workflow.parse_verdict(verdict(run))
    reply['documents']['doc-004'] = {'category': 'UNRELATED'}
    result = finalize(changed, run, '```json\n' + json.dumps(reply) + '\n```')
    assert result['errors'] and not changed.findings() and not changed.proposals()


def test_concurrent_finalization_creates_one_proposal(changed):
    run, _ = analysis_run(changed, ['doc-001'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: finalize(changed, run), range(2)))
    assert sum(bool(r['proposals']) for r in results) == 1
    assert len(changed.proposals()) == 1
    assert changed.run(run.id).status == 'completed'


@pytest.mark.parametrize('target,status', [('expires_at', 'expired'), ('lease_until', 'interrupted')])
def test_expired_and_abandoned_runs_are_reaped_once(changed, tmp_path, target, status):
    run, secret = analysis_run(changed, ['doc-001'])
    with changed.write() as conn:
        conn.execute(f'UPDATE agent_runs SET {target} = ? WHERE id = ?',
                     ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), run.id))
    assert client_for(changed, tmp_path).get('/api/events').status_code == 200
    assert changed.run(run.id).status == status
    assert changed.reap_runs() == 0
    assert len(changed.events(type_prefix='agent.run_finished')) == 1
    with pytest.raises(workflow.Conflict):
        workflow.record_finding(changed, **args(run, secret))


def test_overlapping_runs_keep_identity_and_calls(changed, tmp_path):
    a, ta = analysis_run(changed, ['doc-001'], retrieve=False)
    b, tb = analysis_run(changed, ['doc-004'], retrieve=False)
    for run, token in [(a, ta), (b, tb)]:
        server.get_policy(run.policy_id, None, run.id, token)
        server.get_document(run.document_ids[0], None, run.id, token)
        workflow.record_finding(changed, **args(run, token, run.document_ids[0]))
    finalize(changed, a)
    states = {r['id']: r for r in readmodel.agent_runs(changed, tmp_path)}
    assert states[a.id]['status'] == 'completed' and states[b.id]['status'] == 'open'
    assert states[a.id]['classifications'] == {'doc-001': 'OUTDATED'}
    assert states[b.id]['classifications'] == {'doc-004': 'UNRELATED'}
    assert [c['document_id'] for c in states[a.id]['mcp_calls']] == [None, 'doc-001']
    assert [c['document_id'] for c in states[b.id]['mcp_calls']] == [None, 'doc-004']


@pytest.mark.parametrize('field', ['reason', 'evidence'])
def test_tokens_cannot_enter_finding_free_text_or_logs(changed, caplog, field):
    run, secret = analysis_run(changed, ['doc-001'])
    payload = args(run, secret)
    payload[field] = 'Echo ' + secret
    with pytest.raises(workflow.WorkflowError, match='credentials'):
        workflow.record_finding(changed, **payload)
    with pytest.raises(Exception):
        server.get_document(secret, None)
    assert secret not in caplog.text
    assert secret not in json.dumps([e.model_dump(mode='json') for e in changed.events()])
    assert secret.encode() not in changed.path.read_bytes()


def test_cross_process_redaction_covers_metadata_keys_and_subjects(changed, caplog):
    token = 'urt_' + secrets.token_urlsafe(32)
    changed.record_event('test.echo', actor=token, subject=('document', token),
        metadata={token: token, 'reason': token}, error=token)
    text = json.dumps([e.model_dump(mode='json') for e in changed.events()])
    assert token not in text
    assert config.redact('echo ' + token) == 'echo <redacted>'


@pytest.mark.parametrize('content', [
    'Items can be returned within 30 days.',
    GOOD_FAQ.replace('November 4, 2026', 'November 5, 2026'),
    GOOD_FAQ + ' Free shipping applies to all orders.',
    GOOD_FAQ + ' Order #9999 is promised a 365-day return period.',
    GOOD_FAQ + ' Your statutory 14-day withdrawal right is waived.',
    GOOD_FAQ + ' ' + 'urt_' + 'x' * 43,
])
def test_policy_only_proposal_validation(changed, content):
    with pytest.raises(workflow.WorkflowError):
        propose(changed, content)
    assert not changed.proposals()


def test_public_findings_remain_disabled_by_default(monkeypatch):
    assert not config.findings_tool_enabled() and not config.findings_public_allowed()
    with pytest.raises(SystemExit):
        server.enable_findings_tool('example.trycloudflare.com')
    assert not server._state['findings_tool']


def test_mcp_findings_schema_and_authenticated_roundtrip(changed, findings_tool):
    run, secret = analysis_run(changed, ['doc-001'], retrieve=False)
    async def check():
        async with Client(server.mcp) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            health = await client.call_tool('health_check', {})
            assert health.structured_content['read_only'] is False
            assert health.structured_content['database_id'] == changed.identity()
            assert not tools['record_finding'].annotations.read_only_hint
            assert tools['record_finding'].annotations.idempotent_hint
            credentials = {'run_id': run.id, 'run_token': secret}
            bad = await client.call_tool('get_document', {'document_id': 'doc-001', 'run_id': run.id})
            assert bad.is_error
            for name, params in [('get_policy', {'policy_id': run.policy_id}),
                                 ('get_document', {'document_id': 'doc-001'})]:
                assert not (await client.call_tool(name, {**params, **credentials})).is_error
            payload = args(run, secret)
            payload.pop('actor')
            for invalid in ({'policy_version': '2'}, {'document_version': '3'}, {'document_version': True}):
                assert (await client.call_tool('record_finding', {**payload, **invalid})).is_error
            first = await client.call_tool('record_finding', payload)
            second = await client.call_tool('record_finding', payload)
            assert first.structured_content['status'] == 'recorded'
            assert second.structured_content['status'] == 'duplicate'
            assert first.structured_content['finding_id'] == second.structured_content['finding_id']
            payload['classification'] = 'UNRELATED'
            assert (await client.call_tool('record_finding', payload)).is_error
            payload.update(classification='UNKNOWN', document_version=None, evidence='')
            assert (await client.call_tool('record_finding', payload)).is_error
    asyncio.run(check())
    assert not changed.proposals()


def test_transactional_v1_migration_preserves_existing_proposal(tmp_path, monkeypatch):
    # Build the old schema in isolation, without ever opening the real database.
    # Immutable v1 Store blob: stable across privacy-only commit metadata rewrites.
    source = subprocess.run(['git', 'show', '6572fd43139e4e9bd552f8ca29445d66c9e097fd'], check=True,
                            capture_output=True, text=True).stdout
    tree = ast.parse(source)
    schema = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'SCHEMA' for t in n.targets))
    path = tmp_path / 'v1.db'
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute('PRAGMA user_version = 1')
    conn.close()
    s = Store(path)
    s.seed()
    monkeypatch.setenv('UNDRIFT_REVIEW_TOKEN', 't' * 43)
    workflow.change_policy(s, fixtures.POLICY_ID, token='t' * 43, **fixtures.SHIFTLY_RETURNS_CHANGE)
    # Old table has no evidence columns: insert using explicit legacy columns.
    with s.write() as conn:
        conn.execute("INSERT INTO proposals (id, document_id, base_version, policy_id, policy_version, "
                     "original_content, proposed_content, diff, reason, status, created_at, created_by) "
                     "VALUES ('prop-04ca726838fc', 'doc-001', 3, ?, 2, ?, ?, '', 'legacy', "
                     "'pending_review', ?, 'agent:legacy')",
                     (fixtures.POLICY_ID, fixtures.DOCUMENTS['doc-001'].content, GOOD_FAQ,
                      datetime.now(UTC).isoformat()))
        before = dict(conn.execute("SELECT * FROM proposals").fetchone())
    assert not s.init()
    with s.read() as conn:
        after = dict(conn.execute('SELECT * FROM proposals').fetchone())
        from store import SCHEMA_VERSION
        assert conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    assert all(after[k] == v for k, v in before.items())
    assert not s.init()


def test_newer_schema_is_refused_without_downgrade(store):
    with store.write() as conn:
        conn.execute('PRAGMA user_version = 999')
    with pytest.raises(RuntimeError, match='newer'):
        store.init()
    with store.read() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 999


def test_stale_policy_and_document_cannot_be_finalized(changed, token):
    run, secret = analysis_run(changed, ['doc-001'])
    edit_document_externally(changed, 'doc-001', 'Returns: 60 days.')
    result = finalize(changed, run)
    assert result['errors'] and not changed.proposals()
    run, secret = analysis_run(changed, ['doc-001'])
    workflow.change_policy(changed, run.policy_id, voluntary_return_days=21,
        effective_from=fixtures.SHIFTLY_RETURNS_CHANGE['effective_from'], description='21 days.', token=token)
    with pytest.raises(workflow.Conflict, match='policy version'):
        workflow.record_finding(changed, **args(run, secret))
    assert finalize(changed, run)['errors'] and not changed.proposals()


def test_invalid_second_patch_rolls_back_entire_proposal_batch(changed):
    edit_document_externally(changed, 'doc-004', 'Items can be returned within 60 days.')
    run, _ = analysis_run(changed, ['doc-001', 'doc-004'])
    second = {'category': 'OUTDATED', 'document_version': 3,
              'evidence': 'Items can be returned within 60 days.', 'reason': 'Compared with the policy.',
              'proposed_content': 'Items can be returned within 30 days. Free gifts for everyone.'}
    result = finalize(changed, run, verdict(run, **{'doc-004': second}))
    assert result['errors'] and not result['proposals']
    assert result['errors'][0]['document_id'] == 'doc-004'
    assert result['errors'][0]['details']['violations']
    assert not changed.proposals() and not changed.events(type_prefix='proposal.created')
    assert not changed.findings() and changed.run(run.id).status == 'incomplete'


def test_closed_run_rejects_retries_and_new_reads(changed):
    run, secret = analysis_run(changed, ['doc-001'])
    workflow.record_finding(changed, **args(run, secret))
    finalize(changed, run)
    with pytest.raises(workflow.Conflict, match='completed'):
        workflow.record_finding(changed, **args(run, secret))
    with pytest.raises(workflow.Conflict, match='completed'):
        workflow.retrieve_for_run(changed, run_id=run.id, run_token=secret,
                                  kind='document', subject_id='doc-001', actor='mcp:test')


def test_failed_document_read_can_be_recorded_as_unknown(changed, monkeypatch):
    run, secret = analysis_run(changed, ['doc-001'], retrieve=False)
    original = changed.document
    monkeypatch.setattr(changed, 'document', lambda *a, **k: None)
    with pytest.raises(workflow.Conflict, match='retrieval failed'):
        workflow.retrieve_for_run(changed, run_id=run.id, run_token=secret,
                                  kind='document', subject_id='doc-001', actor='mcp:test')
    monkeypatch.setattr(changed, 'document', original)
    payload = args(run, secret)
    payload.update(document_version=None, classification='UNKNOWN', evidence='', reason='Retrieval failed.')
    finding, _ = workflow.record_finding(changed, **payload)
    assert not finding.retrieved_during_run
    with changed.read() as conn:
        assert conn.execute('SELECT status FROM run_retrievals').fetchone()[0] == 'error'


def old_store(tmp_path):
    source = subprocess.run(['git', 'show', '6572fd43139e4e9bd552f8ca29445d66c9e097fd'], check=True,
                            capture_output=True, text=True).stdout
    schema = next(ast.literal_eval(n.value) for n in ast.parse(source).body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SCHEMA' for t in n.targets))
    path = tmp_path / 'legacy.db'
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute('PRAGMA user_version = 1')
    conn.close()
    s = Store(path)
    s.seed()
    return s


def test_legacy_history_is_imported_once_with_canonical_ids(tmp_path, monkeypatch):
    s = old_store(tmp_path)
    monkeypatch.setenv('UNDRIFT_REVIEW_TOKEN', 't' * 43)
    workflow.change_policy(s, fixtures.POLICY_ID, token='t' * 43, **fixtures.SHIFTLY_RETURNS_CHANGE)
    s.record_event('agent.run_started', actor='agent:old', metadata={'agent_id': 'old-agent'})
    s.record_event('mcp.tool_call', actor='mcp:old', metadata={'tool': 'get_document',
        'document_id': 'doc-001', 'version': 3})
    s.record_event('agent.run_finished', actor='agent:old', metadata={'message_id': 'old-message',
        'thread_id': 'old-thread', 'status': 'success'})
    for did, category in fixtures.EXPECTED_CLASSIFICATION.items():
        s.record_event('agent.classification', actor='agent:old', subject=('document', did), metadata={
            'category': category, 'document_version': fixtures.DOCUMENTS[did].version,
            'policy_version': 2, 'provenance': {'message_id': 'old-message'}})
    s.record_event('agent.classification', actor='agent:old', status='error', error='rejected',
        subject=('document', 'doc-001'), metadata={'category': 'UNRELATED', 'document_version': 3,
            'policy_version': 2, 'provenance': {'message_id': 'old-message'}})
    s.init()
    [run] = s.runs()
    assert run.id.startswith('run-') and run.id != 'old-message'
    assert run.message_id == 'old-message' and run.thread_id == 'old-thread'
    assert len(s.findings()) == 4
    snap = readmodel.impact_snapshot(s, fixtures.POLICY_ID, tmp_path / 'absent')
    assert snap['documents'][0]['finding']['category'] == 'OUTDATED'
    assert snap['documents'][0]['finding']['run_id'] == run.id
    assert not snap['documents'][0]['finding']['evidence_verified']
    assert s.init() is False and len(s.runs()) == 1 and len(s.findings()) == 4


def test_v2_migration_preserves_findings_and_revokes_old_capabilities(tmp_path):
    s = old_store(tmp_path)
    with s.write() as conn:
        conn.execute("CREATE TABLE agent_runs (id TEXT PRIMARY KEY, policy_id TEXT, policy_version INTEGER, "
            "document_ids TEXT, token_hash TEXT, status TEXT, created_at TEXT, expires_at TEXT, closed_at TEXT, "
            "agent_id TEXT, thread_id TEXT, message_id TEXT)")
        conn.execute("CREATE TABLE findings (id TEXT PRIMARY KEY, run_id TEXT REFERENCES agent_runs(id), "
            "policy_id TEXT, policy_version INTEGER, document_id TEXT, document_version INTEGER, category TEXT, "
            "evidence TEXT, reason TEXT, retrieved_during_run INTEGER, submission_sha256 TEXT, created_at TEXT)")
        stamp = datetime.now(UTC).isoformat()
        conn.execute("INSERT INTO agent_runs VALUES (?, ?, 1, ?, 'old-hash', 'open', ?, ?, NULL, NULL, NULL, NULL)",
            ('run-0123456789abcdef', fixtures.POLICY_ID, '["doc-001"]', stamp, stamp))
        conn.execute("INSERT INTO findings VALUES ('fnd-old', ?, ?, 1, 'doc-001', 3, 'UNRELATED', ?, 'old reason', 1, 'old-digest', ?)",
            ('run-0123456789abcdef', fixtures.POLICY_ID, fixtures.DOCUMENTS['doc-001'].content, stamp))
        conn.execute('PRAGMA user_version = 2')
    s.init()
    assert s.run('run-0123456789abcdef').status == 'interrupted'
    [finding] = s.findings()
    assert finding.id == 'fnd-old' and finding.reason == 'old reason'
    assert finding.source == 'legacy' and not finding.retrieved_during_run
    with s.read() as conn:
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    assert s.init() is False


def test_migration_failure_rolls_back_schema_and_version(tmp_path):
    s = old_store(tmp_path)
    s.record_event('agent.run_started', actor='agent:old')
    s.record_event('agent.run_finished', actor='agent:old', metadata={'message_id': 'old'})
    s.record_event('agent.classification', actor='agent:old', subject=('document', 'doc-001'), metadata={
        'category': 'UNRELATED', 'document_version': 999, 'policy_version': 1,
        'provenance': {'message_id': 'old'}})
    with pytest.raises(sqlite3.IntegrityError):
        s.init()
    with s.read() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
        assert 'run_id' not in {r[1] for r in conn.execute('PRAGMA table_info(proposals)')}
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'agent_runs'").fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 4


def test_policy_editor_rejects_stale_base_version(changed, token):
    with pytest.raises(workflow.Conflict, match='editor'):
        workflow.change_policy(changed, fixtures.POLICY_ID, token=token, expected_version=1,
            voluntary_return_days=21, effective_from=fixtures.SEED_POLICY.effective_from, description='21 days.')
    assert changed.policy(fixtures.POLICY_ID).version == 2


def test_quote_cannot_authorize_replacing_shipping_or_warranty(changed):
    for text in ['Shipping takes 60 days.', 'We send packages within 60 days.',
                 'The warranty lasts 60 days.']:
        violations, _ = workflow.check_guardrails(text,
            'Items purchased on or after 2026-11-04 can be returned within 30 days.',
            current_days=30, stale=workflow.days_pattern({60}), evidence=text,
            effective_from=fixtures.SHIFTLY_RETURNS_CHANGE['effective_from'])
        assert {'rule': 'unrelated_content', 'sentence': 0} in violations


def test_new_transition_cannot_repurpose_a_statutory_period(changed):
    original = ('All customers can return items within 60 days. '
                'Customers have a statutory 14-day withdrawal right.')
    proposed = ('Items purchased on or after 2026-11-04 can be returned within 30 days. '
                'Purchases made before 2026-11-04 keep the 14-day return period promised at the time of purchase. '
                'Customers have a statutory 14-day withdrawal right.')
    violations, _ = workflow.check_guardrails(original, proposed, current_days=30,
        stale=workflow.days_pattern({60}), effective_from=fixtures.SHIFTLY_RETURNS_CHANGE['effective_from'])
    assert {'rule': 'unsupported_added_claim', 'proposed_sentence': 1} in violations


@pytest.mark.parametrize('document_read', [False, True])
def test_policy_retrieval_failure_can_be_reported_without_guessing(changed, document_read):
    run, secret = analysis_run(changed, ['doc-001'], retrieve=False)
    if document_read:
        workflow.retrieve_for_run(changed, run_id=run.id, run_token=secret,
                                  kind='document', subject_id='doc-001', actor='mcp:test')
    payload = args(run, secret)
    payload.update(policy_version=None, classification='UNKNOWN',
        document_version=3 if document_read else None,
        evidence=fixtures.DOCUMENTS['doc-001'].content if document_read else '',
        reason='The policy response was unavailable.')
    finding, _ = workflow.record_finding(changed, **payload)
    assert finding.policy_version == run.policy_version and not finding.retrieved_during_run
    reply = {'policy': {'id': run.policy_id, 'version': None, 'voluntary_return_days': None},
        'documents': {'doc-001': {'category': 'UNKNOWN', 'document_version': payload['document_version'],
            'evidence': payload['evidence'], 'reason': payload['reason']}}, 'changes_made': False}
    result = finalize(changed, run, '```json\n' + json.dumps(reply) + '\n```')
    assert result['classifications'] == {'doc-001': 'UNKNOWN'} and not changed.proposals()
    assert changed.run(run.id).status == 'incomplete'
