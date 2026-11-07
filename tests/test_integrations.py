"""Cominty Memory and Slack notifications. Test doubles only: no network, no paid execution."""
import asyncio
import json
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import api
import guard
import integrations
import workflow as w
from discovery_seed import create_demo
from tests.conftest import propose

B = 'Orders purchased on or after November 4, 2026 are eligible for the standard 30-day voluntary return window.'
A = 'All customers have 60 days to return their order.'


@pytest.fixture(autouse=True)
def no_live_env(monkeypatch):
    for name in ('UNDRIFT_INTEGRATIONS_LIVE', 'UNDRIFT_INTEGRATIONS_DB', 'UNDRIFT_NOTIFY_LIVE', 'UNDRIFT_NOTIFY_AGENT_ID',
                 'UNDRIFT_SLACK_CHANNEL', 'UNDRIFT_SLACK_CONNECTION', 'UNDRIFT_GUARD_LIVE', 'UNDRIFT_GUARD_AGENT_ID',
                 'UNDRIFT_GUARD_DB', 'UNDRIFT_MEMORY_NAMESPACE', 'UNDRIFT_REVIEW_BASE_URL'):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def demo(tmp_path):
    return create_demo(tmp_path / 'demo.db')


@pytest.fixture
def gj(tmp_path):
    return guard.GuardJournal(tmp_path / 'guard-journal.db')


@pytest.fixture
def ij(tmp_path):
    return integrations.IntegrationJournal(tmp_path / 'integrations.db')


# ---- fake native Cominty Memory (SDK-shaped) ---------------------------------------------

class FakeComintyBackend:
    def __init__(self):
        self.files, self.calls = {}, []
        backend = self

        class Memory:
            async def list(self, *, namespace=None):
                backend.calls.append(('list', namespace))
                return [SimpleNamespace(**{k: v for k, v in f.items() if k != 'content'}) for (ns, _), f in backend.files.items()
                        if namespace in (None, ns)]

            async def get(self, path, *, namespace):
                from cominty_sdk import NotFoundError
                if (namespace, path) not in backend.files:
                    raise NotFoundError('missing', status_code=404)
                return SimpleNamespace(**backend.files[(namespace, path)])

            async def create(self, *, path, namespace, purpose, content):
                from cominty_sdk import ConflictError
                backend.calls.append(('create', namespace, path))
                assert len(purpose) <= 100, 'Cominty rejects purposes over 100 characters'
                if (namespace, path) in backend.files:
                    raise ConflictError('exists', status_code=409)
                f = {'path': path, 'namespace': namespace, 'purpose': purpose, 'content': content,
                     'created_at': '2026-11-07T10:00:00Z', 'updated_at': '2026-11-07T10:00:00Z', 'version': f'v{len(backend.files) + 1}'}
                backend.files[(namespace, path)] = f
                return SimpleNamespace(**f)

        class Mcp:
            async def list(self, *, enabled=True):
                return [SimpleNamespace(name='slack_official', title='Slack Official', is_enabled=False, origin='builtin',
                                        requires_oauth_connect=True, connection_status=None, mcp_url='https://secret.example/mcp/x'),
                        SimpleNamespace(name='slack', title='Slack Legacy', is_enabled=False, origin='builtin',
                                        requires_oauth_connect=False, connection_status=None, mcp_url='https://secret.example/2')]

        class Client:
            memory, mcp = Memory(), Mcp()
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        self.factory = lambda: Client()


@pytest.fixture
def cominty():
    return FakeComintyBackend()


@pytest.fixture
def memory(cominty):
    return integrations.ComintyMemory('undrift-test', cominty.factory)


def guard_reply(case, verdict, assessment):
    out = {'contract': guard.OUTPUT_CONTRACT, 'check_id': case['check_id'], 'candidate_sha256': case['candidate']['sha256'],
           'policy': {'id': case['policy']['id'], 'version': case['policy']['version']}, 'verdict': verdict,
           'findings': [{'claim': case['candidate']['text'], 'assessment': assessment, 'evidence_refs': ['P'],
                         'reason': 'Checked against P.'}], 'uncertainty': None}
    return 'ok\n```json\n' + json.dumps(out) + '\n```'


class FakeRealGuard:
    """Stands in for a real Cominty Guard execution (source 'cominty')."""
    kind, paid = 'cominty', True

    def __init__(self, verdict='ALLOW', assessment='SUPPORTED', source='cominty'):
        self.verdict, self.assessment, self.source = verdict, assessment, source

    def execute(self, case):
        return guard.Execution('success', self.source, guard_reply(case, self.verdict, self.assessment),
                               {'agent_id': 'guard-x', 'thread_id': 't-1', 'message_id': 'm-1'})


def check(demo, gj, executor, text=B, channel='help-center'):
    return guard.run_check(demo, gj, executor, candidate=text, channel_id=channel, requested_by='rev', confirm_paid=True)


# ---- memory -------------------------------------------------------------------------------

def test_verified_cominty_interpretation_is_stored_in_native_memory_with_provenance(demo, gj, ij, memory, cominty):
    c = check(demo, gj, FakeRealGuard())
    saved = integrations.save_guard_interpretation(demo, gj, ij, memory, check_id=c['id'], actor='rev')
    path = saved['file']['path']
    assert path.startswith('shiftly-returns-v2/guard-') and ('create', 'undrift-test', path) in cominty.calls
    record = integrations.parse_memory(cominty.files[('undrift-test', path)]['content'])
    assert record['policy']['version'] == 2 and record['verdict'] == 'ALLOW'
    assert record['source'] == {'guard_check_id': c['id'], 'database_id': demo.identity(), 'agent_id': 'guard-x',
                                'thread_id': 't-1', 'message_id': 'm-1'}
    write = ij.rows('memory_writes', demo.identity())[0]
    assert write['cominty_version'] == 'v1' and write['source_check_id'] == c['id']
    assert any(e.type == 'memory.saved' for e in demo.events(type_prefix='memory.'))
    with pytest.raises(w.Conflict, match='already exists'):
        integrations.save_guard_interpretation(demo, gj, ij, memory, check_id=c['id'], actor='rev')


@pytest.mark.parametrize('executor, text', [
    (guard.DisabledExecutor(), B),                       # fail-closed, no agent verdict
    (FakeRealGuard(source='test_fixture'), B),           # test fixture, not Cominty
    (FakeRealGuard('NEEDS_REVIEW', 'INSUFFICIENT_EVIDENCE'), B),  # unresolved
    (FakeRealGuard(), A),                                # precheck escalates over agent ALLOW
])
def test_unverified_or_unresolved_results_are_never_remembered(demo, gj, ij, memory, cominty, executor, text):
    c = check(demo, gj, executor, text)
    with pytest.raises(w.Conflict):
        integrations.save_guard_interpretation(demo, gj, ij, memory, check_id=c['id'], actor='rev')
    assert cominty.files == {} and ij.rows('memory_writes', demo.identity()) == []


def test_memory_from_obsolete_policy_is_historical_and_cannot_be_saved_as_current(demo, gj, ij, memory, token):
    c = check(demo, gj, FakeRealGuard())
    integrations.save_guard_interpretation(demo, gj, ij, memory, check_id=c['id'], actor='rev')
    assert [f['status'] for f in integrations.memory_snapshot(demo, ij, memory)['files']] == ['authoritative']
    later = check(demo, gj, FakeRealGuard(), B.replace('standard ', ''))
    w.change_policy(demo, 'shiftly-returns', voluntary_return_days=21, effective_from=date(2026, 12, 1),
                    description='Voluntary returns 21 days from 2026-12-01; statutory 14-day right unchanged.', token=token)
    assert [f['status'] for f in integrations.memory_snapshot(demo, ij, memory)['files']] == ['historical']
    with pytest.raises(w.Conflict, match='obsolete policy'):
        integrations.save_guard_interpretation(demo, gj, ij, memory, check_id=later['id'], actor='rev')


def test_foreign_or_malformed_memory_is_unverified(demo, ij, memory, cominty):
    cominty.files[('undrift-test', 'notes/guess.md')] = {'path': 'notes/guess.md', 'namespace': 'undrift-test', 'purpose': 'x',
                                                          'content': 'Probably 45 days?', 'created_at': 'x', 'updated_at': 'x', 'version': 'v9'}
    files = integrations.memory_snapshot(demo, ij, memory)['files']
    assert files[0]['status'] == 'unverified' and files[0]['written_by_this_service'] is False


def test_recall_finds_saved_interpretation_but_never_changes_the_decision(demo, gj, ij, memory):
    first = check(demo, gj, FakeRealGuard())
    integrations.save_guard_interpretation(demo, gj, ij, memory, check_id=first['id'], actor='rev')
    later = check(demo, gj, guard.DisabledExecutor())
    recall = integrations.recall_for_check(demo, ij, memory, later)
    assert recall['status'] == 'ok' and recall['matches'][0]['status'] == 'authoritative'
    assert recall['matches'][0]['source_check_id'] == first['id']
    assert (later['decision'], later['decision_source'], later['release_eligible']) == ('NEEDS_REVIEW', 'fail_closed', False)
    manual = check(demo, gj, FakeRealGuard(), channel='legal')
    assert manual['decision'] == 'NEEDS_REVIEW' and manual['release_eligible'] is False
    assert integrations.recall_of(demo, ij, later['id'])['id'] == recall['id']


def test_memory_outage_is_visible_and_non_blocking(demo, gj, ij):
    class Broken(integrations.ComintyMemory):
        def list(self):
            raise ConnectionError('down')
    c = check(demo, gj, guard.DisabledExecutor())
    assert integrations.recall_for_check(demo, ij, Broken(), c)['status'] == 'error'
    assert 'unavailable' in integrations.memory_snapshot(demo, ij, Broken())['error']


def test_reviewer_facts_reject_personal_data_and_bind_policy(demo, ij, memory, cominty):
    with pytest.raises(w.WorkflowError, match='personal data'):
        integrations.save_reviewer_fact(demo, ij, memory, statement='Customer jane@example.com bought on 2026-10-01.',
                                        policy_id='shiftly-returns', actor='rev')
    saved = integrations.save_reviewer_fact(demo, ij, memory, statement='Order #1099 was placed before November 4, 2026.',
                                            policy_id='shiftly-returns', actor='rev')
    assert saved['file']['path'].startswith('shiftly-returns-v2/fact-')
    assert integrations.memory_snapshot(demo, ij, memory)['files'][0]['record']['kind'] == 'reviewer_fact'


def test_disabled_memory_refuses_and_reports_not_live(demo, gj, ij):
    c = check(demo, gj, FakeRealGuard())
    with pytest.raises(w.Conflict, match='not enabled'):
        integrations.save_guard_interpretation(demo, gj, ij, integrations.DisabledMemory(), check_id=c['id'], actor='rev')
    assert integrations.memory_snapshot(demo, ij, integrations.DisabledMemory())['live'] is False
    assert integrations.recall_for_check(demo, ij, integrations.DisabledMemory(), c) is None


def test_memory_journal_is_immutable(demo, gj, ij, memory):
    c = check(demo, gj, FakeRealGuard())
    integrations.save_guard_interpretation(demo, gj, ij, memory, check_id=c['id'], actor='rev')
    conn = ij.connect()
    for sql in ('UPDATE memory_writes SET actor="x"', 'DELETE FROM memory_writes'):
        with pytest.raises(sqlite3.DatabaseError, match='immutable'):
            conn.execute(sql)


# ---- Slack notifications ------------------------------------------------------------------

@pytest.fixture
def pending(changed):
    propose(changed)
    return changed


def test_notification_candidates_are_concise_and_contain_no_secrets_or_agent_prose(pending, monkeypatch):
    monkeypatch.setenv('UNDRIFT_REVIEW_BASE_URL', 'http://127.0.0.1:9999')
    items = integrations.candidates(pending)
    assert items and all(i['kind'] == 'proposal_pending' for i in items)
    text = items[0]['text']
    assert 'Policy change: voluntary returns 60 → 30 days for purchases from 2026-11-04' in text
    assert 'Affected channel:' in text and 'Finding:' in text and 'http://127.0.0.1:9999/#/review/prop-' in text
    assert 'not in Slack' in text and '/mcp/' not in text and len(text) <= integrations.MAX_NOTIFICATION


class FakeNotifier:
    kind, paid, live, connection = 'cominty', True, True, 'slack_official'

    def __init__(self, status='delivered'):
        self.status, self.calls = status, []

    def deliver(self, notification, channel):
        self.calls.append((notification, channel))
        return integrations.Delivery(self.status, {'message_id': 'm-9', 'slack': {'message_ts': '1.2', 'channel': channel}},
                                     None if self.status == 'delivered' else 'x')


def test_notification_needs_channel_confirmation_and_is_never_duplicated(pending, ij, monkeypatch):
    demo = pending
    key = integrations.candidates(demo)[0]['key']
    n = FakeNotifier()
    with pytest.raises(w.Conflict, match='not configured'):
        integrations.send_notification(demo, ij, n, key=key, actor='rev', confirm_paid=True)
    monkeypatch.setenv('UNDRIFT_SLACK_CHANNEL', '#undrift-review')
    with pytest.raises(w.Conflict, match='paid'):
        integrations.send_notification(demo, ij, n, key=key, actor='rev')
    with pytest.raises(w.Conflict, match='not configured'):
        integrations.send_notification(demo, ij, integrations.DisabledNotifier(), key=key, actor='rev', confirm_paid=True)
    row = integrations.send_notification(demo, ij, n, key=key, actor='rev', confirm_paid=True)
    assert row['phase'] == 'delivered' and n.calls[0][1] == '#undrift-review'
    with pytest.raises(w.Conflict, match='already delivered'):
        integrations.send_notification(demo, ij, n, key=key, actor='rev', confirm_paid=True)
    assert len(n.calls) == 1
    assert [r['phase'] for r in ij.rows('notifications', demo.identity())] == ['delivered', 'started']
    ev = demo.events(type_prefix='notification.')
    assert ev[-1].metadata['grants_approval'] is False
    assert {p.status for p in demo.proposals()} == {'pending_review'}


def test_unconfirmed_delivery_blocks_retry(pending, ij, monkeypatch):
    monkeypatch.setenv('UNDRIFT_SLACK_CHANNEL', '#undrift-review')
    key = integrations.candidates(pending)[0]['key']
    integrations.send_notification(pending, ij, FakeNotifier('unconfirmed'), key=key, actor='rev', confirm_paid=True)
    with pytest.raises(w.Conflict, match='inspect Slack'):
        integrations.send_notification(pending, ij, FakeNotifier(), key=key, actor='rev', confirm_paid=True)


def test_failed_delivery_allows_a_later_attempt(pending, ij, monkeypatch):
    monkeypatch.setenv('UNDRIFT_SLACK_CHANNEL', '#undrift-review')
    key = integrations.candidates(pending)[0]['key']
    integrations.send_notification(pending, ij, FakeNotifier('failed'), key=key, actor='rev', confirm_paid=True)
    assert integrations.send_notification(pending, ij, FakeNotifier(), key=key, actor='rev', confirm_paid=True)['phase'] == 'delivered'


def sdk_event(kind, data, status='success'):
    from datetime import datetime, timezone
    from cominty_sdk import events
    cls = {'tool_call': events.ToolCall, 'result': events.Result}[kind]
    return cls(id='e', correlation_id=1, at=datetime.now(timezone.utc), status=status, name=kind, data=data)


def fake_notify_client(reply_fn, stream_fn, seen):
    class Run:
        def __init__(self, msg):
            self.thread, self.message_id, self.msg = SimpleNamespace(id='th-1'), 'msg-1', msg
            self.stream = list(stream_fn())
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def __aiter__(self): return self
        async def __anext__(self):
            if self.stream:
                return self.stream.pop(0)
            raise StopAsyncIteration
        async def result(self):
            return SimpleNamespace(status=SimpleNamespace(value='success'), content=reply_fn(json.loads(self.msg.split('```json\n')[1].split('\n```')[0])))

    class Chat:
        async def start(self, **kw):
            seen.update(kw)
            return Run(kw['message'])

    class Client:
        chat = Chat()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    return lambda: Client()


def notify_reply(payload, **override):
    out = {'contract': integrations.NOTIFY_OUTPUT_CONTRACT, 'notification_id': payload['notification_id'],
           'channel': payload['channel'], 'text_sha256': payload['text_sha256'], 'posted': True,
           'message_ts': '1731000000.0001', 'permalink': None, 'error': None, **override}
    return '```json\n' + json.dumps(out) + '\n```'


COST = {'failed': False, 'input_tokens': 10, 'cached_tokens': 0, 'output_tokens': 5, 'input_cost': '0.01', 'output_cost': '0.01', 'total': '0.02'}


@pytest.mark.parametrize('stream, reply, expected', [
    (lambda: [sdk_event('tool_call', {'name': 'slack_send_message', 'description': 'x'}), sdk_event('result', {'reply': '', 'cost': COST})],
     notify_reply, 'delivered'),
    (lambda: [sdk_event('result', {'reply': '', 'cost': COST})], notify_reply, 'unconfirmed'),            # claims posted, no tool call
    (lambda: [sdk_event('tool_call', {'name': 'slack_send_message', 'description': 'x', 'error': 'channel_not_found'}, 'error')],
     lambda p: notify_reply(p, posted=False, message_ts=None), 'unconfirmed'),
    (lambda: [sdk_event('tool_call', {'name': 'slack_send_message', 'description': 'x'})],
     lambda p: notify_reply(p, channel='#general'), 'unconfirmed'),                                       # wrong channel echoed
    (lambda: [sdk_event('tool_call', {'name': 'slack_send_message', 'description': 'x'})],
     lambda p: notify_reply(p, text_sha256='0' * 64), 'unconfirmed'),                                     # altered text
])
def test_cominty_notifier_confirms_only_with_platform_evidence(stream, reply, expected):
    seen = {}
    n = integrations.ComintyNotifier('notify-agent', client_factory=fake_notify_client(reply, stream, seen))
    d = n.deliver({'id': 'ntf-1', 'text': 'hello'}, '#undrift-review')
    assert d.status == expected
    caps = seen['thread_capabilities']
    assert caps.mcp.activation == 'on_request' and caps.mcp.connections == ['slack_official']
    assert all(getattr(caps, k).activation == 'never' for k in ('web', 'indexed_documents', 'skills', 'memory',
                                                                'image_generation', 'video_generation', 'music_generation'))


def test_notifier_requires_live_switch_agent_and_valid_channel(monkeypatch):
    assert integrations.notifier_from_env().kind == 'disabled'
    monkeypatch.setenv('UNDRIFT_NOTIFY_LIVE', '1')
    monkeypatch.setenv('UNDRIFT_NOTIFY_AGENT_ID', 'n')
    monkeypatch.setenv('UNDRIFT_SLACK_CHANNEL', '#bad channel; @everyone')
    assert integrations.notifier_from_env().kind == 'disabled'
    monkeypatch.setenv('UNDRIFT_SLACK_CHANNEL', '#undrift-review')
    assert integrations.notifier_from_env().kind == 'cominty'


# ---- API ----------------------------------------------------------------------------------

def test_api_memory_and_notifications_require_reviewer_and_hide_connector_urls(demo, gj, ij, memory, token):
    client = TestClient(api.build_app(demo, ui_dir=None, guard_journal=gj, guard_executor=FakeRealGuard(),
                                      integration_journal=ij, memory=memory, notifier=integrations.DisabledNotifier()),
                        base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    auth = {'authorization': f'Bearer {token}'}
    first = client.post('/api/guard/checks', json={'candidate': B, 'channel_id': 'help-center', 'confirm_paid': True}, headers=auth)
    cid = first.json()['check']['id']
    assert first.json()['memory_recall']['matches'] == []
    assert client.post('/api/memory/interpretations', json={'check_id': cid}).status_code == 401
    assert client.post('/api/memory/interpretations', json={'check_id': cid, 'x': 1}, headers=auth).status_code == 400
    assert client.post('/api/memory/interpretations', json={'check_id': cid}, headers=auth).status_code == 201
    second = client.post('/api/guard/checks', json={'candidate': B, 'channel_id': 'help-center', 'confirm_paid': True}, headers=auth).json()
    assert second['memory_recall']['matches'][0]['source_check_id'] == cid
    assert client.get(f"/api/guard/checks/{second['check']['id']}").json()['memory_recall']['matches'][0]['status'] == 'authoritative'
    mem = client.get('/api/memory').json()
    assert mem['live'] is True and mem['files'][0]['status'] == 'authoritative'


def test_api_notifications_hide_connector_urls_and_need_configuration(pending, ij, memory, token):
    client = TestClient(api.build_app(pending, ui_dir=None, integration_journal=ij, memory=memory,
                                      notifier=integrations.DisabledNotifier()),
                        base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    auth = {'authorization': f'Bearer {token}'}
    notes = client.get('/api/notifications')
    assert notes.status_code == 200 and 'secret.example' not in notes.text
    body = notes.json()
    assert body['connector']['name'] == 'slack_official' and body['connector']['is_enabled'] is False
    assert body['grants_approval'] is False and body['mode'] == 'disabled'
    key = body['candidates'][0]['key']
    assert client.post('/api/notifications', json={'key': key, 'confirm_paid': True}).status_code == 401
    assert client.post('/api/notifications', json={'key': key, 'confirm_paid': True}, headers=auth).status_code == 409
    assert ij.rows('notifications', pending.identity()) == []


def test_redaction_keeps_policy_dates_but_removes_contact_data():
    assert integrations.redact('Purchases on or after 2026-11-04 get 30 days.') == 'Purchases on or after 2026-11-04 get 30 days.'
    assert integrations.redact('Call +49 170 1234567 or mail a@b.de') == 'Call <number> or mail <email>'
