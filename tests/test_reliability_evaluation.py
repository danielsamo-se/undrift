import json
import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import api
import clarification
import config
from discovery_seed import create_demo
import reliability
from scripts import reliability_evaluation as evaluator
import workflow
from test_clarification import begin, pause, retrieve, INSTRUCTIONS
from test_discovery import TRUTH, reply


def test_metrics_are_not_optimistic_for_missing_or_wrong_labels():
    truth={'a':'OUTDATED','b':'UNRELATED','c':'UNKNOWN','d':'VALID_DISTINCT'}
    score=reliability.classifications({'a':'UNRELATED','b':'OUTDATED','c':'UNKNOWN'},truth,['a'],['d'])
    assert score['accuracy']==.25 and score['outdated_recall']==score['outdated_precision']==0
    assert score['false_positives']==1 and score['unknown_recall']==1
    assert score['protected_classification_accuracy']==0
    missing=reliability.classifications({},truth,['a'],['d'])
    assert missing['accuracy']==missing['outdated_recall']==0 and missing['outdated_precision'] is None


def test_aggregate_keeps_failures_in_denominator_and_explains_undefined_precision():
    results=[{'status':'incomplete','reconciled':True,'metrics':{'accuracy':1,'outdated_precision':1},'reported_cost':'.4'},
             {'status':'failed','reconciled':False,'metrics':{'accuracy':0,'outdated_precision':None},'reported_cost':None}]
    score=reliability.aggregate(results)
    assert score['recorded_conversations']==2 and score['not_started']==3
    assert score['statistics']['accuracy']['mean']==.5
    assert score['statistics']['outdated_precision']['defined_count']==1
    assert score['statistics']['outdated_precision']['result_count']==2
    assert score['reported_total_cost']=='0.4' and score['statuses']['failed']==1


@pytest.fixture
def demo(tmp_path): return create_demo(tmp_path/'evaluation.db')


def test_native_question_phase_is_real_evidence_but_not_reconciled_labels(demo):
    run,secret=begin(demo)
    workflow.record_finding(demo,run_id=run.id,run_token=secret,policy_id=run.policy_id,policy_version=2,
        document_id='help-002',document_version=1,classification='UNKNOWN',evidence=demo.document('help-002').content,
        reason='Original offer unavailable.',actor='test')
    pause(demo,run)
    result=reliability.score(demo,run.id,TRUTH)
    assert not result['reconciled'] and result['metrics']['accuracy']==0
    assert result['observed_unreconciled_metrics']['unknown_recall']==.2
    assert result['native_question_count']==1 and result['native_question_integrity']
    assert result['metrics']['native_clarification_recall']==.2
    assert result['evidence_kind']=='synthetic_test'


def test_reconciled_unknown_is_scored_and_protected_outdated_is_not_autoedited(demo):
    run,secret=workflow.open_run(demo,'shiftly-returns',demo.document_ids(),actor='test',agent_id='impact',
        metadata={'discovery':True,'max_steps':40})
    clarification.create(demo,run.id,'connection',INSTRUCTIONS)
    clarification.bind(demo,run.id,'thread','message')
    retrieve(demo,run,secret)
    workflow.ingest_agent_reply(demo,reply(demo,run),actor='test',provenance={},run_id=run.id,execution_status='success')
    clarification.finish(demo,run.id)
    result=reliability.score(demo,run.id,TRUTH)
    assert result['status']=='incomplete' and result['reconciled']
    assert result['metrics']['accuracy']==result['metrics']['unknown_recall']==1
    assert result['protected_automation_safe'] and result['proposal_count']==0
    assert result['final_retrieval_complete'] and len(result['retrieval_document_ids'])==24


def test_lookalike_question_without_terminal_evidence_fails_integrity(demo):
    run,_=begin(demo); pause(demo,run)
    with demo.write() as c: c.execute('DELETE FROM conversation_evidence')
    assert not reliability.score(demo,run.id,TRUTH)['native_question_integrity']


def test_report_api_does_not_expose_answer_book_or_allow_arbitrary_files(store,tmp_path,monkeypatch):
    monkeypatch.setattr(config,'ROOT',tmp_path)
    report=tmp_path/'evaluations/report.json'; report.parent.mkdir()
    report.write_text(json.dumps({'version':'reliability-v1','state':'completed','results':[],'aggregate':reliability.aggregate([])}))
    monkeypatch.setenv('UNDRIFT_RELIABILITY_REPORT',str(report))
    client=TestClient(api.build_app(store,ui_dir=None),base_url='http://127.0.0.1',client=('127.0.0.1',1))
    assert client.get('/api/reliability').json()['state']=='completed'
    assert client.get('/.tools/reliability-v1/answers.v1.json').status_code==404
    with pytest.raises(workflow.WorkflowError): reliability.load_report(tmp_path/'private.json')


def test_frozen_source_or_fixture_change_is_detected(tmp_path,monkeypatch):
    monkeypatch.setattr(evaluator,'ROOT',tmp_path)
    monkeypatch.setattr(evaluator,'PRIVATE',tmp_path/'private')
    monkeypatch.setattr(evaluator,'TRUTH',tmp_path/'truth.json')
    (tmp_path/'private').mkdir(); (tmp_path/'private/answers.v1.json').write_text('facts')
    (tmp_path/'truth.json').write_text('rubric'); (tmp_path/'code.py').write_text('original')
    monkeypatch.setattr(evaluator,'manifest',lambda:{'seed':'frozen'})
    protocol={'rubric_sha256':evaluator.file_hash(tmp_path/'truth.json'),
        'answer_fixture_sha256':evaluator.file_hash(tmp_path/'private/answers.v1.json'),
        'source_sha256':{'code.py':evaluator.file_hash(tmp_path/'code.py')},'corpus_sha256':evaluator.digest({'seed':'frozen'})}
    evaluator.assert_frozen(protocol,{'protected_files':{}})
    (tmp_path/'code.py').write_text('tuned')
    with pytest.raises(AssertionError): evaluator.assert_frozen(protocol,{'protected_files':{}})


def test_executor_refuses_second_invocation_before_any_paid_work(tmp_path,monkeypatch):
    monkeypatch.setattr(evaluator,'PRIVATE',tmp_path)
    monkeypatch.setattr(evaluator,'PLAN',tmp_path/'plan.json')
    monkeypatch.setattr(evaluator,'PROTOCOL',tmp_path/'protocol.json')
    (tmp_path/'plan.json').write_text('{}'); (tmp_path/'protocol.json').write_text('{}')
    (tmp_path/'execution.lock').write_text('already attempted')
    with pytest.raises(FileExistsError):
        asyncio.run(evaluator.execute())


@pytest.mark.parametrize('total,known,attempts', [
    ('5',True,[]), ('5.1',True,[]), ('0',False,[]),
    ('1',True,[{'slot':i,'phase':p} for i in range(1,6) for p in [1,2]]),
    ('1',True,[{'slot':1,'phase':1},{'slot':1,'phase':1}]),
    ('1',True,[{'slot':1,'phase':3}]), ('NaN',True,[]),
])
def test_message_budget_fails_closed_before_next_paid_call(total,known,attempts):
    with pytest.raises(AssertionError): evaluator.paid_gate(Decimal(total),known,attempts)


def test_last_allowed_message_can_start_below_cost_threshold():
    attempts=[{'slot':i,'phase':p} for i in range(1,5) for p in [1,2]]+[{'slot':5,'phase':1}]
    evaluator.paid_gate(Decimal('4.999'),True,attempts)


def test_service_handoff_has_bounded_wait_without_inherited_capture_pipe(tmp_path,monkeypatch):
    monkeypatch.setattr(evaluator,'PRIVATE',tmp_path)
    monkeypatch.setattr(evaluator,'ACTIVE',tmp_path/'active.json')
    import subprocess
    def stalled_launcher(command,**options):
        assert options['stdout'] is not subprocess.PIPE
        assert options['stderr'] is subprocess.STDOUT
        assert options['timeout']==30
        raise subprocess.TimeoutExpired(command,30)
    monkeypatch.setattr(evaluator.subprocess,'run',stalled_launcher)
    with pytest.raises(subprocess.TimeoutExpired): evaluator.switch({'slot':1})


def test_preflight_recovery_refuses_any_paid_attempt(tmp_path,monkeypatch):
    monkeypatch.setattr(evaluator,'PRIVATE',tmp_path)
    monkeypatch.setattr(evaluator,'PLAN',tmp_path/'plan.json')
    monkeypatch.setattr(evaluator,'PROTOCOL',tmp_path/'protocol.json')
    monkeypatch.setattr(evaluator,'REPORT',tmp_path/'report.json')
    (tmp_path/'plan.json').write_text('{}'); (tmp_path/'protocol.json').write_text('{}')
    (tmp_path/'report.json').write_text(json.dumps({'state':'stopped','results':[],'attempted_paid_messages':0}))
    (tmp_path/'attempt-1-1.json').write_text('{}')
    with pytest.raises(AssertionError): asyncio.run(evaluator.recover_preflight())
