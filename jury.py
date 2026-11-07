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


def sanitize(value):
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items() if not PRIVATE_KEY.search(key)}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return config.redact(value)
    return value


def snapshot(path, *, journal=None):
    """Use the exact existing API contract and readmodel, under a no-write store."""
    source = ArchiveStore(path)
    publisher = PublicationArchive(journal, source.identity()) if journal else None
    app = api.build_app(source, ui_dir=None, publisher=publisher)
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
    ]
    catalog, datasets = [], {}
    for key, name, description, path, journal in specs:
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        data = snapshot(path, journal=journal)
        assert before == hashlib.sha256(path.read_bytes()).hexdigest(), 'Source archive changed'
        state = ArchiveStore(path)
        meta = {'id': key, 'name': name, 'description': description,
                'database_id': state.identity(), 'source_sha256': before, 'source_name': path.name,
                'run_ids': [r.id for r in state.runs()], 'document_count': len(state.document_ids()),
                'historical': True}
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
    result = {'version': 'jury-replay-v1', 'mode': 'historical_readonly',
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
