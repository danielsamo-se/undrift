"""Undrift Guard: fail-closed validation. No paid or remote calls; executors are test doubles."""
import asyncio
import json
import re
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import api
import guard
import server
import workflow as w
from discovery_seed import create_demo

A = 'All customers have 60 days to return their order.'
B = 'Orders purchased on or after November 4, 2026 are eligible for the standard 30-day voluntary return window.'
C = 'Order #1099 qualifies for a 30-day return period.'
D = 'Your previously promised 60-day return window is no longer valid.'
E = 'Your statutory 14-day withdrawal right no longer applies.'


@pytest.fixture
def demo(tmp_path):
    return create_demo(tmp_path / 'guard.db')


@pytest.fixture
def journal(tmp_path):
    return guard.GuardJournal(tmp_path / 'guard-journal.db')


def reply(case, verdict, findings, uncertainty=None, **override):
    out = {'contract': guard.OUTPUT_CONTRACT, 'check_id': case['check_id'],
           'candidate_sha256': case['candidate']['sha256'],
           'policy': {'id': case['policy']['id'], 'version': case['policy']['version']},
           'verdict': verdict, 'findings': findings, 'uncertainty': uncertainty, **override}
    return 'Explanation.\n```json\n' + json.dumps(out) + '\n```'


def claim(text, assessment, refs=('P',), reason='Checked against the verified case.'):
    return {'claim': text, 'assessment': assessment, 'evidence_refs': list(refs), 'reason': reason}


class Scripted:
    """Test-only executor: its results are labelled test_fixture and can never be release-eligible."""
    kind, paid = 'test_fixture', False

    def __init__(self, fn):
        self.fn, self.calls = fn, 0

    def execute(self, case):
        self.calls += 1
        return guard.Execution('success', 'test_fixture', self.fn(case), {'agent_id': 'fixture'})


def run(demo, journal, executor, candidate, channel='support-snippets', **kw):
    return guard.run_check(demo, journal, executor, candidate=candidate, channel_id=channel,
                           requested_by='test-reviewer', **kw)


# ---- fake Cominty SDK for the real executor ---------------------------------------------

class FakeRun:
    def __init__(self, content, status='success', delay=0):
        self.thread, self.message_id = SimpleNamespace(id='thread-1'), 'message-1'
        self.content, self.status, self.delay = content, status, delay

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def __aiter__(self): return self

    async def __anext__(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        raise StopAsyncIteration

    async def result(self):
        return SimpleNamespace(status=SimpleNamespace(value=self.status), content=self.content)


def fake_cominty(respond, *, status='success', delay=0, fail=None, seen=None):
    class Chat:
        async def start(self, **kw):
            if fail:
                raise fail
            if seen is not None:
                seen.update(kw)
            case = json.loads(re.search(r'```json\n(.*)\n```', kw['message'], re.S).group(1))
            return FakeRun(respond(case), status, delay)

    class Client:
        chat = Chat()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    return lambda: Client()


def allow_b(case):
    return reply(case, 'ALLOW', [claim(B, 'SUPPORTED')])


# ---- representative cases ---------------------------------------------------------------

def test_case_a_outdated_global_claim_blocks(demo, journal):
    r = run(demo, journal, Scripted(lambda c: reply(c, 'BLOCK', [claim(A, 'CONTRADICTS_POLICY')])), A)
    assert (r['decision'], r['release_eligible']) == ('BLOCK', False)
    assert r['precheck']['verdict'] == 'BLOCK'


def test_case_b_grounded_claim_allows_only_through_verified_cominty_execution(demo, journal):
    fixture = run(demo, journal, Scripted(allow_b), B)
    assert (fixture['decision'], fixture['decision_source'], fixture['release_eligible']) == ('ALLOW', 'test_fixture', False)
    seen = {}
    real = run(demo, journal, guard.ComintyExecutor('guard-agent', fake_cominty(allow_b, seen=seen)), B, confirm_paid=True)
    assert (real['decision'], real['decision_source'], real['release_eligible']) == ('ALLOW', 'cominty_guard', True)
    assert {k: real['execution'][k] for k in ('agent_id', 'thread_id', 'message_id')} == {
        'agent_id': 'guard-agent', 'thread_id': 'thread-1', 'message_id': 'message-1'}
    caps = seen['thread_capabilities']
    assert all(getattr(caps, name).activation == 'never' for name in
               ('mcp', 'web', 'indexed_documents', 'skills', 'image_generation', 'memory'))


def test_case_c_unknown_order_never_allows_even_if_agent_says_allow(demo, journal):
    r = run(demo, journal, Scripted(lambda c: reply(c, 'ALLOW', [claim(C, 'SUPPORTED')])), C)
    assert (r['decision'], r['decision_source']) == ('NEEDS_REVIEW', 'deterministic_precheck')
    with_fact = run(demo, journal, Scripted(lambda c: reply(c, 'NEEDS_REVIEW', [claim(C, 'UNVERIFIED_SPECIFIC', ['F1'])])),
                    C, facts=['Order #1099 has no verified purchase date.'])
    assert with_fact['decision'] == 'NEEDS_REVIEW' and with_fact['facts'][0]['ref'] == 'F1'


def test_case_d_revoked_commitment_blocks_even_against_agent_allow(demo, journal):
    r = run(demo, journal, Scripted(lambda c: reply(c, 'ALLOW', [claim(D, 'SUPPORTED')])), D,
            evidence_document_ids=['crm-001'])
    assert (r['decision'], r['decision_source']) == ('BLOCK', 'deterministic_precheck')
    assert r['evidence'][0]['document_id'] == 'crm-001'


def test_case_e_statutory_right_blocks(demo, journal):
    r = run(demo, journal, Scripted(lambda c: reply(c, 'BLOCK', [claim(E, 'MISSTATES_STATUTORY')])), E)
    assert r['decision'] == 'BLOCK' and any('Statutory' in x for x in r['reasons'])


def test_unverified_promotion_needs_review(demo, journal):
    text = 'This exceptional extended return applies to your order.'
    r = run(demo, journal, Scripted(lambda c: reply(c, 'ALLOW', [claim(text, 'SUPPORTED')])), text)
    assert r['decision'] == 'NEEDS_REVIEW'


# ---- verification of agent output -------------------------------------------------------

@pytest.mark.parametrize('make, problem', [
    (lambda c: reply(c, 'ALLOW', [claim(B, 'SUPPORTED', ['E9'])]), 'not supplied'),
    (lambda c: reply(c, 'ALLOW', [claim(B, 'SUPPORTED', [])]), 'evidence references'),
    (lambda c: reply(c, 'ALLOW', [claim('Everyone gets 30 days', 'SUPPORTED')]), 'exact quote'),
    (lambda c: reply(c, 'ALLOW', [claim(B, 'SUPPORTED')], candidate_sha256='0' * 64), 'candidate hash'),
    (lambda c: reply(c, 'ALLOW', [claim(B, 'SUPPORTED')], check_id='gchk-other'), 'check_id'),
    (lambda c: reply(c, 'ALLOW', [claim(B, 'SUPPORTED')], policy={'id': 'shiftly-returns', 'version': 1}), 'policy binding'),
    (lambda c: reply(c, 'ALLOW', [claim(B, 'CONTRADICTS_POLICY')]), 'contradictory'),
    (lambda c: reply(c, 'ALLOW', [claim(B, 'SUPPORTED')], uncertainty='Effective date unclear.'), 'uncertainty'),
    (lambda c: reply(c, 'BLOCK', [claim(B, 'SUPPORTED')]), 'BLOCK without'),
    (lambda c: reply(c, 'MAYBE', [claim(B, 'SUPPORTED')]), 'verdict must be'),
    (lambda c: reply(c, 'ALLOW', []), 'non-empty'),
    (lambda c: 'I think this is fine.', 'no JSON'),
    (lambda c: '', 'empty agent reply'),
])
def test_invalid_or_contradictory_agent_output_fails_closed(demo, journal, make, problem):
    r = run(demo, journal, guard.ComintyExecutor('guard-agent', fake_cominty(make)), B, confirm_paid=True)
    assert (r['decision'], r['decision_source'], r['release_eligible']) == ('NEEDS_REVIEW', 'fail_closed', False)
    assert problem in ' '.join(r['verification']['problems'] + r['reasons'])


def test_allow_must_cover_every_candidate_sentence(demo, journal):
    text = B + ' Shipping is free above 50 EUR.'
    r = run(demo, journal, guard.ComintyExecutor('g', fake_cominty(lambda c: reply(c, 'ALLOW', [claim(B, 'SUPPORTED')]))),
            text, confirm_paid=True)
    assert r['decision'] == 'NEEDS_REVIEW' and 'does not cover' in ' '.join(r['verification']['problems'])


def test_stale_policy_or_evidence_during_execution_fails_closed(demo, journal, token):
    def change_policy(case):
        w.change_policy(demo, 'shiftly-returns', token=token, expected_version=case['policy']['version'],
                        voluntary_return_days=21, effective_from=__import__('datetime').date(2027, 1, 1),
                        description='21 days.')
        return allow_b(case)
    r = run(demo, journal, guard.ComintyExecutor('g', fake_cominty(change_policy)), B, confirm_paid=True)
    assert (r['decision'], r['release_eligible']) == ('NEEDS_REVIEW', False)
    assert 'policy changed during verification' in r['verification']['stale']

    def change_doc(case):
        with demo.write() as c:
            c.execute("INSERT INTO document_versions VALUES ('help-003', 2, 'Edited.', '2026-11-07', 'test')")
            c.execute("UPDATE documents SET current_version=2 WHERE id='help-003'")
        return reply(case, 'ALLOW', [claim(B, 'SUPPORTED', ['P', 'E1'])])
    r2 = run(demo, journal, guard.ComintyExecutor('g', fake_cominty(change_doc)), B, confirm_paid=True,
             evidence_document_ids=['help-003'])
    assert r2['decision'] != 'ALLOW' and not r2['release_eligible']
    assert 'help-003) changed during verification' in ' '.join(r2['verification']['stale'])


# ---- execution failures -----------------------------------------------------------------

@pytest.mark.parametrize('kwargs, status', [
    ({'fail': ConnectionError('unreachable')}, 'error'),
    ({'status': 'failed'}, 'error'),
    ({'delay': 2}, 'timeout'),
])
def test_cominty_failures_and_timeouts_fail_closed(demo, journal, kwargs, status):
    ex = guard.ComintyExecutor('g', fake_cominty(allow_b, **kwargs), timeout_s=0.5)
    r = run(demo, journal, ex, B, confirm_paid=True)
    assert (r['execution']['status'], r['decision'], r['decision_source'], r['release_eligible']) == (
        status, 'NEEDS_REVIEW', 'fail_closed', False)


def test_unconfigured_guard_fails_closed_and_paid_execution_needs_confirmation(demo, journal):
    r = run(demo, journal, guard.DisabledExecutor(), B)
    assert (r['decision'], r['execution']['status']) == ('NEEDS_REVIEW', 'not_configured')
    ex = guard.ComintyExecutor('g', fake_cominty(allow_b))
    with pytest.raises(w.Conflict, match='paid'):
        run(demo, journal, ex, B)
    assert len(journal.list(demo.identity())) == 1


def test_executor_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv('UNDRIFT_GUARD_LIVE', raising=False)
    monkeypatch.setenv('UNDRIFT_GUARD_AGENT_ID', 'g')
    assert guard.executor_from_env().kind == 'disabled'
    monkeypatch.setenv('UNDRIFT_GUARD_LIVE', '1')
    assert guard.executor_from_env().kind == 'cominty'


# ---- boundaries, audit and API ----------------------------------------------------------

def test_manual_only_channel_cannot_be_cleared(demo, journal):
    r = run(demo, journal, guard.ComintyExecutor('g', fake_cominty(allow_b)), B, channel='crm', confirm_paid=True)
    assert (r['decision'], r['decision_source'], r['release_eligible']) == ('NEEDS_REVIEW', 'channel_boundary', False)


def test_audit_is_bound_redacted_and_immutable(demo, journal):
    text = B + ' Questions? Write to jane.doe@example.com.'
    r = run(demo, journal, Scripted(lambda c: reply(c, 'NEEDS_REVIEW', [claim(B, 'SUPPORTED')], 'Contact line not policy.')), text)
    assert r['candidate_sha256'] == guard.sha256(text) and 'jane.doe' not in r['candidate_redacted']
    assert r['policy_version'] == 2 and r['input_contract'] == guard.INPUT_CONTRACT
    raw = journal.path.read_bytes().decode('latin-1')
    assert 'jane.doe@example.com' not in raw
    for statement in ('UPDATE guard_checks SET decision="ALLOW"', 'DELETE FROM guard_checks'):
        conn = sqlite3.connect(journal.path)
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute(statement)
        conn.close()


def test_guard_never_sends_publishes_or_applies(demo, journal):
    before = (demo.documents(), demo.proposals(), [r.id for r in demo.runs()])
    for text in (A, B, C, D, E):
        run(demo, journal, guard.ComintyExecutor('g', fake_cominty(allow_b)), text, confirm_paid=True)
    assert (demo.documents(), demo.proposals(), [r.id for r in demo.runs()]) == before
    types = {e.type for e in demo.events(limit=1000)}
    assert 'guard.check_completed' in types
    assert not types & {'proposal.created', 'proposal.approved', 'proposal.applied', 'proposal.auto_applied',
                        'document.version_created'}
    assert all(e.metadata['sends_or_publishes'] is False for e in demo.events(type_prefix='guard.'))


def test_api_requires_reviewer_and_rejects_injected_verdicts(demo, journal, token):
    client = TestClient(api.build_app(demo, ui_dir=None, guard_journal=journal, guard_executor=guard.DisabledExecutor()),
                        base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    body = {'candidate': B, 'channel_id': 'support-snippets'}
    assert client.post('/api/guard/checks', json=body).status_code == 401
    assert client.post('/api/guard/checks', json=body, headers={'authorization': 'Bearer nope'}).status_code == 401
    auth = {'authorization': f'Bearer {token}'}
    assert client.post('/api/guard/checks', json={**body, 'verdict': 'ALLOW'}, headers=auth).status_code == 400
    assert client.post('/api/guard/checks', json={**body, 'evidence_document_ids': ['nope']}, headers=auth).status_code == 404
    assert journal.list(demo.identity()) == []
    r = client.post('/api/guard/checks', json=body, headers=auth)
    assert r.status_code == 201 and r.json()['check']['decision'] == 'NEEDS_REVIEW'
    overview = client.get('/api/guard').json()
    assert overview['executor']['mode'] == 'disabled' and overview['sends_or_publishes'] is False
    assert [c['id'] for c in overview['checks']] == [r.json()['check']['id']]
    assert client.get(f"/api/guard/checks/{r.json()['check']['id']}").status_code == 200
    assert client.get('/api/guard/checks/gchk-missing').status_code == 404


def test_guard_adds_no_mcp_tools():
    async def names():
        from mcp import Client
        async with Client(server.mcp) as c:
            return {t.name for t in (await c.list_tools()).tools}
    assert not {n for n in asyncio.run(names()) if 'guard' in n}


def test_build_case_rejects_malformed_requests(demo):
    for kw in ({'candidate': ''}, {'candidate': 'x' * 2001}, {'channel_id': 'nope'},
               {'evidence_document_ids': ['help-001', 'help-001']}, {'facts': ['']}, {'facts': ['x'] * 11}):
        args = {'candidate': B, 'channel_id': 'support-snippets', 'requested_by': 'r', **kw}
        with pytest.raises(w.WorkflowError):
            guard.build_case(demo, **args)
