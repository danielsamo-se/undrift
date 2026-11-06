"""Local Streamable HTTP finding checks. No Cominty calls or live database access.

Starts a separate loopback server with ephemeral credentials and an isolated database.
Public finding submissions remain disabled, including in the test subprocess.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp import Client
import config
import fixtures
import workflow
from scripts.test_mcp import Results, free_port, prepare_db, structured, wait_ready
from store import Store


async def check_mode(url: str, mode: str, store: Store, results: Results) -> str:
    run, token = workflow.open_run(store, fixtures.POLICY_ID, ['doc-001', 'doc-004'], actor='test:protocol')
    credentials = {'run_id': run.id, 'run_token': token}
    async with Client(url, mode=mode, read_timeout_seconds=30) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        results.record(f'{mode}: discovery', set(tools) == {
            'health_check', 'get_policy', 'get_document', 'record_finding'}, str(sorted(tools)))
        results.record(f'{mode}: annotations', not tools['record_finding'].annotations.read_only_hint
                       and tools['record_finding'].annotations.idempotent_hint, 'finding-only, idempotent')
        await client.call_tool('get_policy', {'policy_id': run.policy_id, **credentials})
        doc = structured(await client.call_tool('get_document', {'document_id': 'doc-001', **credentials}))
        results.record(f'{mode}: authenticated reads',
                       workflow.retrieval_status(store, run)['document_versions']['doc-001'] == [3],
                       'exact served version belongs to this run')
        payload = {**credentials, 'policy_id': run.policy_id, 'policy_version': run.policy_version,
                   'document_id': 'doc-001', 'document_version': doc['version'], 'classification': 'OUTDATED',
                   'evidence': doc['content'], 'reason': 'Compared with the authoritative policy.'}
        first = structured(await client.call_tool('record_finding', payload))
        second = structured(await client.call_tool('record_finding', payload))
        results.record(f'{mode}: idempotent retry', first['status'] == 'recorded'
                       and second['status'] == 'duplicate' and first['finding_id'] == second['finding_id'],
                       'one persisted finding')
        for label, overrides in [('wrong token', {'run_token': 'urt_' + 'z' * 43}),
                                 ('invalid evidence', {'evidence': 'Fabricated.'}),
                                 ('conflicting category', {'classification': 'UNRELATED'}),
                                 ('stale version', {'document_version': 2})]:
            response = await client.call_tool('record_finding', {**payload, **overrides})
            results.record(f'{mode}: reject {label}', response.is_error, 'rejected')
        echoed = await client.call_tool('get_document', {'document_id': token})
        results.record(f'{mode}: redact echoed credential', echoed.is_error
                       and token not in str(echoed), 'unknown-id error contains no credential')
        unknown = structured(await client.call_tool('record_finding', {
            **payload, 'document_id': 'doc-004', 'document_version': None,
            'classification': 'UNKNOWN', 'evidence': '', 'reason': 'Document retrieval failed.'}))
        results.record(f'{mode}: UNKNOWN without version', unknown['document_version'] is None,
                       'honest missing retrieval; no invented version')
        workflow.close_run(store, run.id, status='incomplete', actor='test:protocol')
        closed = await client.call_tool('record_finding', payload)
        results.record(f'{mode}: closed token refused', closed.is_error, 'capability revoked')
        results.record(f'{mode}: no publication', not store.proposals()
                       and store.document('doc-001').version == 3, 'no proposal or document mutation')
    return token


async def run_checks() -> Results:
    test_root = ROOT / '.test-tmp'
    test_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='findings-http-', dir=test_root) as directory:
        root = Path(directory)
        secret, review = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db = root / 'isolated.db'
        prepare_db(db, review)
        store = Store(db)
        port = free_port()
        base = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'UNDRIFT_DB': str(db), 'MCP_PATH_SECRET': secret,
               'MCP_PATH_SECRET_NEXT': '', 'MCP_PUBLIC_HOST': '', 'MCP_PORT': str(port),
               'UNDRIFT_REVIEW_TOKEN': review, 'UNDRIFT_MCP_WRITE_TOOLS': '0',
               'UNDRIFT_MCP_FINDINGS': '1', 'UNDRIFT_MCP_FINDINGS_PUBLIC': '0'}
        results = Results(secret)
        log = root / 'server.log'
        with log.open('wb') as output:
            proc = subprocess.Popen([sys.executable, str(ROOT / 'server.py')], cwd=ROOT,
                env=env, stdout=output, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            tokens = []
            try:
                await wait_ready(base, proc)
                for mode in ('legacy', 'auto'):
                    tokens.append(await check_mode(base + config.mcp_path(secret), mode, store, results))
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        raw = b''.join(p.read_bytes() for p in root.iterdir() if p.is_file())
        results.record('no credentials in logs/database',
                       all(t.encode() not in raw for t in [secret, review, *tokens]),
                       'ephemeral path, reviewer and run credentials absent')
        return results


def main() -> int:
    results = asyncio.run(run_checks())
    path = ROOT / 'runs' / 'test_findings_mcp_local.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'results': results.rows}, indent=2), encoding='utf-8')
    print(f'{len(results.rows) - results.failed}/{len(results.rows)} checks passed')
    return bool(results.failed)


if __name__ == '__main__':
    sys.exit(main())
