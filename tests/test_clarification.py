"""Native SDK contract tests; all executions are fakes, all databases are isolated."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cominty_sdk import events
from cominty_sdk.models.chat import Question
from cominty_sdk.resources.chat import ChatResource
from cominty_sdk.streaming import AssistantRun
from mcp import Client
import pytest
from starlette.testclient import TestClient

import api
import clarification as cq
import discovery
from discovery_seed import create_demo
import readmodel
import run_impact
from scripts.evaluate_clarification import metrics, recognition_metrics, supply_answers
from store import Store
import workflow
import server
from test_discovery import CORRECTION, payload, reply
from test_runner import FakeRun
from test_workflow import edit_document_externally

INSTRUCTIONS = b'isolated native question instruction candidate'


@pytest.fixture
def demo(tmp_path):
    return create_demo(tmp_path / 'clarification.db')


def retrieve(store, run, token):
    for kind, did in [('policy', run.policy_id), *[('document', did) for did in run.document_ids]]:
        workflow.retrieve_for_run(store, run_id=run.id, run_token=token, kind=kind, subject_id=did, actor='mcp:test')


def begin(store, *, ids=None, strategy=False):
    run, token = workflow.open_run(store, 'shiftly-returns', ids or ['doc-001', 'help-002'],
        actor='agent:test', agent_id='impact', metadata={'discovery': True, 'max_steps': 40,
                    **({'discovery_strategy': 'search_first_v1'} if strategy else {})})
    cq.create(store, run.id, 'existing-connection', INSTRUCTIONS)
    cq.bind(store, run.id, 'same-thread', 'initial-message')
    workflow.heartbeat_run(store, run.id, provenance={'thread_id': 'same-thread', 'message_id': 'initial-message'})
    retrieve(store, run, token)
    return run, token


def native(did='help-002', version=1):
    return Question(prompt=f'[document:{did};version:{version}] What return window did the original offer promise?',
                    options=['The original offer promised 60 days for this earlier purchase.', 'I cannot establish this fact.'])


def pause(store, run, questions=None, cost=True):
    questions = questions or [native()]
    cq.evidence(store, run.id, 'terminal', {'status': 'success',
        'questions': [q.model_dump() for q in questions]})
    if cost:
        cq.reported_cost(store, run.id, {'total': '0.2', 'failed': False})
    cq.capture(store, run.id, questions, actor='agent:test', metadata={'execution_status': 'success'})
    return cq.listing(store)[0]['phases'][-1]['questions']


def ready(store, token):
    run, secret = begin(store)
    [q] = pause(store, run)
    cq.answer(store, q['id'], token=token, option=0)
    return run, secret, q


def resume(store, run):
    return cq.prepare_continuation(store, run.id, agent_id='impact', connection='existing-connection',
                                   instructions=INSTRUCTIONS, max_steps=20)


def test_native_capture_revokes_credentials_without_finalizing_or_proposals(demo):
    run, secret = begin(demo)
    workflow.record_finding(demo, **payload(demo, run, secret, proposed_content=CORRECTION))
    [q] = pause(demo, run)
    assert q['prompt'] == native().prompt and q['options'] == native().options
    assert q['evidence'] == demo.document('help-002').content
    assert demo.run(run.id).status == 'incomplete'
    assert not workflow.verified_finding(demo.run(run.id), demo.findings()[0])
    assert not demo.proposals()
    with pytest.raises(workflow.Unauthorized):
        workflow.retrieve_for_run(demo, run_id=run.id, run_token=secret, kind='policy', subject_id=run.policy_id, actor='mcp:test')


@pytest.mark.parametrize('question', [Question(prompt='Which offer?', options=[]), native('crm-004'), native(version=2),
    Question(prompt=native().prompt, options=['same', 'same']), Question(prompt=native().prompt, options=['urt_' + 'a' * 43])])
def test_unbound_stale_cross_scope_or_secret_questions_fail_closed(demo, question):
    run, _ = begin(demo)
    with pytest.raises(workflow.WorkflowError):
        pause(demo, run, [question])
    assert not cq.listing(demo)[0]['phases'][0]['questions']
    assert demo.run(run.id).status == 'open' and not demo.proposals()


def test_native_question_requires_authenticated_retrieval(demo):
    run, _ = begin(demo)
    with demo.write() as c:
        c.execute('DELETE FROM run_retrievals WHERE run_id=?', (run.id,))
    with pytest.raises(workflow.Conflict, match='retrieved'):
        pause(demo, run)


def test_native_question_and_option_whitespace_is_preserved_for_remote_identity(demo, token):
    run, _ = begin(demo)
    question = Question(prompt=native().prompt + '\n', options=[' Original offer was 60 days. ', 'Unknown'])
    [stored] = pause(demo, run, [question])
    assert stored['prompt'] == question.prompt and stored['options'] == question.options
    supply_answers(demo, run.id, {'help-002': {'option': question.options[0]}}, token=token)
    assert cq.listing(demo)[0]['phases'][0]['questions'][0]['answer'] == question.options[0]


def test_reviewer_api_option_free_text_and_duplicate_authorization(demo, token):
    run, _ = begin(demo)
    [q] = pause(demo, run)
    client = TestClient(api.build_app(demo, ui_dir=None), base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    url = f'/api/questions/{q["id"]}/answer'
    assert client.post(url, json={'option': 0}).status_code == 401
    headers = {'Authorization': f'Bearer {token}'}
    for body in ({'option': 100}, {'option': True}, {'option': 0, 'text': 'x'}, {}, {'text': token}):
        assert client.post(url, headers=headers, json=body).status_code == 400
    assert client.post(url, headers=headers, json={'text': 'The original terms cannot be established.'}).status_code == 200
    assert client.post(url, headers=headers, json={'text': 'The original terms cannot be established.'}).json()['status'] == 'already_answered'
    assert client.post(url, headers=headers, json={'option': 0}).status_code == 409
    conv = client.get('/api/conversations').json()['conversations'][0]
    assert conv['state'] == 'ready' and len(demo.runs()) == 1
    assert len(demo.events(type_prefix='agent.question_answered')) == 1
    assert 'The original terms' not in json.dumps([e.model_dump(mode='json') for e in demo.events()])


def test_concurrent_identical_answers_are_idempotent(demo, token):
    run, _ = begin(demo)
    [q] = pause(demo, run)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: cq.answer(demo, q['id'], token=token, option=0), range(2)))
    assert sorted(r['status'] for r in results) == ['already_answered', 'answered']


def test_all_questions_need_answers_and_old_answers_never_mutate(demo, token):
    run, _ = begin(demo, ids=['help-002', 'help-004'])
    questions = pause(demo, run, [native('help-002'), native('help-004')])
    cq.answer(demo, questions[0]['id'], token=token, option=0)
    with pytest.raises(workflow.Conflict): resume(demo, run)
    cq.answer(demo, questions[1]['id'], token=token, option=1)
    new, secret, thread, answers = resume(demo, run)
    assert new.id != run.id and thread == 'same-thread' and len(answers) == 2
    assert new.metadata['parent_run_id'] == run.id
    retrieve(demo, new, secret)
    with pytest.raises(workflow.Conflict): cq.answer(demo, questions[0]['id'], token=token, option=1)


def test_resume_with_expired_old_credentials_uses_fresh_capability(demo, token):
    run, old, _ = ready(demo, token)
    before = demo.run(run.id).model_dump()
    with demo.write() as c:
        c.execute('UPDATE agent_runs SET expires_at=? WHERE id=?', ((datetime.now(UTC) - timedelta(days=1)).isoformat(), run.id))
    new, secret, _, _ = resume(demo, run)
    assert old != secret and new.expires_at > datetime.now(UTC)
    retrieve(demo, new, secret)
    assert demo.run(run.id).status == before['status']
    with pytest.raises(workflow.Unauthorized):
        workflow.retrieve_for_run(demo, run_id=new.id, run_token=old, kind='policy', subject_id=new.policy_id, actor='mcp:test')


@pytest.mark.parametrize('kind', ['document', 'policy'])
def test_stale_context_cannot_answer_or_continue(demo, token, kind):
    run, _ = begin(demo)
    [q] = pause(demo, run)
    cq.answer(demo, q['id'], token=token, option=0)
    if kind == 'document':
        edit_document_externally(demo, 'help-002', 'Changed terms.')
    else:
        with demo.write() as c: c.execute('UPDATE policies SET current_version=1')
    with pytest.raises(workflow.Conflict, match='stale'):
        resume(demo, run)
    with pytest.raises(workflow.Conflict): cq.answer(demo, q['id'], token=token, option=0)


@pytest.mark.parametrize('change', ['instructions', 'connection', 'agent', 'cost_missing', 'cost_failed', 'cost_limit', 'steps', 'turns'])
def test_configuration_and_aggregate_budget_gates(demo, token, change):
    run, _, _ = ready(demo, token)
    kwargs = dict(agent_id='impact', connection='existing-connection', instructions=INSTRUCTIONS, max_steps=20)
    if change in {'instructions', 'connection', 'agent'}:
        kwargs[{'instructions': 'instructions', 'connection': 'connection', 'agent': 'agent_id'}[change]] = b'changed' if change == 'instructions' else 'changed'
    if change == 'cost_missing': cq.reported_cost(demo, run.id, None)
    if change == 'cost_failed': cq.reported_cost(demo, run.id, {'total': '0.2', 'failed': True})
    if change == 'cost_limit': cq.reported_cost(demo, run.id, {'total': '2'})
    if change == 'steps': kwargs['max_steps'] = 41
    if change == 'turns':
        with demo.write() as c: c.execute('UPDATE conversations SET max_turns=1')
    if change.startswith('cost_') or change == 'turns':
        assert cq.listing(demo)[0]['continuation_blocker']
    with pytest.raises(workflow.Conflict): cq.prepare_continuation(demo, run.id, **kwargs)
    assert len(demo.runs()) == 1


def test_concurrent_continuations_reserve_exactly_one_turn(demo, token):
    run, _, _ = ready(demo, token)
    def worker(_):
        try: return resume(demo, run)[0].id
        except workflow.Conflict: return 'conflict'
    with ThreadPoolExecutor(2) as pool: results = list(pool.map(worker, range(2)))
    assert results.count('conflict') == 1 and len(demo.runs()) == 2


def test_killed_continuation_is_not_blindly_dispatched_again(demo, token):
    run, _, _ = ready(demo, token)
    new, _, _, _ = resume(demo, run)
    with demo.write() as c:
        c.execute('UPDATE agent_runs SET lease_until=? WHERE id=?', ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), new.id))
    demo.reap_runs()
    assert cq.listing(demo)[0]['state'] == 'interrupted'
    with pytest.raises(workflow.Conflict): resume(demo, run)


def test_repeated_native_capture_cannot_duplicate_or_reopen_phase(demo):
    run, _ = begin(demo)
    [q] = pause(demo, run)
    with pytest.raises(workflow.Conflict):
        cq.capture(demo, run.id, [native()], actor='runner', metadata={})
    assert cq.listing(demo)[0]['phases'][0]['questions'][0]['id'] == q['id']


def test_parent_retrieval_does_not_authorize_continuation_results(demo, token):
    run, _, _ = ready(demo, token)
    new, _, _, _ = resume(demo, run)
    result = workflow.ingest_agent_reply(demo, reply(demo, new), actor='runner', provenance={},
                                        run_id=new.id, execution_status='success')
    cq.finish(demo, new.id)
    assert result['errors'] and not demo.proposals()
    assert not demo.run(new.id).metadata.get('verified_document_ids')


def test_aggregate_step_budget_not_reset_for_each_follow_up(demo, token):
    run, _, _ = ready(demo, token)
    new, fresh, _, _ = resume(demo, run)
    cq.bind(demo, new.id, 'same-thread', 'second-message')
    retrieve(demo, new, fresh)
    [q] = pause(demo, new)
    cq.answer(demo, q['id'], token=token, option=1)
    with pytest.raises(workflow.Conflict, match='budget'):
        cq.prepare_continuation(demo, run.id, agent_id='impact', connection='existing-connection',
                                instructions=INSTRUCTIONS, max_steps=21)  # 40+20+21 > 80
    third, _, _, _ = resume(demo, run)
    assert cq.phase(demo, third.id)['ordinal'] == 3


@pytest.mark.parametrize('changed', ['message', 'questions', 'live', 'status'])
def test_remote_thread_drift_blocks_paid_dispatch(demo, token, changed):
    run, _, _ = ready(demo, token)
    message = SimpleNamespace(id='initial-message', status='success', live=False, questions=[native()])
    setattr(message, {'message': 'id', 'questions': 'questions', 'live': 'live', 'status': 'status'}[changed],
            {'message': 'foreign-message', 'questions': [], 'live': True, 'status': 'failed'}[changed])
    client = SimpleNamespace(threads=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id='same-thread', messages=[message]))))
    with pytest.raises(workflow.Conflict, match='changed'):
        asyncio.run(run_impact.continuation_thread_ready(client, demo, run.id))
    assert len(demo.runs()) == 1


def test_terminal_failure_with_questions_never_becomes_waiting_for_review(demo, tmp_path, monkeypatch):
    (tmp_path / 'agents').mkdir()
    (tmp_path / 'agents/discovery.md').write_bytes(INSTRUCTIONS)
    monkeypatch.setattr(run_impact, 'ROOT', tmp_path)
    class FailedQuestionRun(NativeRun):
        async def result(self):
            return SimpleNamespace(content='', status=SimpleNamespace(value='failed'), questions=[native()])
    client = SimpleNamespace(chat=SimpleNamespace(start=AsyncMock(return_value=FailedQuestionRun([native()])), cancel=AsyncMock()))
    assert asyncio.run(run_impact.paid_run(client, demo, 'impact', 'existing-connection', 40, discovery=True)) == 1
    assert cq.listing(demo)[0]['state'] == 'failed'
    assert not cq.listing(demo)[0]['phases'][0]['questions'] and not demo.proposals()


def test_fresh_retrieval_final_reconciliation_and_protected_channel_isolation(demo, token):
    run, secret = begin(demo, ids=['doc-001', 'help-002', 'crm-004'])
    workflow.record_finding(demo, **payload(demo, run, secret, 'help-002'))
    initial = demo.findings(run_id=run.id)[0].model_dump()
    [q] = pause(demo, run)
    cq.answer(demo, q['id'], token=token, option=0)
    new, fresh, _, _ = resume(demo, run)
    retrieve(demo, new, fresh)
    categories = {'doc-001': 'OUTDATED', 'help-002': 'EXISTING_COMMITMENT', 'crm-004': 'OUTDATED'}
    for did, category in categories.items():
        values = payload(demo, new, fresh, did)
        values['classification'] = category
        if did == 'doc-001': values['proposed_content'] = CORRECTION
        workflow.record_finding(demo, **values)
    result = workflow.ingest_agent_reply(demo, reply(demo, new, categories, {'doc-001': CORRECTION}),
        actor='agent:test', provenance={}, run_id=new.id, execution_status='success')
    cq.finish(demo, new.id)
    assert len(result['proposals']) == 1
    assert result['document_outcomes']['crm-004']['action'] == 'manual_only'
    assert result['document_outcomes']['help-002']['action'] == 'no_change'
    assert demo.findings(run_id=run.id)[0].model_dump() == initial
    snapshot = readmodel.impact_snapshot(demo, run.policy_id)
    assert next(d for d in snapshot['documents'] if d['id'] == 'help-002')['finding']['run_id'] == new.id
    assert cq.listing(demo)[0]['state'] == 'incomplete'  # CRM still needs manual handling.


def test_evaluation_uses_real_native_capture_and_keeps_labels_out_of_answers(demo, token):
    run, _ = begin(demo)
    pause(demo, run)
    assert metrics(demo, run.id)['native_question_count'] == 1
    results = supply_answers(demo, run.id, {'help-002': {'option': native().options[1]}}, token=token)
    assert results[0]['status'] == 'answered' and metrics(demo, run.id)['answered_count'] == 1
    assert len(demo.runs()) == 1  # No paid continuation implicit in evaluation.
    score = recognition_metrics(demo, run.id, {'help-002': 'UNKNOWN', 'doc-001': 'OUTDATED'})
    assert score['unknown_question_recall'] == score['question_precision'] == 1
    assert score['post_answer_classifications'] == {}  # Questions are not synthetic findings.


def test_evaluation_refuses_missing_native_evidence_and_category_payload(demo, token):
    run, _ = begin(demo)
    pause(demo, run)
    with pytest.raises(workflow.WorkflowError):
        supply_answers(demo, run.id, {'help-002': {'category': 'UNKNOWN', 'text': 'x'}}, token=token)
    with demo.write() as c: c.execute('DELETE FROM conversation_evidence')
    with pytest.raises(workflow.Conflict, match='evidence'):
        supply_answers(demo, run.id, {'help-002': {'text': 'Cannot establish'}}, token=token)


def test_schema_four_migration_is_additive_and_idempotent(demo):
    run, secret = begin(demo)
    workflow.record_finding(demo, **payload(demo, run, secret, proposed_content=CORRECTION))
    baseline = [demo.run(run.id).model_dump(), demo.findings()[0].model_dump(), demo.identity()]
    with demo.write() as c:
        for table in ['clarification_questions', 'conversation_evidence', 'conversation_phases', 'conversations']:
            c.execute(f'DROP TABLE {table}')
        c.execute('PRAGMA user_version=4')
    demo.init(seed=False)
    demo.init(seed=False)
    assert baseline == [demo.run(run.id).model_dump(), demo.findings()[0].model_dump(), demo.identity()]
    assert cq.listing(demo) == []
    with demo.read() as c: assert not c.execute('PRAGMA foreign_key_check').fetchall()


class NativeRun(FakeRun):
    def __init__(self, questions, reply_text='', message_id='native-message', crash=False):
        super().__init__(reply_text, crash=crash)
        self.native_questions = questions
        self.message_id = message_id

    async def __aiter__(self):
        yield events.Result.model_validate({'name': 'result', 'id': '1-0', 'correlation_id': 1,
            'at': datetime.now(UTC), 'status': 'success', 'data': {'reply': self.reply,
            'cost': {'total': '0.1', 'failed': False}, 'questions': [q.model_dump() for q in self.native_questions]}})
        if self.crash: raise RuntimeError('interrupted continuation')

    async def result(self):
        return SimpleNamespace(content=self.reply, questions=self.native_questions, status=SimpleNamespace(value='success'))


def strategy(store, run, secret):
    for query in ['returns period', 'earlier promises', 'eligibility conditions']:
        discovery.search_documents(store, run_id=run.id, run_token=secret, query=query)
    retrieve(store, run, secret)
    discovery.search_documents(store, run_id=run.id, run_token=secret, query='', limit=25)


@pytest.mark.parametrize('crash', [False, True])
def test_actual_sdk_question_contract_to_same_thread_send_and_persistent_cost(demo, token, tmp_path, monkeypatch, crash):
    (tmp_path / 'agents').mkdir()
    (tmp_path / 'agents/discovery.md').write_bytes(INSTRUCTIONS)
    monkeypatch.setattr(run_impact, 'ROOT', tmp_path)
    async def start(**kwargs):
        rid = re.search(r'Run ID: (\S+)', kwargs['message'])[1]
        secret = re.search(r'Run token: (\S+)', kwargs['message'])[1]
        strategy(demo, demo.run(rid), secret)
        return NativeRun([native()])
    async def send(thread_id, **kwargs):
        assert thread_id == 'external-thread' and kwargs['max_steps'] == 20 and kwargs['agent_id'] == 'impact'
        rid = re.search(r'Run ID: (\S+)', kwargs['message'])[1]
        secret = re.search(r'Run token: (\S+)', kwargs['message'])[1]
        run = demo.run(rid)
        retrieve(demo, run, secret)
        assert native().options[1] in kwargs['message']
        return NativeRun([], reply(demo, run), message_id='follow-up-message', crash=crash)
    async def get_thread(thread_id):
        return SimpleNamespace(id=thread_id, messages=[SimpleNamespace(id='native-message',
            status='success', live=False, questions=[native()])])
    client = SimpleNamespace(chat=SimpleNamespace(start=AsyncMock(side_effect=start), send=AsyncMock(side_effect=send), cancel=AsyncMock()),
                             threads=SimpleNamespace(get=AsyncMock(side_effect=get_thread)))
    assert asyncio.run(run_impact.paid_run(client, demo, 'impact', 'existing-connection', 40, discovery=True)) == 3
    [conv] = cq.listing(demo)
    [question] = conv['phases'][0]['questions']
    cq.answer(demo, question['id'], token=token, option=1)
    initial = demo.run(conv['id']).model_dump()
    if crash:
        with pytest.raises(RuntimeError):
            asyncio.run(run_impact.paid_run(client, demo, 'impact', 'existing-connection', 20,
                                          discovery=True, conversation_id=conv['id']))
        client.chat.cancel.assert_awaited_once_with('follow-up-message')
    else:
        assert asyncio.run(run_impact.paid_run(client, demo, 'impact', 'existing-connection', 20,
                        discovery=True, conversation_id=conv['id'])) == 1  # Honest UNKNOWN + protected manual correction.
        assert demo.run(demo.runs()[-1].id).metadata['finalization'] == 'per_document_v1'
    [after] = cq.listing(demo)
    assert [p['message_id'] for p in after['phases']] == ['native-message', 'follow-up-message']
    assert all(p['cost']['total'] == '0.1' for p in after['phases'])
    assert demo.run(conv['id']).model_dump() == initial
    with demo.read() as c:
        raw = json.dumps([dict(r) for r in c.execute('SELECT * FROM conversation_evidence')])
        assert 'urt_' not in raw and 'follow-up-message' in json.dumps(after)
    client.chat.start.assert_awaited_once()
    client.chat.send.assert_awaited_once()


def test_real_sdk_assistant_questions_and_send_wire_contract_without_network():
    """Use SDK implementations, replacing only the network transport."""
    message_id = '00000000-0000-4000-8000-000000000001'
    thread_id = '00000000-0000-4000-8000-000000000002'
    terminal = {'id': message_id, 'thread_id': thread_id, 'role': 'assistant', 'content': '',
                'error_code': None, 'questions': [native().model_dump()], 'live': False,
                'status': 'success', 'events': None, 'structured_output': None, 'files': [], 'agent': None}
    class Transport:
        request = AsyncMock(return_value=terminal)
        @asynccontextmanager
        async def stream_lines(self, method, path, headers=None):
            assert method == 'GET' and path == f'/chat/messages/{message_id}/stream'
            async def lines(): yield terminal
            yield lines()
    async def exercise():
        transport = Transport()
        handle = AssistantRun(transport, message_id)
        assert (await handle.questions())[0] == native()
        chat = ChatResource(transport, user_id='user_' + 'x' * 24)
        follow_up = await chat.send(thread_id, message='A factual answer', agent_id='impact', max_steps=20)
        assert str(follow_up.message_id) == message_id and follow_up.thread is None
        transport.request.assert_awaited_once()
        call = transport.request.call_args
        assert call.args == ('POST', f'/chat/{thread_id}')
        assert call.kwargs['json_body']['message']['content'] == 'A factual answer'
        assert call.kwargs['json_body']['options']['max_steps'] == 20
    asyncio.run(exercise())


def test_clarification_through_mcp_answer_api_finalization_and_impact_projection(demo, token):
    original = server._state['store']
    server.use_store(demo)
    server.enable_discovery_tools(); server.enable_findings_tool(None)
    run, old = begin(demo)
    pause(demo, run)
    client = TestClient(api.build_app(demo, ui_dir=None), base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    question = client.get('/api/conversations').json()['conversations'][0]['phases'][0]['questions'][0]
    headers = {'Authorization': f'Bearer {token}'}
    assert client.post(f'/api/questions/{question["id"]}/answer', headers=headers, json={'option': 0}).status_code == 200
    new, fresh, _, _ = resume(demo, run)
    async def exercise():
        async with Client(server.mcp) as mcp:
            credentials = {'run_id': new.id, 'run_token': fresh}
            assert (await mcp.call_tool('get_policy', {'run_id': run.id, 'run_token': old, 'policy_id': run.policy_id})).is_error
            assert not (await mcp.call_tool('get_policy', {**credentials, 'policy_id': run.policy_id})).is_error
            for did in new.document_ids:
                assert not (await mcp.call_tool('get_document', {**credentials, 'document_id': did})).is_error
                values = payload(demo, new, fresh, did)
                values.pop('actor')
                if did == 'doc-001': values['proposed_content'] = CORRECTION
                else: values['classification'] = 'EXISTING_COMMITMENT'
                assert not (await mcp.call_tool('record_finding', values)).is_error
    try:
        asyncio.run(exercise())
        final = reply(demo, new, {'doc-001': 'OUTDATED', 'help-002': 'EXISTING_COMMITMENT'}, {'doc-001': CORRECTION})
        result = workflow.ingest_agent_reply(demo, final, actor='runner', provenance={}, run_id=new.id, execution_status='success')
        cq.finish(demo, new.id)
        assert len(result['proposals']) == 1 and demo.run(new.id).status == 'completed'
        docs = {d['id']: d for d in client.get('/api/policies/shiftly-returns/impact').json()['documents']}
        assert docs['help-002']['finding']['verified_conclusion']
        assert docs['help-002']['finding']['category'] == 'EXISTING_COMMITMENT'
        assert all(f.source == 'mcp' for f in demo.findings(run_id=new.id))
        assert client.get('/api/conversations').json()['conversations'][0]['state'] == 'completed'
    finally:
        for name in server.DISCOVERY_TOOLS + ['record_finding']: server.mcp.remove_tool(name)
        server._state['discovery_tools'] = server._state['findings_tool'] = False
        server.use_store(original)
