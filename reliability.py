"""Offline reliability scoring and a credential-free local Review API projection.

This module is never imported by MCP. Primary scores use only reconciled labels;
native question turns and failed/provisional findings are reported separately.
"""
from collections import Counter
from decimal import Decimal
import json
import math
from pathlib import Path
from statistics import mean

import clarification
import config
import workflow

VERSION = 'reliability-v1'
DEFAULT_REPORT = config.ROOT / 'evaluations/reliability-v1-results.json'


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def classifications(predicted, truth, paraphrases, protected):
    outdated = {d for d, c in truth.items() if c == 'OUTDATED'}
    unknown = {d for d, c in truth.items() if c == 'UNKNOWN'}
    detected = {d for d, c in predicted.items() if c == 'OUTDATED'}
    uncertain = {d for d, c in predicted.items() if c == 'UNKNOWN'}
    return {
        'accuracy': ratio(sum(predicted.get(d) == c for d, c in truth.items()), len(truth)),
        'outdated_recall': ratio(len(outdated & detected), len(outdated)),
        'outdated_precision': ratio(len(outdated & detected), len(detected)),
        'false_positives': len(detected - outdated), 'outdated_misses': len(outdated - detected),
        'paraphrase_recall': ratio(sum(predicted.get(d) == truth[d] for d in paraphrases), len(paraphrases)),
        'unknown_recall': ratio(len(unknown & uncertain), len(unknown)),
        'unknown_precision': ratio(len(unknown & uncertain), len(uncertain)),
        'protected_classification_accuracy': ratio(sum(predicted.get(d) == truth[d] for d in protected), len(protected)),
    }


def score(store, root_id, truth, *, paraphrases=('web-003', 'bot-001'), evidence_kind='synthetic_test'):
    """Use one read snapshot; no init/reaping or model/network calls."""
    with store.snapshot() as c:
        conversation = next(v for v in clarification.listing(store, c) if v['id'] == root_id)
        phases = conversation['phases']
        runs = [store.run(p['run_id'], c) for p in phases]
        latest = runs[-1]
        final = store.findings(run_id=latest.id, conn=c)
        strategy_failed = any(r.metadata.get('strategy_validation', {}).get('status') == 'failed' for r in runs)
        reconciled = not strategy_failed and (latest.status == 'completed' or (
            latest.status == 'incomplete' and latest.metadata.get('finalization') == 'per_document_v1'))
        predicted = {f.document_id: f.category for f in final} if reconciled else {}
        observed = {f.document_id: f.category for f in final}
        docs = {d.id: d for d in store.documents(conn=c)}
        if set(truth) != set(docs):
            raise workflow.WorkflowError('evaluation rubric/corpus scope mismatch')
        protected = {d.id for d in docs.values() if d.channel in workflow.MANUAL_ONLY_CHANNELS}
        questions = [q for p in phases for q in p['questions']]
        asked = {q['document_id'] for q in questions}
        expected_unknown = {d for d, category in truth.items() if category == 'UNKNOWN'}
        phase_ids = {p['run_id'] for p in phases}
        all_findings = [f for r in runs for f in store.findings(run_id=r.id, conn=c)]
        proposals = [p for p in store.proposals() if p.run_id in phase_ids]
        events = store.events(limit=100000)
        calls = [e for e in events if e.type == 'mcp.tool_call' and any(
            r.created_at <= e.ts and (r.closed_at is None or e.ts <= r.closed_at) for r in runs)]
        discovery = [e for e in events if e.type == 'agent.discovery' and e.metadata.get('run_id') in phase_ids]
        retrievals = [workflow.retrieval_status(store, r, c) for r in runs]
        retrieved_ids = {d for item in retrievals for d, versions in item['document_versions'].items() if versions}
        costs = [p['cost'] for p in phases]
        cost_complete = all(cost and cost.get('total') is not None and not cost.get('failed') for cost in costs)
        reported_cost = sum((Decimal(str(cost['total'])) for cost in costs), Decimal(0)) if cost_complete else None
        native_integrity, llm_rounds, evidence_rows = True, 0, 0
        for p in phases:
            records = [json.loads(row[0]) for row in c.execute('SELECT payload FROM conversation_evidence WHERE run_id=? AND kind=?', (p['run_id'], 'terminal'))]
            actual = [q for terminal in records for q in terminal.get('questions', [])]
            native_integrity &= actual == [{'prompt': q['prompt'], 'options': q['options']} for q in p['questions']]
            stream = [json.loads(row[0]) for row in c.execute("SELECT payload FROM conversation_evidence WHERE run_id=? AND kind='stream'", (p['run_id'],))]
            llm_rounds += len({e.get('correlation_id') for e in stream if e.get('name') == 'llm'})
            evidence_rows += c.execute('SELECT COUNT(*) FROM conversation_evidence WHERE run_id=?', (p['run_id'],)).fetchone()[0]
        safety = all(f.proposed_content is None for f in all_findings if f.document_id in protected) and not any(
            p.document_id in protected or p.status != 'pending_review' for p in proposals)
        result = {
            'root_run_id': root_id, 'thread_id': conversation['thread_id'], 'status': latest.status,
            'reconciled': reconciled, 'evidence_kind': evidence_kind, 'message_count': len(phases),
            'phase_run_ids': [p['run_id'] for p in phases], 'message_ids': [p['message_id'] for p in phases],
            'metrics': classifications(predicted, truth, paraphrases, protected),
            'observed_unreconciled_metrics': classifications(observed, truth, paraphrases, protected) if not reconciled else None,
            'missing_final_classifications': sorted(set(truth) - set(predicted)),
            'misclassified_document_ids': sorted(d for d, category in truth.items() if predicted.get(d) != category),
            'category_counts': dict(Counter(predicted.values())), 'provisional_category_counts': dict(Counter(observed.values())) if not reconciled else {},
            'native_question_count': len(questions), 'native_question_document_ids': sorted(asked),
            'native_question_integrity': native_integrity, 'answered_count': sum(q['answer'] is not None for q in questions),
            'new_questions_after_continuation': len(phases[-1]['questions']) if len(phases) > 1 else 0,
            'protected_automation_safe': safety,
            'mcp_calls': dict(Counter(e.metadata.get('tool') for e in calls)),
            'mcp_error_count': sum(e.status != 'ok' for e in calls),
            'unattributed_mcp_calls': sum(e.metadata.get('run_id') not in phase_ids for e in calls),
            'unattributed_scoped_mcp_calls': sum(e.metadata.get('run_id') not in phase_ids
                and e.metadata.get('tool') != 'health_check' for e in calls),
            'search_queries': [e.metadata.get('query') for e in discovery if e.metadata.get('operation') == 'search_documents'],
            'search_strategy': latest.metadata.get('search_strategy') or runs[0].metadata.get('search_strategy'),
            'strategy_validation': latest.metadata.get('strategy_validation'),
            'continuation_blocker': conversation['continuation_blocker'],
            'retrieval_document_ids': sorted(retrieved_ids), 'final_retrieval_complete': retrievals[-1]['complete'],
            'reported_cost': str(reported_cost) if reported_cost is not None else None, 'cost_reporting_complete': cost_complete,
            'duration_s': sum(r.metadata.get('duration_s', (r.closed_at-r.created_at).total_seconds() if r.closed_at else 0) for r in runs),
            'reserved_parent_steps': sum(p['max_steps'] for p in phases), 'observed_llm_rounds': llm_rounds,
            'steps_measurement': 'Unique llm stream correlation IDs; not a provider-certified parent-step counter.',
            'proposal_count': len(proposals), 'proposal_document_ids': sorted(p.document_id for p in proposals),
            'proposal_ids': [p.id for p in proposals], 'proposal_statuses': dict(Counter(p.status for p in proposals)),
            'execution_evidence_rows': evidence_rows,
            'errors': [r.error for r in runs if r.error],
        }
        result['metrics'].update(native_clarification_recall=ratio(len(asked & expected_unknown), len(expected_unknown)),
            native_clarification_precision=ratio(len(asked & expected_unknown), len(asked)),
            retrieval_coverage=ratio(len(retrieved_ids), len(truth)))
        return result


def aggregate(results, planned=5):
    """Never drop failures or incomplete results. Undefined precision stays null."""
    fields = sorted({key for r in results for key in r.get('metrics', {})})
    statistics = {}
    for key in fields:
        values = [r['metrics'].get(key) for r in results]
        finite = [v for v in values if type(v) in (int, float) and math.isfinite(v)]
        statistics[key] = {'minimum': min(finite) if finite else None, 'mean': mean(finite) if finite else None,
                           'maximum': max(finite) if finite else None, 'defined_count': len(finite), 'result_count': len(results)}
    for key in ['duration_s','reserved_parent_steps','observed_llm_rounds','native_question_count','mcp_error_count']:
        values = [r[key] for r in results if r.get(key) is not None]
        statistics[key] = {'minimum': min(values) if values else None, 'mean': mean(values) if values else None,
                           'maximum': max(values) if values else None, 'defined_count': len(values), 'result_count': len(results)}
    costs = [Decimal(r['reported_cost']) for r in results if r.get('reported_cost') is not None]
    return {'planned_conversations': planned, 'recorded_conversations': len(results),
            'not_started': planned-len(results), 'reconciled_conversations': sum(r.get('reconciled',False) for r in results),
            'statuses': dict(Counter(r.get('status','not_started') for r in results)), 'statistics': statistics,
            'reported_total_cost': str(sum(costs,Decimal(0))),
            'cost_statistics': {'minimum': str(min(costs)) if costs else None,
                                'mean': str(sum(costs,Decimal(0))/len(costs)) if costs else None,
                                'maximum': str(max(costs)) if costs else None, 'defined_count': len(costs)},
            'message_count': sum(r.get('message_count',0) for r in results)}


def load_report(path=None):
    """The Review API never exposes the private answer/rubric files or raw traces."""
    path = Path(path or DEFAULT_REPORT).resolve()
    if not path.is_relative_to((config.ROOT / 'evaluations').resolve()):
        raise workflow.WorkflowError('reliability reports must be under evaluations/')
    if not path.exists():
        return {'version': VERSION, 'state': 'not_started', 'results': [], 'aggregate': aggregate([])}
    if path.stat().st_size > 2_000_000:
        raise workflow.WorkflowError('evaluation report exceeds size limit')
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('version') != VERSION or not isinstance(report.get('results'), list):
        raise workflow.WorkflowError('invalid evaluation report')
    return report
