"""Isolated loopback HTTP discovery checks. No paid calls or live database access."""
import asyncio
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mcp import Client
from discovery_seed import create_demo
from scripts.test_mcp import Results, free_port, wait_ready
import workflow


async def main():
    scratch = ROOT / '.test-tmp'
    scratch.mkdir(exist_ok=True)
    results = Results('unused')
    with tempfile.TemporaryDirectory(prefix='discovery-http-', dir=scratch) as directory:
        folder = Path(directory)
        store = create_demo(folder / 'unified.db')
        secret, review = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        port = free_port()
        env = {**os.environ, 'UNDRIFT_DB': str(store.path), 'MCP_PORT': str(port),
            'MCP_PATH_SECRET': secret, 'MCP_PATH_SECRET_NEXT': '', 'MCP_PUBLIC_HOST': '',
            'UNDRIFT_MCP_DISCOVERY': '1', 'UNDRIFT_MCP_FINDINGS': '1',
            'UNDRIFT_MCP_FINDINGS_PUBLIC': '0', 'UNDRIFT_MCP_WRITE_TOOLS': '0', 'UNDRIFT_REVIEW_TOKEN': review}
        run, token = workflow.open_run(store, 'shiftly-returns', ['doc-001'], actor='test:http', metadata={'discovery': True})
        # Server startup is brief; no artificial historical execution is created.
        with (folder / 'server.log').open('wb') as log:
            process = subprocess.Popen([sys.executable, '-B', str(ROOT / 'server.py')], cwd=ROOT,
                env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                base = f'http://127.0.0.1:{port}'
                await wait_ready(base, process)
                async with Client(base + '/mcp/' + secret, read_timeout_seconds=20) as client:
                    tools = {t.name for t in (await client.list_tools()).tools}
                    results.record('discovery tool boundary', tools == {'health_check', 'get_policy', 'get_document',
                        'list_channels', 'search_documents', 'similar_documents', 'record_finding'}, 'seven tools; no approval/apply/publishing')
                    credentials = {'run_id': run.id, 'run_token': token}
                    health = await client.call_tool('health_check', {})
                    results.record('isolated database identity', health.structured_content['database_id'] == store.identity(), 'same fresh store')
                    denied = await client.call_tool('get_document', {'document_id': 'doc-001'})
                    results.record('unscoped retrieval denied', denied.is_error, 'credentials required')
                    channels = await client.call_tool('list_channels', credentials)
                    results.record('scoped channel counts', channels.structured_content['document_count'] == 1, 'one allowed document')
                    search = await client.call_tool('search_documents', {**credentials, 'query': 'return'})
                    results.record('FTS protocol result', search.structured_content['results'][0]['document_id'] == 'doc-001', 'actual HTTP serialization')
                    similar = await client.call_tool('similar_documents', {**credentials, 'document_id': 'doc-001'})
                    results.record('similarity scope', similar.structured_content['results'] == [], 'no outside-scope matches')
                    wrong = await client.call_tool('search_documents', {**credentials, 'run_token': 'invalid'})
                    results.record('invalid token denied', wrong.is_error, 'no cross-run discovery')
                    await client.call_tool('get_policy', {**credentials, 'policy_id': run.policy_id})
                    await client.call_tool('get_document', {**credentials, 'document_id': 'doc-001'})
                    results.record('authenticated retrieval tracked', workflow.retrieval_status(store, run)['complete'], 'search alone did not authorize evidence')
                    workflow.close_run(store, run.id, status='incomplete', actor='test:http')
                    closed = await client.call_tool('list_channels', credentials)
                    results.record('closed capability revoked', closed.is_error, 'terminal run rejected')
            finally:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
        raw = b''.join(path.read_bytes() for path in folder.iterdir() if path.is_file())
        results.record('credentials absent from persistence', all(value.encode() not in raw for value in [secret, review, token]), 'path/reviewer/run secrets absent')
    print(f'{len(results.rows) - results.failed}/{len(results.rows)} checks passed')
    return int(bool(results.failed))


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
