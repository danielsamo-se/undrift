from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import fixtures
import run_impact
import workflow
from conftest import GOOD_FAQ
from test_workflow import reply_with


class FakeRun:
    def __init__(self, reply, status='success', crash=False):
        self.thread = SimpleNamespace(id='external-thread')
        self.message_id = 'external-message'
        self.reply, self.status, self.crash = reply, status, crash

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def __aiter__(self):
        if self.crash:
            raise RuntimeError('simulated stream interruption')
        for event in []:
            yield event

    async def result(self):
        return SimpleNamespace(content=self.reply, status=SimpleNamespace(value=self.status))


def fake_client(store, *, retrieve=True, status='success', crash=False, hang=False, bad=False):
    async def start(**kwargs):
        rid = re.search(r'Run ID: (\S+)', kwargs['message'])[1]
        secret = re.search(r'Run token: (\S+)', kwargs['message'])[1]
        run = store.run(rid)
        caps = kwargs['thread_capabilities']
        assert caps.video_generation.activation == caps.music_generation.activation == 'never'
        if hang:
            await asyncio.sleep(60)
        if retrieve:
            workflow.retrieve_for_run(store, run_id=rid, run_token=secret,
                                      kind='policy', subject_id=run.policy_id, actor='mcp:fake')
            for did in run.document_ids:
                workflow.retrieve_for_run(store, run_id=rid, run_token=secret,
                                          kind='document', subject_id=did, actor='mcp:fake')
        entries = {d: {'category': fixtures.EXPECTED_CLASSIFICATION[d],
                      'document_version': run.document_versions[d],
                      'evidence': fixtures.DOCUMENTS[d].content,
                      'reason': 'Compared with the policy.'} for d in run.document_ids}
        entries['doc-001']['proposed_content'] = GOOD_FAQ
        if bad:
            entries['doc-001']['evidence'] = 'Fabricated evidence.'
        return FakeRun(reply_with(entries), status, crash)
    return SimpleNamespace(chat=SimpleNamespace(start=start, cancel=AsyncMock()))


@pytest.mark.parametrize('retrieve,status,bad,expected', [
    (True, 'success', False, 'completed'),
    (False, 'success', False, 'incomplete'),
    (True, 'failed', False, 'failed'),
    (True, 'success', True, 'incomplete'),
])
def test_runner_finalizes_only_verified_success(changed, tmp_path, monkeypatch,
                                                retrieve, status, bad, expected):
    monkeypatch.setattr(run_impact, 'ROOT', tmp_path)
    client = fake_client(changed, retrieve=retrieve, status=status, bad=bad)
    code = asyncio.run(run_impact.paid_run(client, changed, 'agent-x', 'undrift', 8))
    [run] = changed.runs()
    assert run.status == expected
    assert bool(changed.proposals()) == (expected == 'completed')
    assert code == (0 if expected == 'completed' else 1)
    assert run.thread_id == 'external-thread' and run.message_id == 'external-message'
    [file] = (tmp_path / 'runs').glob('impact_*.json')
    record = json.loads(file.read_text(encoding='utf-8'))
    assert record['run_id'] == run.id and 'urt_' not in file.read_text(encoding='utf-8')


def test_runner_closes_stream_interruption_without_proposals(changed, tmp_path, monkeypatch):
    monkeypatch.setattr(run_impact, 'ROOT', tmp_path)
    with pytest.raises(RuntimeError, match='simulated'):
        asyncio.run(run_impact.paid_run(fake_client(changed, crash=True), changed, 'a', 'undrift', 8))
    assert changed.runs()[0].status == 'failed' and not changed.proposals()


def test_runner_timeout_cancels_tasks_and_closes_run(changed, tmp_path, monkeypatch):
    monkeypatch.setattr(run_impact, 'ROOT', tmp_path)
    monkeypatch.setattr(workflow, 'RUN_TTL', timedelta(milliseconds=10))
    with pytest.raises(TimeoutError):
        asyncio.run(run_impact.paid_run(fake_client(changed, hang=True), changed, 'a', 'undrift', 8))
    assert changed.runs()[0].status == 'expired' and not changed.proposals()


def test_evidence_write_failure_prevents_proposal_creation(changed, tmp_path, monkeypatch):
    # Point ROOT at an existing file, making runs/ impossible to create.
    root = tmp_path / 'file'
    root.write_text('occupied')
    monkeypatch.setattr(run_impact, 'ROOT', root)
    with pytest.raises(OSError):
        asyncio.run(run_impact.paid_run(fake_client(changed), changed, 'a', 'undrift', 8))
    assert changed.runs()[0].status == 'failed' and not changed.proposals()


@pytest.mark.parametrize('same_database,scoped,enabled,ready', [
    (True, True, True, True), (False, True, True, False),
    (True, False, True, False), (True, True, False, False),
])
def test_free_mcp_preflight_checks_database_and_scoped_reads(changed, monkeypatch,
                                                            same_database, scoped, enabled, ready):
    import mcp
    monkeypatch.setenv('MCP_PATH_SECRET', 's' * 43)
    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def call_tool(self, *args):
            return SimpleNamespace(is_error=False, structured_content={
                'database_id': changed.identity() if same_database else 'different'})
        async def list_tools(self):
            names = ['get_policy', 'get_document'] + (['record_finding'] if enabled else [])
            return SimpleNamespace(tools=[SimpleNamespace(name=name, input_schema={
                'properties': {'run_id': {}, 'run_token': {}} if scoped else {}}) for name in names])
    monkeypatch.setattr(mcp, 'Client', FakeClient)
    assert asyncio.run(run_impact.mcp_ready(changed)) is ready


@pytest.mark.parametrize('failure', ['stream', 'bind', 'cancel'])
def test_remote_handle_cleanup_and_redacted_partial_trace(changed, tmp_path, monkeypatch, failure):
    monkeypatch.setattr(run_impact, 'ROOT', tmp_path)
    client = fake_client(changed)
    start = client.chat.start
    secret = None
    async def wrapped_start(**kwargs):
        nonlocal secret
        secret = re.search(r'Run token: (\S+)', kwargs['message'])[1]
        handle = await start(**kwargs)
        class PartialRun(FakeRun):
            async def __aiter__(self):
                yield SimpleNamespace(model_dump_json=lambda: json.dumps({'name': 'tool_call', 'data': {'description': secret}}))
                if failure == 'cancel':
                    raise asyncio.CancelledError()
                raise RuntimeError('stream lost')
        return PartialRun(handle.reply)
    client.chat.start = wrapped_start
    if failure == 'bind':
        monkeypatch.setattr(workflow, 'heartbeat_run', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('bind failed')))
    with pytest.raises((RuntimeError, asyncio.CancelledError)):
        asyncio.run(run_impact.paid_run(client, changed, 'a', 'undrift', 8))
    client.chat.cancel.assert_awaited_once_with('external-message')
    [file] = (tmp_path / 'runs').glob('impact_*.json')
    raw = file.read_text()
    assert secret not in raw and 'urt_' not in raw
    record = json.loads(raw)
    assert record['provenance']['message_id'] == 'external-message'
    assert record['cancellation'] == 'requested'
    if failure != 'bind':
        assert len(record['events']) == 1
    assert not changed.proposals()


def test_completed_worker_wins_simultaneous_heartbeat_failure(changed, monkeypatch):
    async def worker(client, store, agent_id, connection, max_steps, run_id, *rest):
        workflow.close_run(store, run_id, status='completed', actor='test')
        return 0
    async def immediate_sleep(_):
        return None
    async def both_done(tasks, **kwargs):
        await asyncio.gather(*tasks, return_exceptions=True)
        return set(tasks), set()
    monkeypatch.setattr(run_impact, '_paid_run', worker)
    monkeypatch.setattr(asyncio, 'sleep', immediate_sleep)
    monkeypatch.setattr(asyncio, 'wait', both_done)
    assert asyncio.run(run_impact.paid_run(None, changed, 'a', 'undrift', 8)) == 0
    assert changed.runs()[0].status == 'completed'


def test_reconciled_partial_run_is_not_reported_as_a_heartbeat_failure(changed, monkeypatch):
    async def worker(client, store, agent_id, connection, max_steps, run_id, *rest):
        workflow.close_run(store, run_id, status='incomplete', actor='test',
                           metadata={'finalization': 'per_document_v1', 'verified_document_ids': []})
        return 1  # Needs clarification, not a runner crash.
    async def immediate_sleep(_):
        return None
    async def heartbeat_first(tasks, **kwargs):
        await asyncio.gather(*tasks, return_exceptions=True)
        heartbeat = next(task for task in tasks if task.get_coro().__name__ == 'heartbeat')
        return {heartbeat}, set(tasks) - {heartbeat}
    monkeypatch.setattr(run_impact, '_paid_run', worker)
    monkeypatch.setattr(asyncio, 'sleep', immediate_sleep)
    monkeypatch.setattr(asyncio, 'wait', heartbeat_first)
    assert asyncio.run(run_impact.paid_run(None, changed, 'a', 'undrift', 8)) == 1
    assert changed.runs()[0].status == 'incomplete'
