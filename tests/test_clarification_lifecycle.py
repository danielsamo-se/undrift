"""Execution-quality failures must not erase provider evidence or authorize retries."""
import asyncio
import json
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

import api
import clarification as cq
import discovery
from discovery_seed import create_demo
import reliability
import run_impact
from scripts import evaluation_service, reliability_evaluation as evaluator
from scripts.evaluate_clarification import supply_answers
import workflow
from test_clarification import INSTRUCTIONS, NativeRun, begin, native, pause, ready, retrieve, resume
from test_discovery import TRUTH, reply, payload
from store import Store


@pytest.fixture
def demo(tmp_path):
    return create_demo(tmp_path / 'lifecycle.db')


def bad_strategy(store, run, secret):
    for query in ['return period', 'send back', 'original offer']:
        discovery.search_documents(store, run_id=run.id, run_token=secret, query=query)
    discovery.search_documents(store, run_id=run.id, run_token=secret, query='', limit=25)
    retrieve(store, run, secret)


def test_five_native_questions_survive_violation_with_exact_options_and_api_visibility(demo, token):
    ids = ['crm-002', 'bot-003', 'help-002', 'help-004', 'support-002']
    run, secret = begin(demo, ids=ids, strategy=True)
    bad_strategy(demo, run, secret)
    questions = [native(did) for did in ids]
    pause(demo, run, questions)
    client = TestClient(api.build_app(demo, ui_dir=None), base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    [conv] = client.get('/api/conversations').json()['conversations']
    saved = conv['phases'][0]['questions']
    assert [(q['prompt'], q['options']) for q in saved] == [(q.prompt, q.options) for q in questions]
    assert all(q['answer'] is None and q['evidence'] == demo.document(q['document_id']).content for q in saved)
    assert conv['state'] == 'failed' and conv['continuation_blocker']
    assert client.post(f'/api/questions/{saved[0]["id"]}/answer', json={'option': 1}).status_code == 401
    assert client.post(f'/api/questions/{saved[0]["id"]}/answer', headers={'Authorization': f'Bearer {token}'}, json={'option': 1}).status_code == 409
    with pytest.raises(workflow.Conflict): supply_answers(demo, run.id, {}, token=token)
    result = reliability.score(demo, run.id, TRUTH)
    assert result['native_question_integrity'] and result['native_question_count'] == 5
    assert result['metrics']['native_clarification_recall'] == 1
    assert result['metrics']['accuracy'] == result['metrics']['outdated_recall'] == 0
    assert not result['reconciled'] and not demo.proposals()
    assert len(demo.events(type_prefix='agent.search_strategy_rejected')) == 1
    with pytest.raises(workflow.Conflict): pause(demo, run, questions)
    assert len(cq.listing(demo)[0]['phases'][0]['questions']) == 5


@pytest.mark.parametrize('corruption', ['ready_label', 'stray_certificate'])
def test_failed_strategy_cannot_be_laundered_into_continuation_or_scoring(demo, corruption):
    run, secret = begin(demo, strategy=True)
    bad_strategy(demo, run, secret)
    workflow.record_finding(demo, **payload(demo, run, secret, 'help-002'))
    pause(demo, run)
    with demo.write() as c:
        if corruption == 'ready_label':
            c.execute("UPDATE conversations SET state='ready'")
        else:
            metadata = demo.run(run.id, c).metadata
            metadata['finalization'] = 'per_document_v1'
            c.execute('UPDATE agent_runs SET metadata=? WHERE id=?', (json.dumps(metadata), run.id))
    with pytest.raises(workflow.Conflict): resume(demo, run)
    assert len(demo.runs()) == 1
    result = reliability.score(demo, run.id, TRUTH)
    assert not result['reconciled'] and result['metrics']['accuracy'] == 0


def test_strategy_violation_still_rejects_invalid_context_atomically(demo):
    run, secret = begin(demo, strategy=True)
    bad_strategy(demo, run, secret)
    with pytest.raises(workflow.Conflict): pause(demo, run, [native(), native('crm-004')])
    assert cq.listing(demo)[0]['phases'][0]['questions'] == []
    assert demo.run(run.id).status == 'open'


@pytest.mark.parametrize('exception', [sqlite3.OperationalError('disk unavailable'), KeyboardInterrupt()])
def test_interrupted_capture_rolls_back_rows_but_retains_prior_terminal_evidence(demo, monkeypatch, exception):
    run, _ = begin(demo, strategy=True)
    original = workflow.close_run
    def fail(*args, **kwargs):
        raise exception
    monkeypatch.setattr(workflow, 'close_run', fail)
    with pytest.raises(type(exception)): pause(demo, run)
    assert cq.listing(demo)[0]['phases'][0]['questions'] == []
    assert demo.run(run.id).status == 'open'
    with demo.read() as c:
        assert c.execute("SELECT COUNT(*) FROM conversation_evidence WHERE kind='terminal'").fetchone()[0] == 1
    monkeypatch.setattr(workflow, 'close_run', original)
    workflow.close_run(demo, run.id, status='interrupted', actor='test')
    cq.finish(demo, run.id)
    result = reliability.score(demo, run.id, TRUTH)
    assert result['status'] == 'interrupted' and not result['native_question_integrity']
    with pytest.raises(RuntimeError, match='persistence mismatch'): evaluator.check_trial(result)


@pytest.mark.parametrize('damage', ['unanswered', 'no_questions', 'interrupted'])
def test_ready_label_alone_never_authorizes_a_new_paid_phase(demo, token, damage):
    run, _, _ = ready(demo, token)
    with demo.write() as c:
        if damage == 'unanswered': c.execute('UPDATE clarification_questions SET answer=NULL')
        elif damage == 'no_questions': c.execute('DELETE FROM clarification_questions')
        else: c.execute("UPDATE agent_runs SET status='interrupted'")
    with pytest.raises(workflow.Conflict): resume(demo, run)
    assert len(demo.runs()) == 1


@pytest.mark.parametrize('mode', ['questions', 'no_questions', 'stream_interrupted', 'post_capture_failure'])
def test_runner_retains_provider_evidence_and_quality_failure_without_dispatching_again(demo, tmp_path, monkeypatch, mode):
    (tmp_path / 'agents').mkdir()
    (tmp_path / 'agents/discovery.md').write_bytes(INSTRUCTIONS)
    monkeypatch.setattr(run_impact, 'ROOT', tmp_path)
    async def start(**kwargs):
        rid = re.search(r'Run ID: (\S+)', kwargs['message'])[1]
        secret = re.search(r'Run token: (\S+)', kwargs['message'])[1]
        run = demo.run(rid)
        bad_strategy(demo, run, secret)
        return NativeRun([] if mode == 'no_questions' else [native()], reply(demo, run), crash=mode == 'stream_interrupted')
    client = SimpleNamespace(chat=SimpleNamespace(start=AsyncMock(side_effect=start), send=AsyncMock(), cancel=AsyncMock()))
    original_capture = cq.capture
    if mode == 'post_capture_failure':
        def fail_after_capture(*args, **kwargs):
            original_capture(*args, **kwargs)
            raise OSError('private file sink failed')
        monkeypatch.setattr(cq, 'capture', fail_after_capture)
    if mode in {'stream_interrupted', 'post_capture_failure'}:
        with pytest.raises(RuntimeError if mode == 'stream_interrupted' else OSError):
            asyncio.run(run_impact.paid_run(client, demo, 'impact', 'existing-connection', 40, discovery=True))
    else:
        assert asyncio.run(run_impact.paid_run(client, demo, 'impact', 'existing-connection', 40, discovery=True)) == 1
    [run] = demo.runs()
    assert run.status == 'failed' and not demo.proposals()
    assert cq.listing(demo)[0]['state'] == 'failed'
    with demo.read() as c:
        kinds = [r[0] for r in c.execute('SELECT kind FROM conversation_evidence')]
    assert 'stream' in kinds
    if mode == 'stream_interrupted':
        client.chat.cancel.assert_awaited_once_with('native-message')
        assert 'failure' in kinds and 'terminal' not in kinds
    else:
        client.chat.cancel.assert_not_awaited()
        assert 'terminal' in kinds
        assert run.metadata['strategy_validation']['status'] == 'failed'
        assert len(cq.listing(demo)[0]['phases'][0]['questions']) == (0 if mode == 'no_questions' else 1)
    assert not reliability.score(demo, run.id, TRUTH)['reconciled']
    client.chat.start.assert_awaited_once()
    client.chat.send.assert_not_awaited()


def test_strategy_failed_capture_wins_heartbeat_completion_race(demo, tmp_path, monkeypatch):
    (tmp_path / 'agents').mkdir()
    (tmp_path / 'agents/discovery.md').write_bytes(INSTRUCTIONS)
    monkeypatch.setattr(run_impact, 'ROOT', tmp_path)
    async def worker(client, store, agent_id, connection, steps, rid, secret, *args, **kwargs):
        cq.bind(store, rid, 'thread', 'message')
        retrieve(store, store.run(rid), secret)
        pause(store, store.run(rid))
        return 1
    async def sleep(_): return None
    async def heartbeat_first(tasks, **kwargs):
        await asyncio.gather(*tasks, return_exceptions=True)
        beat = next(t for t in tasks if t.get_coro().__name__ == 'heartbeat')
        return {beat}, set(tasks) - {beat}
    monkeypatch.setattr(run_impact, '_paid_run', worker)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    monkeypatch.setattr(asyncio, 'wait', heartbeat_first)
    assert asyncio.run(run_impact.paid_run(None, demo, 'impact', 'existing-connection', 40, discovery=True)) == 1
    assert cq.listing(demo)[0]['state'] == 'failed'


def test_provider_snapshot_remains_available_when_consistency_check_rejects():
    run = SimpleNamespace(id='run-test')
    thread = SimpleNamespace(id='thread')
    message = SimpleNamespace(id='message', status=SimpleNamespace(value='success'), live=False, questions=[native()], content='')
    snapshot = evaluator.provider_evidence(run, thread, message)
    with pytest.raises(RuntimeError, match='mismatch'): evaluator.check_provider(snapshot, [])
    assert snapshot['questions'][0]['prompt'] == native().prompt
    evaluator.check_provider(snapshot, [{'questions': snapshot['questions']}])
    for update in [{'live': True}, {'status': 'failed'}]:
        with pytest.raises(RuntimeError, match='failed or remains live'):
            evaluator.check_provider({**snapshot, **update}, [{'questions': snapshot['questions']}])


def test_quality_failure_is_retained_but_trust_failures_stop_the_series(demo):
    run, secret = begin(demo, strategy=True)
    bad_strategy(demo, run, secret)
    pause(demo, run)
    result = reliability.score(demo, run.id, TRUTH)
    evaluator.check_trial(result)  # Accept the measurement, never certify the run.
    assert reliability.aggregate([result])['statistics']['accuracy']['mean'] == 0
    for field, bad in [('native_question_integrity', False), ('protected_automation_safe', False),
                       ('mcp_error_count', 1), ('unattributed_scoped_mcp_calls', 1)]:
        with pytest.raises(RuntimeError): evaluator.check_trial({**result, field: bad})
    with pytest.raises(RuntimeError): evaluator.check_trial(result, domain_unchanged=False)
    evaluator.check_trial({**result, 'unattributed_mcp_calls': 1, 'unattributed_scoped_mcp_calls': 0})


def test_stop_diagnostic_is_nonempty_structured_and_redacted():
    assert evaluator.stop_diagnostic(AssertionError())['error_type'] == 'AssertionError'
    secret = 'urt_' + 'a' * 43
    diagnostic = evaluator.stop_diagnostic(discovery.SearchStrategyViolation('contract failed', observed={'lead_followed': False}, token=secret))
    assert diagnostic['code'] == 'search_strategy_violation'
    assert diagnostic['details']['observed']['lead_followed'] is False
    assert secret not in json.dumps(diagnostic)


def test_failed_strategy_blocks_remote_continuation_preflight_before_provider_lookup(demo):
    run, secret = begin(demo, strategy=True)
    bad_strategy(demo, run, secret)
    pause(demo, run)
    # Even an accidentally restored UI label must not cause a remote read or dispatch.
    with demo.write() as c: c.execute("UPDATE conversations SET state='ready'")
    client = SimpleNamespace(threads=SimpleNamespace(get=AsyncMock()))
    with pytest.raises(workflow.Conflict):
        asyncio.run(run_impact.continuation_thread_ready(client, demo, run.id))
    client.threads.get.assert_not_awaited()


def test_old_lifecycle_protocol_is_rejected_without_rewriting_its_report(tmp_path, monkeypatch):
    for name, path in [('PRIVATE', tmp_path), ('PLAN', tmp_path / 'plan.json'),
                       ('PROTOCOL', tmp_path / 'protocol.json'), ('REPORT', tmp_path / 'report.json')]:
        monkeypatch.setattr(evaluator, name, path)
    evaluator.PLAN.write_text('{}')
    evaluator.PROTOCOL.write_text(json.dumps({'version': 'reliability-v1'}))
    evaluator.REPORT.write_text('preserved historical report')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with pytest.raises(RuntimeError, match='fresh versioned evaluation series'):
        asyncio.run(evaluator.execute())
    assert not (tmp_path / 'execution.lock').exists()
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


def test_fresh_series_paths_do_not_mix_archived_studies(tmp_path, monkeypatch):
    # Restore global selection after the test; never operate on a real study.
    for key in ['ROOT', 'SERIES', 'PRIVATE', 'PLAN', 'ACTIVE', 'PROTOCOL', 'REPORT']:
        monkeypatch.setattr(evaluator, key, getattr(evaluator, key))
    monkeypatch.setattr(evaluator, 'ROOT', tmp_path)
    monkeypatch.setattr(evaluation_service, 'ROOT', tmp_path)
    evaluator.select_series('reliability-v2')
    assert evaluator.REPORT == tmp_path / 'evaluations/reliability-v2-results.json'
    assert evaluator.PRIVATE == tmp_path / '.tools/reliability-v2'
    path = tmp_path / 'data/reliability-v2/evaluation-01.db'
    path.parent.mkdir(parents=True); path.touch()
    db, report = evaluation_service.series_paths({'series_id': 'reliability-v2', 'database': str(path)})
    assert db == path and report == evaluator.REPORT
    with pytest.raises(ValueError): evaluation_service.series_paths({'series_id': 'reliability-v1', 'database': str(path)})
    for name in ['../reliability-v1', 'reliability-v0', 'reliability-v2/evil']:
        with pytest.raises(ValueError): evaluator.select_series(name)


@pytest.mark.parametrize('fault,expected_count', [('quality_only', 5), ('persistence', 1), ('interrupted', 1), ('provider', 1)])
def test_five_slot_orchestration_retains_quality_failures_and_stops_trust_failures(tmp_path, monkeypatch, fault, expected_count):
    """Run the actual one-shot orchestration with local stores and a fake transport.

    No service launcher/network/real SDK request is called. The real dispatch guard,
    capture transaction, grader, report sink and stop/recovery paths still execute.
    """
    for key in ['ROOT', 'SERIES', 'PRIVATE', 'PLAN', 'ACTIVE', 'PROTOCOL', 'REPORT', 'TRUTH']:
        monkeypatch.setattr(evaluator, key, getattr(evaluator, key))
    monkeypatch.setattr(evaluator, 'ROOT', tmp_path)
    evaluator.select_series('reliability-v2')
    evaluator.PRIVATE.mkdir(parents=True)
    slots = []
    for i in range(1, 6):
        path = tmp_path / f'data/reliability-v2/evaluation-{i:02}.db'
        store = create_demo(path)
        slots.append({'slot': i, 'database': str(path.relative_to(tmp_path)), 'database_id': store.identity(), 'domain': evaluator.domain(store)})
    evaluator.write(evaluator.PLAN, {'series_id': evaluator.SERIES, 'slots': slots, 'protected_files': {}})
    evaluator.write(evaluator.PROTOCOL, {'series_id': evaluator.SERIES, 'lifecycle': evaluator.LIFECYCLE,
                                       'remote': {}, 'answer_fixture_sha256': 'fixed'})
    evaluator.write(evaluator.PRIVATE / 'answers.v1.json', {'answers': {}})
    truth = tmp_path / 'truth.json'; evaluator.write(truth, TRUTH)
    monkeypatch.setattr(evaluator, 'TRUTH', truth)
    monkeypatch.setattr(evaluator, 'assert_frozen', lambda *a: None)  # Drift guards separately tested.
    current = {}
    monkeypatch.setattr(evaluator, 'configure', lambda slot: current.update(store=Store(tmp_path / slot['database'])))
    monkeypatch.setattr(evaluator, 'switch', lambda slot: None)
    monkeypatch.setattr(evaluator, 'wait_service', AsyncMock())
    monkeypatch.setattr(evaluator, 'attestation', AsyncMock(return_value={}))
    def no_answers(*a, **kw): raise AssertionError('A failed strategy must never receive fixture answers')
    monkeypatch.setattr(evaluator, 'supply_answers', no_answers)
    class FakeSdk:
        def __init__(self, **kw):
            self._transport = SimpleNamespace(request=AsyncMock(return_value={
                'id': 'thread', 'messages': [{'role': 'assistant', 'id': 'message'}]}))
    monkeypatch.setattr(evaluator, 'AsyncCominty', FakeSdk)
    async def fake_main():
        store = current['store']
        run, secret = workflow.open_run(store, 'shiftly-returns', store.document_ids(), actor='test', agent_id='impact',
                                       metadata={'discovery': True, 'discovery_strategy': 'search_first_v1', 'max_steps': 40})
        cq.create(store, run.id, 'connection', INSTRUCTIONS)
        client = run_impact.AsyncCominty()
        await client._transport.request('POST', '/chat', json_body={'options': {'max_steps': 40,
            'capabilities': {'mcp': {'connections': [evaluator.CONNECTION], 'activation': 'always'}, 'web': {'activation': 'never'}}}})
        cq.bind(store, run.id, 'thread', 'message')
        workflow.heartbeat_run(store, run.id, provenance={'thread_id': 'thread', 'message_id': 'message'})
        bad_strategy(store, run, secret)
        pause(store, run)
        if fault == 'persistence':
            with store.write() as c: c.execute('DELETE FROM clarification_questions')
        if fault == 'interrupted': raise KeyboardInterrupt()
        return 1
    monkeypatch.setattr(run_impact, 'main', fake_main)
    async def get_thread(thread_id):
        return SimpleNamespace(id='thread', messages=[SimpleNamespace(id='message', status=SimpleNamespace(value='success'),
            live=False, questions=[] if fault == 'provider' else [native()], content='')])
    class ReadOnlyFake:
        def __init__(self, **kw): self.threads = SimpleNamespace(get=AsyncMock(side_effect=get_thread))
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
    monkeypatch.setattr(evaluator, 'ReadOnlyCominty', ReadOnlyFake)
    asyncio.run(evaluator.execute())
    report = json.loads(evaluator.REPORT.read_text())
    assert report['attempted_paid_messages'] == expected_count
    assert len(report['results']) == expected_count
    assert all(not r['reconciled'] and r['metrics']['accuracy'] == 0 for r in report['results'])
    assert len(list(evaluator.PRIVATE.glob('attempt-*.json'))) == expected_count
    assert not list(evaluator.PRIVATE.glob('attempt-*-2.json'))
    assert (evaluator.PRIVATE / 'provider-1-1.json').is_file()
    assert report['aggregate']['statistics']['accuracy']['mean'] == 0
    assert report['state'] == ('completed' if fault == 'quality_only' else 'stopped')
    if fault != 'quality_only': assert report['stop_reason'] and report['stop_diagnostic']
    # One-shot lock remains after both completed and stopped series.
    with pytest.raises(FileExistsError): asyncio.run(evaluator.execute())
