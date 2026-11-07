"""Report rendering reads preserved scores; it must not mix studies or regrade."""
from copy import deepcopy
import json

import pytest

import reliability
from scripts import render_reliability_report as renderer


def report():
    good = {'slot':1,'root_run_id':'run-good','thread_id':'thread-good','database':'data/reliability-v2/evaluation-01.db',
        'database_id':'identity-good','phase_run_ids':['run-good','run-followup'],'message_ids':['initial','followup'],
        'status':'incomplete','reconciled':True,'message_count':2,'reported_cost':'0.8','duration_s':300,
        'metrics':{'accuracy':1,'outdated_recall':1,'outdated_precision':1,'unknown_recall':1,'native_clarification_recall':1},
        'native_question_count':5,'answered_count':5,'native_question_integrity':True,'protected_automation_safe':True,
        'mcp_calls':{'search_documents':4,'similar_documents':1,'get_document':48},'mcp_error_count':0,
        'proposal_count':6,'search_strategy':{'lead_followed':True},'errors':['manual correction required'],
        'search_queries':['return period','send back','earlier offer',''],'observed_unreconciled_metrics':None}
    failed = deepcopy(good)
    failed.update(slot=2,root_run_id='run-failed',status='failed',reconciled=False,message_count=1,reported_cost='0.4',
        phase_run_ids=['run-failed'],message_ids=['failed-message'],proposal_count=0,answered_count=0,
        mcp_calls={'search_documents':5,'get_document':24},search_strategy={'lead_followed':False},
        strategy_validation={'code':'search_strategy_violation'},errors=['Search order failed'],
        metrics={**good['metrics'],'accuracy':0,'outdated_recall':0,'outdated_precision':None,'unknown_recall':0},
        observed_unreconciled_metrics={'accuracy':19/24,'outdated_recall':1,'outdated_precision':1})
    results=[good,failed]
    return {'series_id':'reliability-v2','state':'completed','results':results,'aggregate':reliability.aggregate(results),
            'protocol_sha256':'frozen','attempted_paid_messages':3,'cumulative_reported_cost':'1.2'}


def test_v2_renderer_keeps_failures_denominators_and_separate_native_evidence(tmp_path,monkeypatch):
    monkeypatch.setattr(renderer,'ROOT',tmp_path)
    folder=tmp_path/'evaluations'; folder.mkdir()
    # An unrelated v1 diagnosis must neither contaminate nor block the v2 report.
    (folder/'reliability-v1-stop-diagnosis.json').write_text('{"canonical_report_sha256":"unrelated"}')
    data=report(); before=deepcopy(data)
    rendered=renderer.render(data)
    assert '# Undrift Reliability Evaluation v2' in rendered
    assert '| accuracy | 0.00% | 50.00% | 100.00% | 2/2 |' in rendered
    assert '| outdated_precision | 100.00% | 100.00% | 100.00% | 1/2 |' in rendered
    assert 'run-failed' in rendered and 'search_strategy_violation' in rendered
    assert 'Unreconciled observations only' in rendered
    assert '5 / 0' in rendered and '5 / 5' in rendered
    assert '| similar_documents | 0 | 0.50 | 1 | 1 |' in rendered
    assert 'evaluations/reliability-v2-results.json' in rendered
    assert 'reliability-v1' not in rendered
    assert data==before


def test_renderer_rejects_unsafe_series_and_diagnosis_mismatch(tmp_path,monkeypatch):
    monkeypatch.setattr(renderer,'ROOT',tmp_path)
    data=report()
    with pytest.raises(ValueError): renderer.render({**data,'series_id':'../private'})
    folder=tmp_path/'evaluations';folder.mkdir()
    (folder/'reliability-v2-stop-diagnosis.json').write_text(json.dumps({'canonical_report_sha256':'wrong'}))
    with pytest.raises(ValueError,match='does not match'): renderer.render(data)
