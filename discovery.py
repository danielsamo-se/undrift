"""Run-scoped lexical discovery. Search snippets never count as authenticated retrieval."""
from collections import Counter
import hashlib
import math
import re

import config
from store import Store
import workflow

WORD = re.compile(r'\w+', re.UNICODE)
STOP = frozenset('a an and are as at be by can for from in is it of on or our the this to within with your'.split())


def tokens(text):
    return [w for w in WORD.findall(text.casefold()) if w not in STOP]


def _scope(store, c, run_id, run_token):
    run = workflow.authorize_run(store, run_id, run_token, c)
    docs = [store.document(did, conn=c) for did in run.document_ids]
    if any(d is None or d.version != run.document_versions.get(d.id) for d in docs):
        raise workflow.Conflict('document versions changed during the run')
    return run, docs


def _event(store, c, run, actor, operation, results, **extra):
    store.add_event(c, 'agent.discovery', actor=actor, subject=('run', run.id), metadata={
        'run_id': run.id, 'operation': operation, 'result_documents': results, **extra})


def list_channels(store: Store, *, run_id, run_token, actor='mcp:discovery'):
    store.reap_runs()
    with store.write() as c:
        run, docs = _scope(store, c, run_id, run_token)
        counts = Counter(d.channel for d in docs)
        channels = [dict(r) | {'document_count': counts[r['id']]} for r in c.execute(
            'SELECT * FROM channels ORDER BY id') if r['id'] in counts]
        _event(store, c, run, actor, 'list_channels', [], document_count=len(docs))
        return {'run_id': run.id, 'channels': channels, 'document_count': len(docs)}


def _limit(limit, offset=0):
    if type(limit) is not int or not 1 <= limit <= 25 or type(offset) is not int or not 0 <= offset <= 10000:
        raise workflow.WorkflowError('limit must be 1-25 and offset must be 0-10000')


def search_documents(store: Store, *, run_id, run_token, query='', channel_id=None,
                     limit=10, offset=0, actor='mcp:discovery'):
    _limit(limit, offset)
    if not isinstance(query, str) or len(query) > 500 or config.redact(query, run_token) != query:
        raise workflow.WorkflowError('search query must be at most 500 characters and contain no credentials')
    terms = WORD.findall(query.casefold())
    if query.strip() and not terms:
        raise workflow.WorkflowError('search query must contain a word or number')
    if len(terms) > 32:
        raise workflow.WorkflowError('search query contains too many terms')
    store.reap_runs()
    with store.write() as c:
        run, docs = _scope(store, c, run_id, run_token)
        if channel_id is not None and channel_id not in {d.channel for d in docs}:
            raise workflow.WorkflowError('channel is outside the run scope')
        allowed = [d.id for d in docs if channel_id is None or d.channel == channel_id]
        placeholders = ','.join('?' for _ in allowed)
        clause = f'document_id IN ({placeholders})'
        params = allowed
        if terms:
            # Literal words joined with OR: no user-controlled FTS operators or SQL.
            clause += ' AND document_search MATCH ?'
            params = [*params, ' OR '.join('"' + w + '"' for w in terms)]
        total = c.execute('SELECT COUNT(*) FROM document_search WHERE ' + clause, params).fetchone()[0]
        rank = 'bm25(document_search)' if terms else '0.0'
        rows = c.execute(f'SELECT document_id, channel_id, version, title, content, {rank} AS rank '
                         'FROM document_search WHERE ' + clause + ' ORDER BY rank, document_id LIMIT ? OFFSET ?',
                         [*params, limit, offset]).fetchall()
        results = [{'document_id': r['document_id'], 'channel': r['channel_id'], 'version': int(r['version']),
                    'title': r['title'], 'snippet': r['content'][:320], 'score': -float(r['rank'])} for r in rows]
        _event(store, c, run, actor, 'search_documents', [r['document_id'] for r in results],
               query_sha256=hashlib.sha256(query.encode()).hexdigest(), channel_id=channel_id,
               limit=limit, offset=offset)
        return {'run_id': run.id, 'total': total, 'offset': offset, 'results': results, 'retrieval_required': True}


def similar_documents(store: Store, *, run_id, run_token, document_id, limit=5, actor='mcp:discovery'):
    _limit(limit)
    store.reap_runs()
    with store.write() as c:
        run, docs = _scope(store, c, run_id, run_token)
        source = next((d for d in docs if d.id == document_id), None)
        if source is None:
            raise workflow.WorkflowError('source document is outside the run scope')
        words = {d.id: Counter(tokens(d.title + ' ' + d.content)) for d in docs}
        frequency = Counter(w for terms in words.values() for w in terms)
        vectors = {did: {w: (1 + math.log(n)) * (math.log((1 + len(docs)) / (1 + frequency[w])) + 1)
                         for w, n in terms.items()} for did, terms in words.items()}
        norms = {did: math.sqrt(sum(x * x for x in v.values())) for did, v in vectors.items()}
        reference = vectors[source.id]
        results = []
        for d in docs:
            if d.id == source.id:
                continue
            denominator = norms[source.id] * norms[d.id]
            score = sum(value * vectors[d.id].get(w, 0) for w, value in reference.items()) / denominator if denominator else 0.0
            if score > 0:
                results.append({'document_id': d.id, 'title': d.title, 'channel': d.channel,
                                'version': d.version, 'score': round(score, 10)})
        results = sorted(results, key=lambda r: (-r['score'], r['document_id']))[:limit]
        _event(store, c, run, actor, 'similar_documents', [r['document_id'] for r in results], source_document=document_id)
        return {'run_id': run.id, 'method': 'tfidf_cosine', 'results': results, 'retrieval_required': True}
