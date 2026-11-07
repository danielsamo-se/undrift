"""Native Cominty questions and durable, explicitly authorized continuation phases.

An answer is factual context, never approval to execute, correct or publish. A closed
phase is immutable; continuation mints a new capability and re-retrieves the corpus.
No function in this module makes a network request.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from decimal import Decimal, InvalidOperation

import config
import workflow
from store import now

BINDING = re.compile(r'^\[document:([a-zA-Z0-9_-]+);version:([1-9][0-9]*)\]\s+\S')


def _text(value, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise workflow.WorkflowError('invalid clarification text')
    if config.redact(value) != value:
        raise workflow.WorkflowError('credentials are not allowed in clarification text')
    # Preserve native prompts/options byte-for-byte for remote identity checks.
    return value


def _decimal(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError):
        raise workflow.WorkflowError('invalid reported cost') from None


def create(store, run_id, connection, instructions, *, max_turns=3,
           max_total_steps=80, max_reported_cost='2'):
    with store.write() as c:
        run = workflow._open_run(store, run_id, c)
        steps = run.metadata['max_steps']
        if not 1 <= max_turns <= 5 or not 1 <= steps <= 40 or not steps <= max_total_steps <= 120:
            raise workflow.WorkflowError('invalid conversation execution budget')
        limit = _decimal(max_reported_cost)
        if not limit:
            raise workflow.WorkflowError('reported cost budget must be positive')
        c.execute('INSERT INTO conversations VALUES (?,?,?,?,NULL,?,?,?,?,?)',
                  (run.id, run.agent_id, connection, hashlib.sha256(instructions).hexdigest(),
                   'running', max_turns, max_total_steps, str(limit), now()))
        c.execute('INSERT INTO conversation_phases VALUES (?,?,1,?,NULL,?,NULL,?,NULL)',
                  (run.id, run.id, steps, 'dispatching', now()))


def phase(store, run_id, conn=None):
    with store.read(conn) as c:
        # Reading older archives never implicitly migrates them.
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='conversation_phases'").fetchone():
            return None
        row = c.execute('SELECT * FROM conversation_phases WHERE run_id=?', (run_id,)).fetchone()
        return dict(row) if row else None


def bind(store, run_id, thread_id, message_id):
    with store.write() as c:
        p = phase(store, run_id, c)
        if not p:
            return
        workflow._open_run(store, run_id, c)
        conv = c.execute('SELECT * FROM conversations WHERE id=?', (p['conversation_id'],)).fetchone()
        if conv['thread_id'] not in (None, thread_id) or p['message_id'] not in (None, message_id):
            raise workflow.Conflict('conversation identity changed')
        c.execute('UPDATE conversations SET thread_id=? WHERE id=?', (thread_id, conv['id']))
        c.execute("UPDATE conversation_phases SET message_id=?,state='streaming' WHERE run_id=?",
                  (message_id, run_id))
        store.add_event(c, 'agent.conversation_message', actor='runner', subject=('run', run_id),
                        metadata={'run_id': run_id, 'conversation_id': conv['id'],
                                  'thread_id': thread_id, 'message_id': message_id, 'phase': p['ordinal']})


def evidence(store, run_id, kind, payload, *, run_token=''):
    """Persist redacted stream evidence even when the stream/file sink later fails."""
    raw = config.redact(json.dumps(payload, default=str), run_token)
    with store.write() as c:
        if not phase(store, run_id, c):
            return
        c.execute('INSERT INTO conversation_evidence(run_id,kind,payload,created_at) VALUES (?,?,?,?)',
                  (run_id, kind, raw, now()))


def reported_cost(store, run_id, cost):
    with store.write() as c:
        if phase(store, run_id, c):
            if cost is not None:
                _decimal(cost.get('total'))
            c.execute('UPDATE conversation_phases SET cost=? WHERE run_id=?',
                      (json.dumps(cost) if cost is not None else None, run_id))


def _versions(store, run, c):
    policy = store.policy(run.policy_id, conn=c)
    if policy is None or policy.version != run.policy_version or any(
            (doc := store.document(did, conn=c)) is None or doc.version != version for did, version in run.document_versions.items()):
        raise workflow.Conflict('clarification context is stale; start a new analysis')


def capture(store, run_id, questions, *, actor, metadata):
    """Only a trusted runner supplies actual Message.questions after a successful turn."""
    if not questions or len(questions) > 24:
        raise workflow.WorkflowError('invalid native question count')
    with store.write() as c:
        run = workflow._open_run(store, run_id, c)
        p = phase(store, run_id, c)
        if not p or not p['message_id']:
            raise workflow.Conflict('native questions require a bound conversation message')
        _versions(store, run, c)
        rows = []
        for index, question in enumerate(questions):
            prompt = _text(question.prompt)
            binding = BINDING.match(prompt)
            if not binding:
                raise workflow.WorkflowError('native question lacks document/version binding')
            did, version = binding[1], int(binding[2])
            if did not in run.document_ids or run.document_versions[did] != version:
                raise workflow.Conflict('native question is outside the current run context')
            retrieval = workflow.retrieval_status(store, run, c)
            if run.policy_version not in retrieval['policy_versions'] or version not in retrieval['document_versions'].get(did, []):
                raise workflow.Conflict('native question context was not authenticated and retrieved')
            options = [_text(o, 1000) for o in question.options]
            if len(options) > 8 or len({option.strip() for option in options}) != len(options):
                raise workflow.WorkflowError('invalid native answer options')
            rows.append(('question-' + uuid.uuid4().hex, run_id, index, did, version,
                         run.policy_version, prompt, json.dumps(options), config.redact(store.document(did, version, c).content)))
        if sum(len(row[6]) for row in rows) > 12000:
            raise workflow.WorkflowError('native questions exceed the bounded conversation context')
        # Capture admissible provider evidence independently of execution quality.
        # Never swallow auth, version, storage or programming failures as a strategy failure.
        from discovery import SearchStrategyViolation, validate_search_strategy
        failure = None
        try:
            strategy = validate_search_strategy(store, run, c)
        except SearchStrategyViolation as exc:
            strategy = exc.details['observed']
            failure = {'status': 'failed', 'code': exc.code, 'message': str(exc), 'details': exc.details}
        state = 'failed' if failure else 'awaiting_answers'
        for row in rows:
            c.execute('INSERT INTO clarification_questions(id,run_id,ordinal,document_id,document_version,'
                      'policy_version,prompt,options,evidence) VALUES (?,?,?,?,?,?,?,?,?)', row)
        workflow.close_run(store, run_id, status='failed' if failure else 'incomplete', actor=actor,
                           metadata={**metadata, 'native_questions_captured': True,
                                     'awaiting_clarification': not bool(failure), 'search_strategy': strategy,
                                     'strategy_validation': failure or {'status': 'passed' if strategy.get('enforced') else 'not_enforced'}},
                           error=failure['message'] if failure else 'Native questions require reviewer answers', _conn=c)
        # Immediately revoke the capability; no live heartbeat or token waits on a human.
        c.execute("UPDATE agent_runs SET token_hash='' WHERE id=?", (run_id,))
        c.execute('UPDATE conversation_phases SET state=?,finished_at=? WHERE run_id=?', (state, now(), run_id))
        c.execute('UPDATE conversations SET state=? WHERE id=?', (state, p['conversation_id']))
        store.add_event(c, 'agent.questions_captured', actor=actor, subject=('run', run_id),
                        metadata={'run_id': run_id, 'conversation_id': p['conversation_id'],
                                  'question_ids': [row[0] for row in rows], 'conversation_state': state})
        if failure:
            store.add_event(c, 'agent.search_strategy_rejected', actor=actor, status='error',
                            subject=('run', run_id), metadata={'run_id': run_id, **failure}, error=failure['message'])
        return {'state': state, 'strategy_validation': failure or {'status': 'passed' if strategy.get('enforced') else 'not_enforced'}}


def _continuation_failure(store, phases, c):
    """Independent of the mutable conversation label; no failed phase can be laundered."""
    for p in phases:
        run = store.run(p['run_id'], c)
        if run.metadata.get('strategy_validation', {}).get('status') == 'failed':
            return 'Search strategy failed. Questions are retained as evidence; start a new independent analysis.'
        if run.status in {'failed', 'expired', 'interrupted'}:
            return 'A conversation phase failed or was interrupted; inspect evidence before starting a new analysis.'
    latest = phases[-1]
    run = store.run(latest['run_id'], c)
    if run.status != 'incomplete' or not run.metadata.get('awaiting_clarification'):
        return 'The latest phase is not awaiting native clarification.'
    return None


def listing(store, conn=None):
    with store.read(conn) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='conversations'").fetchone():
            return []
        result = []
        for conv in c.execute('SELECT * FROM conversations ORDER BY created_at'):
            item = dict(conv)
            item['phases'] = []
            for row in c.execute('SELECT * FROM conversation_phases WHERE conversation_id=? ORDER BY ordinal', (conv['id'],)):
                p = dict(row)
                run = store.run(p['run_id'], c)
                # A killed worker cannot leave the product claiming it is running.
                p['state'] = run.status if run.status in {'expired', 'interrupted', 'failed'} else p['state']
                p['cost'] = json.loads(p['cost']) if p['cost'] else None
                p['strategy_validation'] = run.metadata.get('strategy_validation')
                p['questions'] = []
                for q in c.execute('SELECT * FROM clarification_questions WHERE run_id=? ORDER BY ordinal', (p['run_id'],)):
                    question = dict(q)
                    question['options'] = json.loads(q['options'])
                    p['questions'].append(question)
                item['phases'].append(p)
            if item['phases'][-1]['state'] in {'expired', 'interrupted', 'failed'}:
                item['state'] = item['phases'][-1]['state']
            costs = [p['cost'] for p in item['phases']]
            known_cost = all(cost and not cost.get('failed') and cost.get('total') is not None for cost in costs)
            item['reported_total_cost'] = str(sum((_decimal(cost['total']) for cost in costs), Decimal(0))) if known_cost else None
            item['continuation_blocker'] = _continuation_failure(store, item['phases'], c)
            if item['state'] == 'ready':
                if item['continuation_blocker']:
                    pass
                elif len(item['phases']) >= item['max_turns']:
                    item['continuation_blocker'] = 'Conversation turn limit reached.'
                elif sum(p['max_steps'] for p in item['phases']) >= item['max_total_steps']:
                    item['continuation_blocker'] = 'Conversation parent-step allowance exhausted.'
                elif not known_cost:
                    item['continuation_blocker'] = 'Reported cost is unavailable; inspect execution evidence.'
                elif _decimal(item['reported_total_cost']) >= _decimal(item['max_reported_cost']):
                    item['continuation_blocker'] = 'Reported cost budget exhausted.'
                else:
                    try:
                        _versions(store, store.run(conv['id'], c), c)
                    except workflow.Conflict as exc:
                        item['continuation_blocker'] = str(exc)
            result.append(item)
        return result


def answer(store, question_id, *, token, option=None, text=None):
    actor = workflow.authorize(store, token, 'clarification.answer', ('question', question_id))
    with store.write() as c:
        q = c.execute('SELECT * FROM clarification_questions WHERE id=?', (question_id,)).fetchone()
        if not q:
            raise workflow.NotFound('unknown clarification question')
        options = json.loads(q['options'])
        if (option is None) == (text is None):
            raise workflow.WorkflowError('select one option or provide free text')
        if option is not None:
            if type(option) is not int or not 0 <= option < len(options):
                raise workflow.WorkflowError('invalid answer option')
            value = options[option]
        else:
            value = _text(text).strip()
        run = store.run(q['run_id'], c)
        _versions(store, run, c)
        if q['answer'] is not None:
            if q['answer'] == value and q['answered_by'] == actor:
                return {'question_id': question_id, 'status': 'already_answered'}
            raise workflow.Conflict('question already answered; answers are immutable')
        p = phase(store, run.id, c)
        phases = c.execute('SELECT * FROM conversation_phases WHERE conversation_id=? ORDER BY ordinal', (p['conversation_id'],)).fetchall()
        if blocker := _continuation_failure(store, phases, c):
            raise workflow.Conflict(blocker)
        state = c.execute('SELECT state FROM conversations WHERE id=?', (p['conversation_id'],)).fetchone()[0]
        if state != 'awaiting_answers':
            raise workflow.Conflict('conversation is not accepting answers')
        pending = [dict(row) for row in c.execute(
            'SELECT id,document_id,document_version,prompt,answer FROM clarification_questions WHERE run_id=?', (run.id,))]
        for item in pending:
            if item['id'] == question_id:
                item['answer'] = value
        if len(json.dumps(pending, ensure_ascii=False)) > 24000:
            raise workflow.WorkflowError('answers exceed the bounded continuation message; provide a shorter factual answer')
        c.execute('UPDATE clarification_questions SET answer=?,answered_by=?,answered_at=? WHERE id=?',
                  (value, actor, now(), question_id))
        missing = c.execute('SELECT 1 FROM clarification_questions WHERE run_id=? AND answer IS NULL', (run.id,)).fetchone()
        if not missing:
            c.execute("UPDATE conversations SET state='ready' WHERE id=?", (p['conversation_id'],))
        store.add_event(c, 'agent.question_answered', actor=actor, subject=('run', run.id),
                        metadata={'run_id': run.id, 'conversation_id': p['conversation_id'],
                                  'question_id': question_id, 'document_id': q['document_id'],
                                  'answer_sha256': hashlib.sha256(value.encode()).hexdigest()})
        return {'question_id': question_id, 'status': 'answered'}


def prepare_continuation(store, conversation_id, *, agent_id, connection, instructions, max_steps):
    """Trusted runner only, called AFTER explicit paid-turn approval/preflight.

    Reserve under the write lock before dispatch. No automatic retries: a crash or
    ambiguous POST response requires remote inspection, never a second blind send.
    """
    with store.write() as c:
        conv = c.execute('SELECT * FROM conversations WHERE id=?', (conversation_id,)).fetchone()
        if not conv or conv['state'] != 'ready':
            raise workflow.Conflict('conversation is not ready for continuation')
        if (conv['agent_id'], conv['connection'], conv['instructions_sha256']) != (
                agent_id, connection, hashlib.sha256(instructions).hexdigest()):
            raise workflow.Conflict('conversation configuration changed')
        phases = c.execute('SELECT * FROM conversation_phases WHERE conversation_id=? ORDER BY ordinal', (conversation_id,)).fetchall()
        if blocker := _continuation_failure(store, phases, c):
            raise workflow.Conflict(blocker)
        questions = c.execute('SELECT * FROM clarification_questions WHERE run_id=? ORDER BY ordinal', (phases[-1]['run_id'],)).fetchall()
        if not questions or any(q['answer'] is None for q in questions):
            raise workflow.Conflict('native questions require recorded reviewer answers')
        if not 1 <= max_steps <= 40 or len(phases) >= conv['max_turns'] or sum(p['max_steps'] for p in phases) + max_steps > conv['max_total_steps']:
            raise workflow.Conflict('conversation step/turn budget exhausted')
        costs = [json.loads(p['cost']) if p['cost'] else None for p in phases]
        if any(not cost or cost.get('failed') or cost.get('total') is None for cost in costs):
            raise workflow.Conflict('reported execution cost is unavailable; inspect before continuing')
        if sum((_decimal(cost['total']) for cost in costs), Decimal(0)) >= _decimal(conv['max_reported_cost']):
            raise workflow.Conflict('reported cost budget exhausted')
        root = store.run(conversation_id, c)
        _versions(store, root, c)
        # open_run uses its own short transaction; do it on this same connection.
        run, secret = workflow.open_run(store, root.policy_id, root.document_ids,
            actor='runner:continuation', agent_id=agent_id, _conn=c,
            metadata={**{key: root.metadata[key] for key in (
                'discovery', 'discovery_strategy', 'seed_sha256', 'instructions_sha256', 'expected_model') if key in root.metadata},
                      'max_steps': max_steps, 'conversation_id': conversation_id,
                      'parent_run_id': phases[-1]['run_id'], 'phase': len(phases) + 1})
        c.execute('INSERT INTO conversation_phases VALUES (?,?,?,?,NULL,?,NULL,?,NULL)',
                  (run.id, conversation_id, len(phases) + 1, max_steps, 'dispatching', now()))
        c.execute("UPDATE conversations SET state='running' WHERE id=?", (conversation_id,))
        answers = [dict(q) for q in c.execute('SELECT id,document_id,document_version,prompt,answer FROM clarification_questions '
                                             'WHERE run_id=? ORDER BY ordinal', (phases[-1]['run_id'],))]
        return run, secret, conv['thread_id'], answers


def finish(store, run_id, *, failed=False):
    with store.write() as c:
        p = phase(store, run_id, c)
        if not p or p['state'] == 'awaiting_answers':
            return
        run = store.run(run_id, c)
        state = 'failed' if failed else run.status
        c.execute('UPDATE conversation_phases SET state=?,finished_at=? WHERE run_id=?', (state, now(), run_id))
        c.execute('UPDATE conversations SET state=? WHERE id=?', (state, p['conversation_id']))
