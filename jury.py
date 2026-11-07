"""Export genuine read-only API projections for the independently hosted jury replay.

Sources are read using SQLite mode=ro. No migrations, reaping, credentials, raw
databases, raw provider prompts or evaluation answer books enter the deployment.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from datetime import UTC, datetime

from starlette.testclient import TestClient

import api
import config
import guard
import integrations
from store import Store

ROOT = Path(__file__).resolve().parent
PRIVATE_KEY = re.compile(r'(?i)(token|secret|password|authorization|cookie|credential|api_key|mcp_url|record_file)')


class ArchiveStore(Store):
    def connect(self):
        conn = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        return conn

    def write(self, *args, **kwargs):
        raise PermissionError('Jury archive is read-only')

    def init(self, *args, **kwargs):
        raise PermissionError('Jury archives cannot be migrated or seeded')

    def reap_runs(self):
        # Historical status is evidence; public replay never revises it.
        return 0


class PublicationArchive:
    def __init__(self, path, database_id):
        self.path, self.database_id = path, database_id

    def get(self, proposal_id):
        with sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True) as c:
            c.row_factory = sqlite3.Row
            row = c.execute('SELECT plan,state FROM publications WHERE database_id=? AND proposal_id=?',
                            (self.database_id, proposal_id)).fetchone()
            if not row:
                return None
            state, plan = json.loads(row['state']), json.loads(row['plan'])
            events = [dict(r) | {'metadata': json.loads(r['metadata'])} for r in c.execute(
                'SELECT * FROM publication_events WHERE database_id=? AND proposal_id=? ORDER BY seq',
                (self.database_id, proposal_id))]
            return {**state, 'digest': plan['digest'], 'target': plan['target'], 'events': events}


def _ro(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


class ArchiveGuardJournal(guard.GuardJournal):
    """Read-only Guard audit journal: no schema creation, no writes."""
    def connect(self):
        return _ro(self.path)

    def record(self, row):
        raise PermissionError('Jury archive is read-only')


class ArchiveIntegrationJournal(integrations.IntegrationJournal):
    def connect(self):
        return _ro(self.path)

    def insert(self, table, row):
        raise PermissionError('Jury archive is read-only')


MEMORY_WITHHELD = ('The stored interpretation text contains a known historical redaction defect '
                   '("on or after <number>" instead of the effective date). It is not shown as an '
                   'authoritative policy interpretation; only independently verified metadata and provenance are published.')


def memory_evidence(store, journal_path, namespace=integrations.DEFAULT_NAMESPACE):
    """Independent read-back from native Cominty Memory (free API reads), reduced to verified metadata."""
    import asyncio
    from cominty_sdk import AsyncCominty

    async def read():
        async with AsyncCominty() as client:
            files = [await client.memory.get(f.path, namespace=namespace) for f in await client.memory.list(namespace=namespace)]
            servers = await client.mcp.list(enabled=None)
        return files, servers

    files, servers = asyncio.run(read())
    with _ro(journal_path) as c:
        writes = {r['path']: dict(r) for r in c.execute('SELECT * FROM memory_writes WHERE database_id=?', (store.identity(),))}
    out = []
    for f in files:
        record = integrations.parse_memory(f.content) or {}
        digest = hashlib.sha256(f.content.encode('utf-8')).hexdigest()
        local = writes.get(f.path)
        out.append({
            'path': f.path, 'namespace': f.namespace, 'cominty_version': str(f.version), 'created_at': str(f.created_at),
            'updated_at': str(f.updated_at), 'purpose': f.purpose, 'content_sha256': digest, 'content_chars': len(f.content),
            'matches_local_write_record': bool(local and local['content_sha256'] == digest and local['cominty_version'] == str(f.version)),
            'unchanged_since_write': str(f.created_at) == str(f.updated_at),
            'kind': record.get('kind'), 'verdict': record.get('verdict'), 'policy': record.get('policy'),
            'policy_status_at_capture': integrations.classify(store, record or None),
            'source_check_id': (record.get('source') or {}).get('guard_check_id'),
            'cominty_message_id': (record.get('source') or {}).get('message_id'),
            'saved_by': record.get('saved_by'), 'saved_at': record.get('saved_at'),
            'display': 'metadata_only', 'withheld_reason': MEMORY_WITHHELD,
        })
    slack = next((s for s in servers if s.name == integrations.DEFAULT_SLACK_CONNECTION), None)
    memory = {'recorded': True, 'source': 'cominty_memory_readback', 'namespace': namespace,
              'captured_at': datetime.now(UTC).isoformat(), 'files': out,
              'note': 'Captured by an independent read of the native Cominty Memory API at export time.'}
    notifications = {'recorded': True, 'connection': integrations.DEFAULT_SLACK_CONNECTION,
                     'connector': {k: getattr(slack, k, None) for k in ('name', 'title', 'is_enabled', 'requires_oauth_connect',
                                                                       'connection_status')} if slack else None,
                     'agent_configured': False, 'channel': None, 'deliveries': [], 'grants_approval': False,
                     'note': 'Slack delivery is prepared but not connected. No notification has been sent.'}
    return memory, notifications


def sanitize(value):
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items() if not PRIVATE_KEY.search(key)}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return config.redact(value)
    return value


def snapshot(path, *, journal=None, guard_paths=None):
    """Use the exact existing API contract and readmodel, under a no-write store."""
    source = ArchiveStore(path)
    publisher = PublicationArchive(journal, source.identity()) if journal else None
    guard_journal = ArchiveGuardJournal(guard_paths[0]) if guard_paths else None
    integration_journal = ArchiveIntegrationJournal(guard_paths[1]) if guard_paths else None
    app = api.build_app(source, ui_dir=None, publisher=publisher, guard_journal=guard_journal,
                        guard_executor=guard.DisabledExecutor(), integration_journal=integration_journal,
                        memory=integrations.DisabledMemory(), notifier=integrations.DisabledNotifier())
    client = TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    def get(route):
        response = client.get(route)
        if response.status_code != 200:
            raise RuntimeError(f'Historical projection failed: {route} ({response.status_code})')
        return sanitize(response.json())
    payload = {route: get(route) for route in ['/api/health', '/api/documents', '/api/runs', '/api/proposals', '/api/conversations','/api/autonomy']}
    payload['/api/autonomy']['historical']=True
    for policy_id in source.policy_ids():
        for suffix in ['', '/impact']:
            route = f'/api/policies/{policy_id}{suffix}'
            payload[route] = get(route)
    for run in source.runs():
        route = '/api/runs/' + run.id
        payload[route] = get(route)
    for proposal in source.proposals():
        route = '/api/proposals/' + proposal.id
        payload[route] = get(route)
        route += '/publication'
        payload[route] = get(route)
    with source.snapshot() as c:
        rows = [sanitize(dict(row) | {'metadata': json.loads(row['metadata'])}) for row in c.execute('SELECT * FROM events ORDER BY seq')]
    payload['/api/events'] = {'events': rows, 'latest_seq': rows[-1]['seq'] if rows else 0, 'next_after': 0}
    if guard_paths:
        overview = get('/api/guard')
        overview['executor'] = {**overview['executor'], 'mode': 'recorded', 'paid': False}
        payload['/api/guard'] = overview
        for check in overview['checks']:
            payload['/api/guard/checks/' + check['id']] = get('/api/guard/checks/' + check['id'])
        memory, notifications = memory_evidence(source, guard_paths[1])
        payload['/api/memory'], payload['/api/notifications'] = sanitize(memory), sanitize(notifications)
    return payload


def export(output: Path, *, specs=None):
    config.load_env()
    # Separate exporter process only: prevent publishing credential construction.
    os.environ['UNDRIFT_GITHUB_REPOSITORY'] = ''
    os.environ['UNDRIFT_PUBLISH_CREDENTIAL_FILE'] = ''
    os.environ['UNDRIFT_RELIABILITY_REPORT'] = str(ROOT / 'evaluations/reliability-v2-results.json')
    specs = specs or [
        ('discovery', 'Autonomous Discovery', 'First genuine 24-document discovery execution. Original classifications preserved.', ROOT/'data/unified-demo-v1.db', None),
        ('clarification', 'Native clarification & review', 'Genuine questions, local-reviewer factual answers and same-thread continuation.', ROOT/'data/clarification-development-v1.db', None),
        ('publishing', 'GitHub → Vercel → Verified', 'Human-approved PR #1, merged commit and recorded exact production verification.', ROOT/'data/publishing-demo-20261106T103837160600Z.db', ROOT/'data/publishing-demo-20261106T103837160600Z-publication-journal.db'),
        *[(f'evaluation-{n}', f'Reliability v2 · trial {n}', 'Independent frozen evaluation; failures remain failures.', ROOT/f'data/reliability-v2/evaluation-{n:02}.db', None) for n in range(1,6)],
        ('reliability-v1', 'Archived Reliability v1', 'Original failed study; its evidence and scores are unchanged.', ROOT/'data/reliability-v1/evaluation-01.db', None),
        ('autonomy', 'Channel autonomy · operator copy', 'Recorded v6 delegation: Help Center bounded auto, max 1 per run, with audit. Historical proposals stay ineligible.', ROOT/'data/autonomy-operator-v1.db', None),
        ('autonomy-fixture', 'Autonomy · local test fixture', 'Scripted local fixture showing one automatic application with a receipt. Not a Cominty agent run.', ROOT/'data/autonomy-fixture-v1.db', None),
        ('guard', 'Guard & Cominty Memory', 'One real Cominty Guard ALLOW, server-verified; deterministic precheck cases; native Cominty Memory read-back.', ROOT/'data/guard-demo-v1.db', 'guard'),
    ]
    catalog, datasets = [], {}
    for key, name, description, path, journal in specs:
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        guard_paths = (ROOT/'data/guard-demo-journal-v1.db', ROOT/'data/guard-demo-integrations-v1.db') if journal == 'guard' else None
        data = snapshot(path, journal=None if journal == 'guard' else journal, guard_paths=guard_paths)
        assert before == hashlib.sha256(path.read_bytes()).hexdigest(), 'Source archive changed'
        state = ArchiveStore(path)
        meta = {'id': key, 'name': name, 'description': description,
                'database_id': state.identity(), 'source_sha256': before, 'source_name': path.name,
                'run_ids': [r.id for r in state.runs()], 'document_count': len(state.document_ids()),
                'historical': True, 'kind': 'fixture' if key == 'autonomy-fixture' else 'recorded'}
        datasets[key] = {'metadata': meta, 'routes': data}
        catalog.append(meta)
    reports = {}
    for series in ['reliability-v1', 'reliability-v2']:
        path = ROOT/'evaluations'/(series+'-results.json')
        if not path.exists():
            continue
        report = sanitize(json.loads(path.read_text(encoding='utf-8')))
        report.update(series_id=series, active_series_id='reliability-v2',
                      available_series=['reliability-v1','reliability-v2'], archived=series!='reliability-v2')
        reports[series] = report
    result = {'version': 'jury-replay-v2', 'mode': 'historical_readonly',
              'captured_at': datetime.now(UTC).isoformat(), 'default_replay': 'clarification',
              'catalog': catalog, 'datasets': datasets, 'reports': reports}
    raw = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
    if config.redact(raw) != raw or re.search(r'urt_[A-Za-z0-9_-]{40,}', raw):
        raise RuntimeError('Credential isolation check failed')
    result['snapshot_id'] = hashlib.sha256(raw.encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, separators=(',', ':'))
    return {'snapshot_id': result['snapshot_id'], 'replays': len(catalog), 'bytes': output.stat().st_size}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.output), indent=2))
