import hashlib
import json
import sqlite3

import pytest

from discovery_seed import create_demo
import jury
import readmodel
import workflow


def test_archive_projection_never_migrates_reaps_or_changes_source(tmp_path,monkeypatch):
    source=create_demo(tmp_path/'archive.db')
    run,_=workflow.open_run(source,'shiftly-returns',['doc-001'],actor='test')
    monkeypatch.setenv('UNDRIFT_GITHUB_REPOSITORY','')
    with source.read() as c:c.execute('SELECT 1')
    before=hashlib.sha256(source.path.read_bytes()).hexdigest()
    archive=jury.ArchiveStore(source.path)
    assert archive.reap_runs()==0
    with pytest.raises(PermissionError):archive.init()
    with pytest.raises(PermissionError):archive.write()
    with archive.read() as c:
        with pytest.raises(sqlite3.OperationalError):c.execute('DELETE FROM agent_runs')
    data=jury.snapshot(source.path)
    assert data['/api/health']['database_id']==source.identity()
    assert data['/api/runs']['runs'][0]['id']==run.id
    assert source.run(run.id).status=='open'
    assert hashlib.sha256(source.path.read_bytes()).hexdigest()==before


def test_export_redaction_removes_credentials_but_retains_exact_review_evidence():
    native={'prompt':'[document:help-002;version:1] Which original offer?', 'options':['Unknown'], 'answer':'Unknown'}
    original={'token_hash':'private','metadata':{'authorization':'private','secret_slot':'current'},
              'evidence':'Items can be returned within 60 days.', 'question':native,
              'record_file':'private-path','url':'https://example.test/mcp/'+'a'*40,
              'reason':'urt_'+'b'*43}
    safe=jury.sanitize(original)
    assert 'token_hash' not in safe and safe['metadata']=={} and 'record_file' not in safe
    assert safe['question']==native and safe['evidence']==original['evidence']
    assert 'b'*43 not in json.dumps(safe) and 'a'*40 not in json.dumps(safe)


def test_snapshot_uses_existing_consistency_projection_not_invented_findings(tmp_path,monkeypatch):
    source=create_demo(tmp_path/'archive.db');monkeypatch.setenv('UNDRIFT_GITHUB_REPOSITORY','')
    data=jury.snapshot(source.path)
    assert data['/api/runs']['runs']==[] and data['/api/proposals']['proposals']==[]
    snapshot=data['/api/policies/shiftly-returns/impact']
    assert len(snapshot['documents'])==24 and len(snapshot['channels'])==7
    assert all(d['finding'] is None for d in snapshot['documents'])
    assert snapshot['policy']==readmodel.impact_snapshot(source,'shiftly-returns')['policy']
