"""Offline evaluation adapter for ACTUALLY captured native questions.

This reads a private answer book outside the runtime/agent surfaces. It can queue
answers; it NEVER executes an agent. Continuation needs separately approved CLI.
Answer-book format: {"document-id": {"text": "fact"}} or {"option": "exact label"}.
No category labels or scoring keys are sent to Cominty. No answer book is bundled.
"""
import argparse
import json
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clarification
import config
from store import Store
import workflow


def supply_answers(store, conversation_id, answer_book, *, token):
    conversations = clarification.listing(store)
    conv = next((c for c in conversations if c['id'] == conversation_id), None)
    if not conv or conv['state'] != 'awaiting_answers':
        raise workflow.Conflict('no real pending native questions for this conversation')
    phase = conv['phases'][-1]
    with store.read() as c:
        terminals = [json.loads(r[0]) for r in c.execute(
            "SELECT payload FROM conversation_evidence WHERE run_id=? AND kind='terminal'", (phase['run_id'],))]
    native = [q for terminal in terminals if terminal.get('status') == 'success' for q in terminal.get('questions', [])]
    if not phase['message_id'] or [(q['prompt'], q['options']) for q in phase['questions']] != [
            (q['prompt'], q['options']) for q in native]:
        raise workflow.Conflict('native question evidence is missing or inconsistent')
    planned = []
    for question in phase['questions']:
        if question['answer'] is not None:
            continue
        entry = answer_book.get(question['document_id'])
        if not isinstance(entry, dict) or set(entry) not in ({'text'}, {'option'}):
            raise workflow.WorkflowError('answer book lacks a unique factual answer', document_id=question['document_id'])
        if 'option' in entry:
            if entry['option'] not in question['options']:
                raise workflow.WorkflowError('predefined option was not offered by the agent')
            planned.append((question['id'], {'option': question['options'].index(entry['option'])}))
        else:
            planned.append((question['id'], {'text': entry['text']}))
    # Validate every mapping before submitting; all submissions use reviewer auth.
    return [clarification.answer(store, qid, token=token, **body) for qid, body in planned]


def metrics(store, conversation_id):
    conv = next((c for c in clarification.listing(store) if c['id'] == conversation_id), None)
    if not conv:
        raise workflow.NotFound('unknown conversation')
    questions = [q for p in conv['phases'] for q in p['questions']]
    return {'conversation_id': conv['id'], 'state': conv['state'],
            'native_question_count': len(questions),
            'clarified_document_ids': sorted({q['document_id'] for q in questions}),
            'answered_count': sum(q['answer'] is not None for q in questions),
            'message_ids': [p['message_id'] for p in conv['phases']],
            'phase_run_ids': [p['run_id'] for p in conv['phases']],
            'reported_costs': [p['cost'] for p in conv['phases']]}


def recognition_metrics(store, conversation_id, ground_truth):
    """Score pre-answer questions separately from post-answer conclusions.

    A native question recognizes missing context; it is not a fabricated UNKNOWN
    finding. Never rewrite original accuracy using a post-answer classification.
    """
    report = metrics(store, conversation_id)
    asked = set(report['clarified_document_ids'])
    unknown = {did for did, category in ground_truth.items() if category == 'UNKNOWN'}
    return {**report, 'unknown_question_true_positives': sorted(asked & unknown),
            'unknown_question_misses': sorted(unknown - asked),
            'questions_on_other_categories': sorted(asked - unknown),
            'unknown_question_recall': len(asked & unknown) / len(unknown) if unknown else None,
            'question_precision': len(asked & unknown) / len(asked) if asked else None,
            'post_answer_classifications': {f.document_id: f.category for f in store.findings(run_id=report['phase_run_ids'][-1])}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--conversation', required=True)
    parser.add_argument('--answer-book', type=Path)
    parser.add_argument('--ground-truth', type=Path, help='Private scoring labels; never supplied to the agent')
    parser.add_argument('--submit-answers', action='store_true')
    args = parser.parse_args()
    config.load_env()
    store = Store(args.db)
    # No init/migration/reset: evaluation operates only on an explicitly prepared DB.
    if store.metadata('demo_profile') != 'shiftly-discovery-v1':
        raise SystemExit('Evaluation requires an explicit isolated discovery store')
    if args.submit_answers:
        if not args.answer_book:
            parser.error('--submit-answers requires a private --answer-book')
        supply_answers(store, args.conversation, json.loads(args.answer_book.read_text(encoding='utf-8')),
                       token=os.environ.get('UNDRIFT_REVIEW_TOKEN'))
    report = recognition_metrics(store, args.conversation, json.loads(args.ground_truth.read_text(encoding='utf-8'))) if args.ground_truth else metrics(store, args.conversation)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
