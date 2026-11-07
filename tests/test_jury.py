import hashlib
import json
import sqlite3

import pytest

from discovery_seed import create_demo
import jury
import readmodel
import workflow
from scripts import build_jury_site


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


def test_deployment_manifest_hashes_exact_written_worker_bytes(tmp_path,monkeypatch):
    monkeypatch.setattr(build_jury_site,'ROOT',tmp_path)
    ui=tmp_path/'.test-tmp/ui';ui.mkdir(parents=True);(ui/'index.html').write_text('<h1>Jury</h1>')
    site=tmp_path/'.tools/site';(site/'.openai').mkdir(parents=True)
    (site/'.openai/hosting.json').write_text('{"project_id":"isolated-test"}')
    template=tmp_path/'deployment/jury/worker.mjs';template.parent.mkdir(parents=True)
    template.write_text('export function createWorker(){\nreturn {};\n}\n')
    bundle=tmp_path/'bundle.json';bundle.write_text(json.dumps({'mode':'historical_readonly','snapshot_id':'test',
        'catalog':[{'id':'history','database_id':'original'}]}))
    manifest=build_jury_site.build(ui,bundle,site)
    actual=(site/'dist/server/index.js').read_bytes()
    assert manifest['worker_sha256']==hashlib.sha256(actual).hexdigest()
    assert manifest['module_bytes']==len(actual)


def test_jury_packager_refuses_unreviewed_live_payload(tmp_path,monkeypatch):
    monkeypatch.setattr(build_jury_site,'ROOT',tmp_path)
    ui=tmp_path/'.test-tmp/ui';ui.mkdir(parents=True)
    site=tmp_path/'.tools/site';(site/'.openai').mkdir(parents=True)
    (site/'.openai/hosting.json').write_text('{"project_id":"isolated-test"}')
    bundle=tmp_path/'live.json';bundle.write_text('{"mode":"live","catalog":[{}]}')
    with pytest.raises(ValueError,match='historical replay export'):build_jury_site.build(ui,bundle,site)
