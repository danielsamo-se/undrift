"""Operator source switcher and read-only jury archive adapters (no network)."""
import json

import pytest
from starlette.testclient import TestClient

import api
import guard
import integrations
import jury
from discovery_seed import create_demo


def test_sources_lists_only_labelled_loopback_environments(tmp_path, monkeypatch):
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps([
        {'label': 'Operator', 'url': 'http://127.0.0.1:8501', 'kind': 'live', 'description': 'ok'},
        {'label': 'Remote', 'url': 'https://example.com', 'kind': 'live'},
        {'label': 'Secret', 'url': 'http://127.0.0.1:8000/mcp/abc', 'kind': 'live'},
        {'label': 'Bad kind', 'url': 'http://localhost:8502', 'kind': 'production'},
        'garbage',
    ]))
    monkeypatch.setenv('UNDRIFT_SOURCES_FILE', str(path))
    demo = create_demo(tmp_path / 'demo.db')
    client = TestClient(api.build_app(demo, ui_dir=None), base_url='http://127.0.0.1', client=('127.0.0.1', 1))
    body = client.get('/api/sources').json()
    assert body['current']['database_id'] == demo.identity()
    assert body['sources'] == [{'label': 'Operator', 'url': 'http://127.0.0.1:8501', 'kind': 'live', 'description': 'ok'}]
    path.write_text('{not json')
    assert client.get('/api/sources').json()['sources'] == []


def test_jury_archive_journals_are_read_only(tmp_path):
    demo = create_demo(tmp_path / 'demo.db')
    gj = guard.GuardJournal(tmp_path / 'g.db')
    check = guard.run_check(demo, gj, guard.DisabledExecutor(), candidate='All customers have 60 days.',
                            channel_id='support-snippets', requested_by='r')
    ij = integrations.IntegrationJournal(tmp_path / 'i.db'); ij.connect().close()
    before = (tmp_path / 'g.db').read_bytes()
    archive = jury.ArchiveGuardJournal(tmp_path / 'g.db')
    assert archive.get(check['id'], demo.identity())['decision'] == 'BLOCK'
    with pytest.raises(PermissionError):
        archive.record(check)
    with pytest.raises(PermissionError):
        jury.ArchiveIntegrationJournal(tmp_path / 'i.db').insert('memory_recalls', {})
    with pytest.raises(Exception):
        archive.connect().execute('CREATE TABLE x (y)')
    assert (tmp_path / 'g.db').read_bytes() == before


def test_public_memory_projection_withholds_interpretation_text():
    assert 'not shown as an authoritative policy interpretation' in jury.MEMORY_WITHHELD
